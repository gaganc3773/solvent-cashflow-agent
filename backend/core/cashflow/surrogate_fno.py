"""FNO-based operator surrogate — hybrid two-branch architecture.

The problem: map merchant parameters θ to the value function V(bank, pipe, t=0).

Where FNO honestly earns its keep here: the per-weekday intensity λ[Mon..Sun]
is a length-7 sequence — a discrete function on the "day-of-week" domain. An
FNO branch learns Fourier features over that sequence; the rest of the
parameters (log ticket, refund rate, expenses, etc.) go through a plain MLP
branch. The two branches are concatenated and projected to the flattened
V grid.

No external `neuralop` dependency — we implement `SpectralConv1d` in ~30
lines with `torch.fft`. That's the entire non-trivial FNO machinery.

Comparison target: the pure-NumPy MLP in `surrogate.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


# ─── Spectral 1D conv — the FNO core ───────────────────────────────

class SpectralConv1d(nn.Module):
    """Learned complex weights on the low Fourier modes.

    Signal path:
       x(t)  ─FFT→  X(ω)  ─truncate to `modes` low ω, ×R(ω)→  X̃(ω)  ─iFFT→  y(t)

    R(ω) is a learned (in_channels, out_channels, modes) complex tensor.
    Everything above `modes` is zeroed — that's the "operator" prior: assume
    the mapping is low-frequency in the input's structure.
    """
    def __init__(self, in_channels: int, out_channels: int, modes: int):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.modes = modes
        scale = 1.0 / (in_channels * out_channels)
        # Store real + imaginary parts as real tensors (autograd-friendly),
        # combine to complex at forward time.
        self.weight_r = nn.Parameter(scale * torch.randn(in_channels, out_channels, modes))
        self.weight_i = nn.Parameter(scale * torch.randn(in_channels, out_channels, modes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, in_channels, N)
        B, _, N = x.shape
        x_ft = torch.fft.rfft(x, dim=-1)                        # (B, C, N//2+1)
        modes = min(self.modes, x_ft.shape[-1])
        out_ft = torch.zeros(
            B, self.out_channels, x_ft.shape[-1],
            dtype=torch.cfloat, device=x.device,
        )
        w = torch.complex(self.weight_r[..., :modes], self.weight_i[..., :modes])
        out_ft[:, :, :modes] = torch.einsum("bic,ioc->boc", x_ft[:, :, :modes], w)
        return torch.fft.irfft(out_ft, n=N, dim=-1)             # (B, out_channels, N)


class FNOBlock(nn.Module):
    """Spectral + 1×1 conv skip + GELU. Standard FNO block."""
    def __init__(self, channels: int, modes: int):
        super().__init__()
        self.spec = SpectralConv1d(channels, channels, modes)
        self.skip = nn.Conv1d(channels, channels, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.gelu(self.spec(x) + self.skip(x))


# ─── Hybrid surrogate: FNO branch + MLP branch ─────────────────────

class FNOSurrogate(nn.Module):
    """Hybrid two-branch operator learner.

    - **FNO branch**  operates on the length-7 weekday-intensity vector,
      treating it as a discrete function on the weekly domain. Four spectral
      layers with `modes=4` (all frequencies for length-7 signals),
      `width=16` channels.
    - **MLP branch**  takes the remaining scalar merchant parameters (ticket
      lognormal, refund/dispute rates, opening balances, expense magnitudes,
      credit APR, etc.) through two hidden layers.
    - **Fusion + head**  concatenates branch outputs and maps to the flat
      V(bank, pipe, t=0) grid.
    """
    def __init__(self, n_scalars: int, output_dim: int, seq_len: int = 7,
                 fno_width: int = 16, fno_modes: int = 4, fno_depth: int = 4,
                 fno_out: int = 32, scalar_hidden: int = 64, scalar_out: int = 32,
                 head_hidden: int = 128):
        super().__init__()
        self.seq_len = seq_len
        # FNO branch
        self.lift = nn.Conv1d(1, fno_width, kernel_size=1)
        self.fno_blocks = nn.ModuleList([
            FNOBlock(fno_width, fno_modes) for _ in range(fno_depth)
        ])
        self.fno_head = nn.Linear(fno_width, fno_out)
        # Scalar MLP branch
        self.scalar_mlp = nn.Sequential(
            nn.Linear(n_scalars, scalar_hidden), nn.GELU(),
            nn.Linear(scalar_hidden, scalar_out),
        )
        # Fusion + output head
        fused_dim = fno_out + scalar_out
        self.head = nn.Sequential(
            nn.Linear(fused_dim, head_hidden), nn.GELU(),
            nn.Linear(head_hidden, head_hidden), nn.GELU(),
            nn.Linear(head_hidden, output_dim),
        )

    def forward(self, lambda_dow: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        # lambda_dow: (B, seq_len), scalars: (B, n_scalars)
        z = lambda_dow.unsqueeze(1)         # (B, 1, seq_len)
        z = self.lift(z)                    # (B, fno_width, seq_len)
        for block in self.fno_blocks:
            z = block(z)                    # (B, fno_width, seq_len)
        z = z.mean(dim=-1)                  # global avg pool → (B, fno_width)
        z = self.fno_head(z)                # (B, fno_out)
        s = self.scalar_mlp(scalars)        # (B, scalar_out)
        return self.head(torch.cat([z, s], dim=-1))


def count_params(m: nn.Module) -> int:
    return sum(p.numel() for p in m.parameters())


# ─── Training loop ─────────────────────────────────────────────────

@dataclass
class FNOTrainResult:
    train_rel_l2: float
    val_rel_l2: float
    n_params: int
    n_train: int
    n_val: int
    train_time_s: float
    inference_ms_batch1: float
    losses: list[float]


def train_fno(
    lambda_dow_train: np.ndarray,     # (N_train, 7)
    scalars_train: np.ndarray,        # (N_train, n_scalars)
    Y_train: np.ndarray,              # (N_train, output_dim)
    lambda_dow_val: np.ndarray,
    scalars_val: np.ndarray,
    Y_val: np.ndarray,
    epochs: int = 400, lr: float = 3e-3, log_every: int = 50,
    device: str = "cpu",
) -> tuple[FNOSurrogate, FNOTrainResult]:
    """Train the hybrid FNO surrogate. Returns the model + comparison stats."""
    n_train = lambda_dow_train.shape[0]
    n_val = lambda_dow_val.shape[0]
    n_scalars = scalars_train.shape[1]
    output_dim = Y_train.shape[1]

    # Normalise inputs and outputs
    lam_mean, lam_std = lambda_dow_train.mean(0), lambda_dow_train.std(0) + 1e-8
    sc_mean, sc_std   = scalars_train.mean(0),   scalars_train.std(0)   + 1e-8
    y_mean, y_std     = Y_train.mean(0),         Y_train.std(0)         + 1e-8

    def norm(arr, m, s): return (arr - m) / s

    def to_t(arr): return torch.tensor(arr, dtype=torch.float32, device=device)

    lam_tr = to_t(norm(lambda_dow_train, lam_mean, lam_std))
    sc_tr  = to_t(norm(scalars_train, sc_mean, sc_std))
    y_tr   = to_t(norm(Y_train, y_mean, y_std))
    lam_va = to_t(norm(lambda_dow_val, lam_mean, lam_std))
    sc_va  = to_t(norm(scalars_val, sc_mean, sc_std))
    y_va   = to_t(norm(Y_val, y_mean, y_std))

    model = FNOSurrogate(n_scalars=n_scalars, output_dim=output_dim).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    t0 = time.time()
    losses: list[float] = []
    for epoch in range(epochs):
        model.train()
        opt.zero_grad()
        pred = model(lam_tr, sc_tr)
        loss = F.mse_loss(pred, y_tr)
        loss.backward()
        opt.step()
        losses.append(float(loss.item()))
        if epoch % log_every == 0 or epoch == epochs - 1:
            print(f"  [fno] epoch {epoch:4d}  loss = {loss.item():.4e}")

    # Evaluate rel-L² in original (un-normalised) space
    model.eval()
    with torch.no_grad():
        yhat_tr = (model(lam_tr, sc_tr).cpu().numpy() * y_std) + y_mean
        yhat_va = (model(lam_va, sc_va).cpu().numpy() * y_std) + y_mean
    rel_tr = float(np.linalg.norm(yhat_tr - Y_train) / (np.linalg.norm(Y_train) + 1e-8))
    rel_va = float(np.linalg.norm(yhat_va - Y_val) / (np.linalg.norm(Y_val) + 1e-8))

    # Single-sample inference latency (representative for merchant apps)
    lam_one = lam_va[:1]
    sc_one = sc_va[:1]
    # warm
    with torch.no_grad():
        for _ in range(5): _ = model(lam_one, sc_one)
    with torch.no_grad():
        t_inf = time.time()
        n_reps = 200
        for _ in range(n_reps):
            _ = model(lam_one, sc_one)
        inf_ms = (time.time() - t_inf) / n_reps * 1000

    # Save normalisation on the model for downstream inference
    model.lam_mean, model.lam_std = lam_mean, lam_std   # type: ignore
    model.sc_mean, model.sc_std   = sc_mean, sc_std     # type: ignore
    model.y_mean, model.y_std     = y_mean, y_std       # type: ignore

    return model, FNOTrainResult(
        train_rel_l2=rel_tr, val_rel_l2=rel_va,
        n_params=count_params(model),
        n_train=n_train, n_val=n_val,
        train_time_s=time.time() - t0,
        inference_ms_batch1=inf_ms,
        losses=losses,
    )
