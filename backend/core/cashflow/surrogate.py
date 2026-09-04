"""Neural surrogate for the cashflow HJB across the merchant-parameter family.

Given a merchant's parameters as a vector, predict the value function V(s, t=0)
without re-solving VI. This is the "operator learning" idea: instead of solving
a fresh PDE per merchant, learn the (params → V) map once and query in ms.

Implementation
--------------
- Sample K synthetic merchants across a parameter grid (intensity, opening
  bank, refund rate, has-vendor-bulk-day, credit_apr, is_fee_bps)
- For each: solve VI, extract V(bank, pipe, t=0) grid as ground truth
- Train a small MLP: params → flattened V grid
- Inference: params → MLP forward pass → reshape → policy lookup

This is a scaled-down demonstration. A full FNO with proper functional inputs
(λ(dow) as a length-7 sequence, expense_calendar as length-31) is the
productionization step. The scaffolding here validates the concept:
per-merchant policy in ms instead of seconds.

Design constraint: fits in <2 min end-to-end on CPU for demo reproducibility.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

import numpy as np

from .optimizer_vi import VIConfig, build_env_from_forecast, solve_vi, enumerate_actions
from .forecast import fit_history, build_schedule, simulate_delta_paths, HistoryFit


# ─── Merchant parameter vector ──────────────────────────────────────

@dataclass
class MerchantParams:
    """Compact param vector for the surrogate.

    `intensity_dow` is a length-7 vector (Mon..Sun payment intensities) —
    the natural functional input for the FNO surrogate. `intensity` is its
    mean, retained for the MLP surrogate's scalar-only baseline.
    """
    intensity: float                  # mean daily payment count (scalar summary)
    log_avg_ticket_paise: float       # log(paise) of average ticket
    refund_rate: float                # 0..1
    dispute_rate: float               # 0..1
    fee_rate: float                   # 0..0.05
    opening_bank_lakhs: float         # ₹ in lakhs
    opening_pipeline_lakhs: float
    monthly_fixed_lakhs: float        # sum of fixed expenses per month
    daily_variable_lakhs: float       # sum of variable expense means per day
    has_vendor_bulk_day: float        # 0 or 1
    intensity_dow: np.ndarray = None  # (7,) per-weekday intensity, optional

    def to_vec(self) -> np.ndarray:
        """Flat 10-dim vector for the MLP baseline (scalar intensity)."""
        return np.array([
            self.intensity, self.log_avg_ticket_paise, self.refund_rate,
            self.dispute_rate, self.fee_rate, self.opening_bank_lakhs,
            self.opening_pipeline_lakhs, self.monthly_fixed_lakhs,
            self.daily_variable_lakhs, self.has_vendor_bulk_day,
        ], dtype=np.float32)

    def to_scalar_vec(self) -> np.ndarray:
        """9-dim scalar vector for the FNO's MLP branch (excludes intensity —
        that lives in the FNO branch as the length-7 sequence)."""
        return np.array([
            self.log_avg_ticket_paise, self.refund_rate, self.dispute_rate,
            self.fee_rate, self.opening_bank_lakhs, self.opening_pipeline_lakhs,
            self.monthly_fixed_lakhs, self.daily_variable_lakhs,
            self.has_vendor_bulk_day,
        ], dtype=np.float32)

    def to_dow_vec(self) -> np.ndarray:
        """Length-7 weekday-intensity sequence for the FNO branch."""
        return self.intensity_dow.astype(np.float32) if self.intensity_dow is not None \
            else np.full(7, self.intensity, dtype=np.float32)

    @staticmethod
    def dim() -> int:
        return 10


def sample_merchant_params(rng: np.random.Generator) -> MerchantParams:
    """Draw one merchant from the training family.

    Intensity is drawn as base × per-weekday multiplier (7-vector). This
    is the operator-style functional input the FNO branch consumes; the
    MLP baseline just gets the mean.
    """
    base = float(rng.uniform(8, 200))
    # Realistic weekly variation: multipliers ~ N(1, 0.15), clipped to [0.5, 1.6]
    mults = np.clip(rng.normal(1.0, 0.15, size=7), 0.5, 1.6)
    intensity_dow = (base * mults).astype(np.float32)
    intensity = float(intensity_dow.mean())
    log_ticket = float(rng.uniform(np.log(300_00), np.log(30_000_00)))
    return MerchantParams(
        intensity=intensity,
        log_avg_ticket_paise=log_ticket,
        refund_rate=float(rng.uniform(0.01, 0.12)),
        dispute_rate=float(rng.uniform(0.001, 0.008)),
        fee_rate=float(rng.uniform(0.008, 0.024)),
        opening_bank_lakhs=float(rng.uniform(0.5, 15)),
        opening_pipeline_lakhs=float(rng.uniform(0.2, 3.0)),
        monthly_fixed_lakhs=float(rng.uniform(2.0, 12.0)),
        daily_variable_lakhs=float(rng.uniform(0.1, 0.8)),
        has_vendor_bulk_day=float(rng.choice([0.0, 1.0])),
        intensity_dow=intensity_dow,
    )


# ─── Build VI ground truth for a sampled merchant ───────────────────

def solve_for_sampled_merchant(p: MerchantParams, horizon: int = 30,
                               seed: int = 0) -> tuple[VIConfig, np.ndarray, np.ndarray]:
    """Build a synthetic HistoryFit + schedule from params; solve VI."""
    rng = np.random.default_rng(seed)

    # Use per-weekday intensity if the merchant profile has one; otherwise
    # fall back to a flat vector at the mean (backward-compatible).
    intensity_by_dow = (p.intensity_dow.astype(float)
                        if p.intensity_dow is not None
                        else np.full(7, p.intensity))
    fit = HistoryFit(
        intensity_by_dow=intensity_by_dow,
        ticket_lognorm=(p.log_avg_ticket_paise, 0.6),
        refund_fraction=p.refund_rate,
        dispute_fraction=p.dispute_rate,
        fee_rate_avg=p.fee_rate,
        tax_rate=0.18,
        settlement_delay_days=2,
    )
    from datetime import date, timedelta
    start = date(2026, 8, 30)
    bp, pp = simulate_delta_paths(
        fit, start, horizon, n_paths=1500,
        daily_variable_expense_paise=int(p.daily_variable_lakhs * 1_00_000 * 100),
        seed=seed,
    )
    # Synthetic fixed-outflow schedule: split monthly_fixed across ~3 canonical dates
    fo = np.zeros(horizon)
    monthly_paise = int(p.monthly_fixed_lakhs * 1_00_000 * 100)
    fo[1] -= int(monthly_paise * 0.55)          # payroll on day 1
    if horizon > 5: fo[5] -= int(monthly_paise * 0.20)     # rent on day 5
    if horizon > 20: fo[20] -= int(monthly_paise * 0.15)   # GST on day 20
    if p.has_vendor_bulk_day > 0.5 and horizon > 15:
        fo[15] -= int(monthly_paise * 0.60)     # inventory bulk

    cfg = VIConfig(horizon_days=horizon)
    env = build_env_from_forecast(cfg, bp, pp, fo)
    V, pi = solve_vi(cfg, env)
    return cfg, V, pi


# ─── MLP surrogate ──────────────────────────────────────────────────

class MLPSurrogate:
    """Tiny 3-layer MLP mapping MerchantParams -> V(bank, pipe, t=0).

    Kept in pure NumPy to avoid a heavy PyTorch dependency for the demo.
    Trained with Adam-like SGD; ~2 min for 50 merchants on CPU.
    """
    def __init__(self, input_dim: int, output_dim: int, hidden: int = 128, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.W1 = rng.standard_normal((input_dim, hidden)).astype(np.float32) * np.sqrt(2 / input_dim)
        self.b1 = np.zeros(hidden, dtype=np.float32)
        self.W2 = rng.standard_normal((hidden, hidden)).astype(np.float32) * np.sqrt(2 / hidden)
        self.b2 = np.zeros(hidden, dtype=np.float32)
        self.W3 = rng.standard_normal((hidden, output_dim)).astype(np.float32) * np.sqrt(2 / hidden)
        self.b3 = np.zeros(output_dim, dtype=np.float32)
        self.output_dim = output_dim

    def forward(self, x: np.ndarray) -> np.ndarray:
        # x: (batch, input_dim)
        h1 = np.maximum(0, x @ self.W1 + self.b1)
        h2 = np.maximum(0, h1 @ self.W2 + self.b2)
        return h2 @ self.W3 + self.b3

    def train(self, X: np.ndarray, Y: np.ndarray, epochs: int = 200, lr: float = 3e-3,
              log_every: int = 20) -> list[float]:
        n = X.shape[0]
        losses = []
        m_W1 = np.zeros_like(self.W1); v_W1 = np.zeros_like(self.W1)
        m_W2 = np.zeros_like(self.W2); v_W2 = np.zeros_like(self.W2)
        m_W3 = np.zeros_like(self.W3); v_W3 = np.zeros_like(self.W3)
        beta1, beta2, eps = 0.9, 0.999, 1e-8

        for epoch in range(epochs):
            # Forward
            h1 = np.maximum(0, X @ self.W1 + self.b1)
            h2 = np.maximum(0, h1 @ self.W2 + self.b2)
            yhat = h2 @ self.W3 + self.b3

            # MSE loss (per-cell, averaged)
            diff = yhat - Y
            loss = float((diff ** 2).mean())
            losses.append(loss)

            # Backprop
            dyhat = 2.0 * diff / (n * self.output_dim)                      # (n, out)
            dW3 = h2.T @ dyhat
            dh2 = dyhat @ self.W3.T
            dh2[h2 <= 0] = 0
            dW2 = h1.T @ dh2
            dh1 = dh2 @ self.W2.T
            dh1[h1 <= 0] = 0
            dW1 = X.T @ dh1

            # Adam
            t = epoch + 1
            for W, dW, m, v in [(self.W1, dW1, m_W1, v_W1),
                                (self.W2, dW2, m_W2, v_W2),
                                (self.W3, dW3, m_W3, v_W3)]:
                m[:] = beta1 * m + (1 - beta1) * dW
                v[:] = beta2 * v + (1 - beta2) * (dW ** 2)
                m_hat = m / (1 - beta1 ** t)
                v_hat = v / (1 - beta2 ** t)
                W -= lr * m_hat / (np.sqrt(v_hat) + eps)

            if epoch % log_every == 0 or epoch == epochs - 1:
                print(f"  epoch {epoch:4d}  loss = {loss:.4e}")

        return losses


# ─── Training + inference pipeline ──────────────────────────────────

@dataclass
class TrainedSurrogate:
    mlp: MLPSurrogate
    cfg: VIConfig
    grid_shape: tuple[int, int]          # (n_bank, n_pipe)
    train_rel_l2: float
    val_rel_l2: float
    n_train: int
    n_val: int
    train_time_s: float


def train_surrogate(n_train: int = 40, n_val: int = 10, horizon: int = 30,
                    seed: int = 42) -> TrainedSurrogate:
    """Sample merchants, solve VI, train MLP to predict V(:, :, 0)."""
    rng = np.random.default_rng(seed)
    t0 = time.time()

    # Generate training data
    print(f"Sampling + solving VI for {n_train + n_val} merchants...")
    Xs: list[np.ndarray] = []
    Ys: list[np.ndarray] = []
    cfg_ref = None
    for i in range(n_train + n_val):
        p = sample_merchant_params(rng)
        cfg, V, pi = solve_for_sampled_merchant(p, horizon=horizon, seed=i)
        if cfg_ref is None:
            cfg_ref = cfg
        # Y = V(bank, pipe, t=0) flattened
        Xs.append(p.to_vec())
        Ys.append(V[0].flatten().astype(np.float32))
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{n_train + n_val} done")

    X = np.stack(Xs)                                       # (N, 10)
    Y = np.stack(Ys)                                       # (N, NB*NP)

    # Normalize
    x_mean, x_std = X.mean(0), X.std(0) + 1e-8
    y_mean, y_std = Y.mean(0), Y.std(0) + 1e-8
    X_norm = (X - x_mean) / x_std
    Y_norm = (Y - y_mean) / y_std

    X_tr, X_val = X_norm[:n_train], X_norm[n_train:]
    Y_tr, Y_val = Y_norm[:n_train], Y_norm[n_train:]

    print(f"Training MLP: {X_tr.shape[0]} train / {X_val.shape[0]} val, output dim = {Y.shape[1]}")
    mlp = MLPSurrogate(input_dim=X.shape[1], output_dim=Y.shape[1], hidden=128, seed=seed)
    mlp.train(X_tr, Y_tr, epochs=400, lr=3e-3, log_every=50)

    # Rel-L2 on val (in normalized space, then unnormalize for reporting)
    yhat_val = mlp.forward(X_val)
    yhat_val_orig = yhat_val * y_std + y_mean
    y_val_orig = Y_val * y_std + y_mean
    rel_l2 = float(np.linalg.norm(yhat_val_orig - y_val_orig) / (np.linalg.norm(y_val_orig) + 1e-8))
    yhat_tr = mlp.forward(X_tr)
    yhat_tr_orig = yhat_tr * y_std + y_mean
    y_tr_orig = Y_tr * y_std + y_mean
    rel_l2_tr = float(np.linalg.norm(yhat_tr_orig - y_tr_orig) / (np.linalg.norm(y_tr_orig) + 1e-8))

    # Store normalization on the surrogate for inference
    mlp.x_mean, mlp.x_std = x_mean, x_std                  # type: ignore
    mlp.y_mean, mlp.y_std = y_mean, y_std                  # type: ignore

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. Train rel-L2 = {rel_l2_tr:.4f}, Val rel-L2 = {rel_l2:.4f}")

    return TrainedSurrogate(
        mlp=mlp,
        cfg=cfg_ref,
        grid_shape=(cfg_ref.n_bank, cfg_ref.n_pipe),
        train_rel_l2=rel_l2_tr,
        val_rel_l2=rel_l2,
        n_train=n_train,
        n_val=n_val,
        train_time_s=elapsed,
    )


def infer_value(surrogate: TrainedSurrogate, params: MerchantParams) -> np.ndarray:
    """Predict V(bank, pipe, t=0) for a merchant. Returns (NB, NP) grid."""
    x = params.to_vec().reshape(1, -1)
    x_norm = (x - surrogate.mlp.x_mean) / surrogate.mlp.x_std       # type: ignore
    y_norm = surrogate.mlp.forward(x_norm)
    y = y_norm * surrogate.mlp.y_std + surrogate.mlp.y_mean         # type: ignore
    return y.reshape(surrogate.grid_shape)
