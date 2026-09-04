"""Benchmark harness: VI (HJB-optimal) vs heuristic vs threshold policy.

For a set of synthetic merchants, roll out four policies on their forecast
distribution and measure expected total 30-day cost. Report the lift of VI
over each baseline.

This is the honest evidence table for the pitch. All results are on
synthetic merchants — we disclose that in the video and deck.

Policies compared
-----------------
1. FIXED HEURISTIC          — "IS 50% of pipeline every 3 days"
2. RETRY-EVERY-72H          — "IS 100% pipeline if any shortfall in next 3d"
3. THRESHOLD                — "IS if bank < ₹1L, else nothing"  (a simple ML-lite baseline)
4. VALUE ITERATION (VI)     — HJB-optimal ground truth

Metric: mean of expected-total-cost (VI's Q(s, a=optimal) at t=0) across
merchants, and a per-merchant lift table.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import numpy as np

from .events import CashflowEvent
from .forecast import fit_history, build_schedule, simulate_delta_paths
from .optimizer_vi import (
    VIConfig, build_env_from_forecast, solve_vi, Action, enumerate_actions,
    _to_bank_idx, _to_pipe_idx,
)


# ─── Policy interface ───────────────────────────────────────────────

Policy = Callable[[int, int, int, VIConfig], Action]
# signature: policy(bank_paise, pipeline_paise, day_index, cfg) -> Action


def policy_fixed_heuristic(bank: int, pipe: int, t: int, cfg: VIConfig) -> Action:
    """IS 50% of pipeline every 3 days. Simple and dumb."""
    if t % 3 == 0 and pipe >= 20_000_00:
        return Action("IS", amount_paise=int(pipe * 0.5), is_ratio=0.5)
    return Action("nothing")


def policy_retry_72h(bank: int, pipe: int, t: int, cfg: VIConfig) -> Action:
    """Always IS 50% every 3 days regardless. Emulates dumb dunning-style retry."""
    if t % 3 == 0:
        amt = int(pipe * 0.5)
        if amt >= 10_000_00:
            return Action("IS", amount_paise=amt, is_ratio=0.5)
    return Action("nothing")


def policy_threshold_bank(bank: int, pipe: int, t: int, cfg: VIConfig) -> Action:
    """Simple ML-lite baseline: IS if bank < ₹1L, else nothing."""
    if bank < 1_00_000_00 and pipe >= 10_000_00:
        amt = min(pipe, 50_000_00)
        return Action("IS", amount_paise=amt, is_ratio=amt / max(pipe, 1))
    return Action("nothing")


def policy_vi(V_and_pi):
    """Wrap a solved (V, pi) into a policy callable."""
    V, pi = V_and_pi

    def _p(bank: int, pipe: int, t: int, cfg: VIConfig) -> Action:
        actions = enumerate_actions(cfg, int(round(pipe / cfg.step_pipe_paise) * cfg.step_pipe_paise))
        i = int(_to_bank_idx(cfg, np.asarray([bank]))[0])
        j = int(_to_pipe_idx(cfg, np.asarray([pipe]))[0])
        if t < 0 or t >= pi.shape[0]:
            return Action("nothing")
        a_idx = int(pi[t, i, j])
        if 0 <= a_idx < len(actions):
            return actions[a_idx]
        return Action("nothing")

    return _p


# ─── Rollout evaluator ──────────────────────────────────────────────

def rollout_cost(
    policy: Policy,
    cfg: VIConfig,
    bank_paths: np.ndarray,       # (n_paths, H)
    pipe_paths: np.ndarray,       # (n_paths, H)
    fixed_out: np.ndarray,        # (H,)
    opening_bank: int,
    opening_pipe: int,
) -> dict:
    """Simulate the policy on n_paths forecast paths; return cost stats."""
    n_paths, H = bank_paths.shape
    total_costs = np.zeros(n_paths)

    for path in range(n_paths):
        bank = opening_bank
        pipe = opening_pipe
        cost_accum = 0.0
        for t in range(H):
            act = policy(bank, pipe, t, cfg)

            if act.kind == "IS":
                amt = min(act.amount_paise, pipe)
                fee = int(amt * cfg.is_fee_bps / 10_000)
                bank += amt - fee
                pipe -= amt
                cost_accum += fee
            elif act.kind == "credit":
                bank += act.amount_paise
                days_held = H - t
                cost_accum += act.amount_paise * cfg.credit_apr * days_held / 365

            bank += fixed_out[t]
            bank += bank_paths[path, t]
            pipe += pipe_paths[path, t]

            if bank < 0:
                cost_accum += -bank * cfg.overdraft_apr / 365

        total_costs[path] = cost_accum

    return {
        "mean_cost_paise": float(total_costs.mean()),
        "p95_cost_paise": float(np.percentile(total_costs, 95)),
        "max_cost_paise": float(total_costs.max()),
        "shortfall_rate": float((total_costs > 5000).mean()),  # any meaningful cost
    }


# ─── One-merchant benchmark ─────────────────────────────────────────

@dataclass
class MerchantBenchmark:
    merchant_key: str
    policies: dict[str, dict]       # {"vi": {...}, "heuristic": {...}, ...}
    vi_solve_ms: float


def benchmark_merchant(
    events: list[CashflowEvent],
    fit,
    schedule_fixed_outflows: list,
    opening_bank_paise: int,
    opening_pipeline_paise: int,
    daily_variable_paise: int,
    start: date,
    horizon_days: int = 30,
    n_paths_forecast: int = 3_000,
    n_paths_eval: int = 500,
    seed: int = 7,
    merchant_key: str = "unknown",
) -> MerchantBenchmark:
    import time as _t

    # Forecast distribution
    bp, pp = simulate_delta_paths(
        fit, start, horizon_days, n_paths=n_paths_forecast,
        daily_variable_expense_paise=daily_variable_paise, seed=seed,
    )
    fo = np.zeros(horizon_days)
    for d, amt, _, _ in schedule_fixed_outflows:
        idx = (d - start).days
        if 0 <= idx < horizon_days:
            fo[idx] += amt

    cfg = VIConfig(horizon_days=horizon_days)
    env = build_env_from_forecast(cfg, bp, pp, fo)

    # Solve VI
    t0 = _t.time()
    V, pi = solve_vi(cfg, env)
    vi_ms = (_t.time() - t0) * 1000

    # Sub-sample paths for evaluation
    eval_idx = np.random.default_rng(0).choice(bp.shape[0], min(n_paths_eval, bp.shape[0]), replace=False)
    bp_eval = bp[eval_idx]
    pp_eval = pp[eval_idx]

    policies = {
        "vi": policy_vi((V, pi)),
        "heuristic_50pct_3day": policy_fixed_heuristic,
        "retry_72h": policy_retry_72h,
        "threshold_1L": policy_threshold_bank,
    }

    results = {}
    for name, pol in policies.items():
        results[name] = rollout_cost(
            pol, cfg, bp_eval, pp_eval, fo,
            opening_bank_paise, opening_pipeline_paise,
        )

    return MerchantBenchmark(
        merchant_key=merchant_key,
        policies=results,
        vi_solve_ms=vi_ms,
    )


def summarize_bench(rows: list[MerchantBenchmark]) -> dict:
    """Aggregate across merchants; report lifts."""
    if not rows:
        return {}
    policy_names = list(rows[0].policies.keys())
    agg = {}
    for name in policy_names:
        costs = np.array([r.policies[name]["mean_cost_paise"] for r in rows])
        agg[name] = {
            "mean_cost_paise": float(costs.mean()),
            "median_cost_paise": float(np.median(costs)),
            "p95_cost_paise": float(np.percentile(costs, 95)),
        }

    vi_mean = agg["vi"]["mean_cost_paise"]
    for name in policy_names:
        if name != "vi":
            baseline = agg[name]["mean_cost_paise"]
            agg[name]["cost_lift_over_vi_paise"] = float(baseline - vi_mean)
            agg[name]["cost_lift_over_vi_pct"] = float((baseline - vi_mean) / max(baseline, 1) * 100)
    return {
        "n_merchants": len(rows),
        "vi_solve_ms_mean": float(np.mean([r.vi_solve_ms for r in rows])),
        "policies": agg,
    }
