"""Head-to-head: pure-NumPy MLP surrogate vs PyTorch FNO surrogate.

Both models learn the operator (merchant parameters → V(bank, pipe, t=0)).
Trained on the same synthetic-merchant data with the same train/val split.

Reports for each: parameter count, rel-L² on train and val, wall-clock
training time, and single-sample inference latency (representative of
production merchant-facing use).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

# backend/ on path
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.cashflow.surrogate import (
    sample_merchant_params, solve_for_sampled_merchant, MLPSurrogate,
)
from core.cashflow.surrogate_fno import train_fno


# ─── Shared data generation ────────────────────────────────────────

def generate_dataset(n_train: int, n_val: int, horizon: int, seed: int = 42):
    rng = np.random.default_rng(seed)
    n = n_train + n_val
    print(f"[data] Sampling + solving VI for {n} merchants...")
    all_dow, all_scalar, all_flat, Y = [], [], [], []
    for i in range(n):
        p = sample_merchant_params(rng)
        _, V, _ = solve_for_sampled_merchant(p, horizon=horizon, seed=i)
        all_dow.append(p.to_dow_vec())
        all_scalar.append(p.to_scalar_vec())
        all_flat.append(p.to_vec())
        Y.append(V[0].flatten().astype(np.float32))
        if (i + 1) % 10 == 0:
            print(f"       {i + 1}/{n} done")
    dow    = np.stack(all_dow)               # (n, 7)
    scalar = np.stack(all_scalar)            # (n, 9)  — for FNO's MLP branch
    flat   = np.stack(all_flat)              # (n, 10) — for MLP baseline
    Y      = np.stack(Y)                     # (n, 726)
    return dow, scalar, flat, Y


# ─── MLP baseline training (uses existing pure-NumPy surrogate) ─────

def train_mlp(flat_train: np.ndarray, Y_train: np.ndarray,
              flat_val: np.ndarray, Y_val: np.ndarray,
              epochs: int = 400, lr: float = 3e-3):
    n_train = flat_train.shape[0]
    n_val = flat_val.shape[0]

    x_mean, x_std = flat_train.mean(0), flat_train.std(0) + 1e-8
    y_mean, y_std = Y_train.mean(0), Y_train.std(0) + 1e-8

    X_tr = (flat_train - x_mean) / x_std
    X_va = (flat_val   - x_mean) / x_std
    Y_tr_n = (Y_train - y_mean) / y_std
    Y_va_n = (Y_val   - y_mean) / y_std

    print(f"[mlp] Training: {n_train} train / {n_val} val, output dim = {Y_train.shape[1]}")
    mlp = MLPSurrogate(input_dim=X_tr.shape[1], output_dim=Y_train.shape[1],
                       hidden=128, seed=42)
    t0 = time.time()
    mlp.train(X_tr, Y_tr_n, epochs=epochs, lr=lr, log_every=100)
    train_time = time.time() - t0

    yhat_tr = (mlp.forward(X_tr) * y_std) + y_mean
    yhat_va = (mlp.forward(X_va) * y_std) + y_mean
    rel_tr = float(np.linalg.norm(yhat_tr - Y_train) / (np.linalg.norm(Y_train) + 1e-8))
    rel_va = float(np.linalg.norm(yhat_va - Y_val)   / (np.linalg.norm(Y_val)   + 1e-8))

    # Warm + time single-sample inference
    x_one = X_va[:1]
    for _ in range(5): _ = mlp.forward(x_one)
    n_reps = 200
    t = time.time()
    for _ in range(n_reps): _ = mlp.forward(x_one)
    inf_ms = (time.time() - t) / n_reps * 1000

    n_params = (mlp.W1.size + mlp.b1.size + mlp.W2.size + mlp.b2.size +
                mlp.W3.size + mlp.b3.size)

    return {
        "name": "MLP (pure NumPy)",
        "n_params": int(n_params),
        "train_rel_l2": rel_tr,
        "val_rel_l2": rel_va,
        "train_time_s": train_time,
        "inference_ms": inf_ms,
    }


# ─── Main ─────────────────────────────────────────────────────────

def main(n_train: int = 40, n_val: int = 10, horizon: int = 30, epochs: int = 400):
    dow, scalar, flat, Y = generate_dataset(n_train, n_val, horizon)
    dow_tr, dow_va = dow[:n_train], dow[n_train:]
    sca_tr, sca_va = scalar[:n_train], scalar[n_train:]
    flat_tr, flat_va = flat[:n_train], flat[n_train:]
    Y_tr, Y_va = Y[:n_train], Y[n_train:]

    print()
    print("=" * 66)
    print("MLP BASELINE")
    print("=" * 66)
    mlp_stats = train_mlp(flat_tr, Y_tr, flat_va, Y_va, epochs=epochs)

    print()
    print("=" * 66)
    print("FNO HYBRID (PyTorch — FNO on weekday-λ, MLP on 9 scalars)")
    print("=" * 66)
    fno_model, fno_res = train_fno(
        dow_tr, sca_tr, Y_tr, dow_va, sca_va, Y_va,
        epochs=epochs, lr=3e-3, log_every=100,
    )
    fno_stats = {
        "name": "FNO hybrid (PyTorch)",
        "n_params": fno_res.n_params,
        "train_rel_l2": fno_res.train_rel_l2,
        "val_rel_l2": fno_res.val_rel_l2,
        "train_time_s": fno_res.train_time_s,
        "inference_ms": fno_res.inference_ms_batch1,
    }

    # ─── Report ─────
    print()
    print("=" * 66)
    print("COMPARISON  (same data, same split, same epochs)")
    print("=" * 66)
    header = f"{'Metric':<30} {'MLP baseline':>18} {'FNO hybrid':>18}"
    print(header)
    print("-" * len(header))
    def row(k, mv, fv):
        print(f"{k:<30} {mv:>18} {fv:>18}")
    row("Model parameters",
        f"{mlp_stats['n_params']:,}",
        f"{fno_stats['n_params']:,}")
    row("Train samples",  str(n_train), str(n_train))
    row("Val samples",    str(n_val),   str(n_val))
    row("Train rel-L²",
        f"{mlp_stats['train_rel_l2']:.4f}",
        f"{fno_stats['train_rel_l2']:.4f}")
    row("Val rel-L² (unseen)",
        f"{mlp_stats['val_rel_l2']:.4f}",
        f"{fno_stats['val_rel_l2']:.4f}")
    row("Training time",
        f"{mlp_stats['train_time_s']:.1f}s",
        f"{fno_stats['train_time_s']:.1f}s")
    row("Inference (single sample)",
        f"{mlp_stats['inference_ms']:.3f}ms",
        f"{fno_stats['inference_ms']:.3f}ms")
    print()
    print("Interpretation:")
    if fno_stats['val_rel_l2'] < mlp_stats['val_rel_l2']:
        pct = (mlp_stats['val_rel_l2'] - fno_stats['val_rel_l2']) / mlp_stats['val_rel_l2'] * 100
        print(f"  FNO's val rel-L² is {pct:.1f}% lower than MLP's — the FNO branch")
        print(f"  captures weekday structure that the MLP's scalar-mean baseline misses.")
    else:
        pct = (fno_stats['val_rel_l2'] - mlp_stats['val_rel_l2']) / mlp_stats['val_rel_l2'] * 100
        print(f"  FNO's val rel-L² is {pct:.1f}% HIGHER than MLP's — at this tiny")
        print(f"  dataset size the FNO's added expressiveness doesn't beat the MLP.")
        print(f"  Expected: at 500+ merchants with richer weekday variation, FNO would win.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=40)
    ap.add_argument("--val", type=int, default=10)
    ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--horizon", type=int, default=30)
    args = ap.parse_args()
    main(n_train=args.train, n_val=args.val, horizon=args.horizon, epochs=args.epochs)
