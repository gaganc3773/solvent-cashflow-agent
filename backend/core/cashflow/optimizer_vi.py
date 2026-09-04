"""Value iteration on the merchant cash-position HJB.

Problem
-------
At each day t the merchant sees state s = (bank_paise, pipeline_paise) and
picks an action. Random inflow arrives, deterministic bills go out, state
transitions, we advance t. Minimize expected total cost over H = 30 days.

Actions
-------
- nothing
- IS(y) — pull y from pipeline into bank; pay is_fee_bps × y in fees
- credit(z) — draw z from Razorpay Capital; incurs credit_apr × z / 365
              interest per day held (until horizon end, kept simple)

Costs (per day, added to Q)
----------------------------
- IS fees (immediate)
- overdraft: |bank|⁻ × overdraft_apr / 365 (per day bank is negative)
- credit interest for that day

Bellman
-------
V(s, t) = min_a  [ c(s, a, t)  +  E[V(s', t+1) | s, a] ]

Transition model
----------------
bank'   = bank + action_bank_effect - is_fee + fixed_out(t) + inflow_stoch
pipeline' = pipe + action_pipe_effect + pipe_delta_stoch

`inflow_stoch` and `pipe_delta_stoch` are 3-scenario samples (low / mid / high)
drawn from the forecaster's per-day distribution. Weights (0.2, 0.6, 0.2) so
mean matches and tails are represented.

This is the discretised HJB PDE for the impulse-control problem. It reduces
to classical cash-management (Miller-Orr 1966, Constantinides 1976) with an
added T+2 pipeline lag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


# ─── Config ─────────────────────────────────────────────────────────

@dataclass
class VIConfig:
    horizon_days: int = 30
    bank_lo_paise: int = -3_00_000_00        # -₹3L
    bank_hi_paise: int = +10_00_000_00       # +₹10L
    step_bank_paise: int = 20_000_00         # ₹20K bucket
    pipe_hi_paise: int = 3_00_000_00         # +₹3L
    step_pipe_paise: int = 30_000_00         # ₹30K bucket
    is_fee_bps: int = 30                     # 0.30% Razorpay default
    credit_apr: float = 0.14
    # Overdraft "APR" is a shorthand for the ALL-IN cost of going negative
    # for a day — bank penalty fees, credit-score damage, emergency-IS at
    # bad terms, bounced-payment fees to vendors/employees, reputation cost.
    # A bounced payroll isn't just interest — it damages team trust in
    # ways that dwarf any interest calc. We use 12.0 (~₹3,300/day per ₹1L)
    # as a merchant-calibrated proxy. Real merchants can set this per
    # their own risk appetite in production.
    overdraft_apr: float = 12.0
    credit_options_paise: tuple[int, ...] = (50_000_00, 1_00_000_00, 2_00_000_00)
    is_ratios: tuple[float, ...] = (0.25, 0.50, 0.75, 1.0)

    @property
    def n_bank(self) -> int:
        return (self.bank_hi_paise - self.bank_lo_paise) // self.step_bank_paise + 1

    @property
    def n_pipe(self) -> int:
        return self.pipe_hi_paise // self.step_pipe_paise + 1


@dataclass
class Action:
    kind: str                     # "nothing" | "IS" | "credit"
    amount_paise: int = 0
    is_ratio: float = 0.0

    def label(self) -> str:
        if self.kind == "nothing":
            return "Do nothing"
        if self.kind == "IS":
            return f"Instant-settle ₹{self.amount_paise/100:,.0f}"
        if self.kind == "credit":
            return f"Draw ₹{self.amount_paise/100:,.0f} credit"
        return "?"


def enumerate_actions(cfg: VIConfig, pipeline_paise: int) -> list[Action]:
    acts: list[Action] = [Action("nothing")]
    for r in cfg.is_ratios:
        y = int(pipeline_paise * r)
        if y >= 10_000_00:  # skip tiny IS actions below ₹10K
            acts.append(Action("IS", amount_paise=y, is_ratio=r))
    for z in cfg.credit_options_paise:
        acts.append(Action("credit", amount_paise=z))
    return acts


# ─── Environment ────────────────────────────────────────────────────

@dataclass
class DailyEnv:
    """Per-day transition inputs, pre-derived from the forecaster.

    Length H arrays. Scalars are signed paise (negative = outflow).
    inflow_scenarios shape (H, 3), same for pipe_delta_scenarios.
    weights shape (3,).
    """
    fixed_out: np.ndarray                # (H,) signed, deterministic
    inflow_scenarios: np.ndarray         # (H, 3) stochastic bank credits+debits sans fixed_out
    pipe_delta_scenarios: np.ndarray     # (H, 3) stochastic pipeline change
    weights: np.ndarray                  # (3,) sum = 1


def build_env_from_forecast(
    cfg: VIConfig,
    bank_delta_paths: np.ndarray,       # (n_paths, H) — stochastic bank delta only
    pipe_delta_paths: np.ndarray,       # (n_paths, H) — stochastic pipeline delta only
    fixed_out_by_day: np.ndarray,       # (H,) signed
) -> DailyEnv:
    """Collapse (n_paths, H) sample paths into 3-scenario (low/mid/high) VI env."""
    H = fixed_out_by_day.shape[0]
    bank_scenarios = np.stack([
        np.percentile(bank_delta_paths, 10, axis=0),
        np.percentile(bank_delta_paths, 50, axis=0),
        np.percentile(bank_delta_paths, 90, axis=0),
    ], axis=1)  # (H, 3)
    pipe_scenarios = np.stack([
        np.percentile(pipe_delta_paths, 10, axis=0),
        np.percentile(pipe_delta_paths, 50, axis=0),
        np.percentile(pipe_delta_paths, 90, axis=0),
    ], axis=1)  # (H, 3)
    weights = np.array([0.2, 0.6, 0.2])
    return DailyEnv(
        fixed_out=fixed_out_by_day,
        inflow_scenarios=bank_scenarios,
        pipe_delta_scenarios=pipe_scenarios,
        weights=weights,
    )


# ─── Grid helpers ───────────────────────────────────────────────────

def _to_bank_idx(cfg: VIConfig, bank) -> np.ndarray:
    arr = np.atleast_1d(bank)
    idx = np.round((arr - cfg.bank_lo_paise) / cfg.step_bank_paise).astype(np.int64)
    return np.clip(idx, 0, cfg.n_bank - 1)


def _to_pipe_idx(cfg: VIConfig, pipe) -> np.ndarray:
    arr = np.atleast_1d(pipe)
    idx = np.round(arr / cfg.step_pipe_paise).astype(np.int64)
    return np.clip(idx, 0, cfg.n_pipe - 1)


def _bank_of_idx(cfg: VIConfig, i: int) -> int:
    return cfg.bank_lo_paise + i * cfg.step_bank_paise


def _pipe_of_idx(cfg: VIConfig, j: int) -> int:
    return j * cfg.step_pipe_paise


# ─── VI solver ──────────────────────────────────────────────────────

def solve_vi(cfg: VIConfig, env: DailyEnv) -> tuple[np.ndarray, np.ndarray]:
    """Backward VI. Returns (V, pi).

    V:  (H+1, NB, NP)   value function; V[H] = 0
    pi: (H, NB, NP)     chosen action index (see enumerate_actions).
                        The list of actions can vary by pipeline bucket, so
                        pi is the *index* within that bucket's action list.
    """
    H = cfg.horizon_days
    NB, NP = cfg.n_bank, cfg.n_pipe

    V = np.zeros((H + 1, NB, NP), dtype=np.float64)
    pi = np.full((H, NB, NP), -1, dtype=np.int32)

    action_cache = [enumerate_actions(cfg, _pipe_of_idx(cfg, j)) for j in range(NP)]

    # Pre-broadcast bank and pipe centres
    banks = np.arange(NB) * cfg.step_bank_paise + cfg.bank_lo_paise  # (NB,)
    pipes = np.arange(NP) * cfg.step_pipe_paise                       # (NP,)

    for t in range(H - 1, -1, -1):
        fixed_out = env.fixed_out[t]                                   # scalar
        inflow = env.inflow_scenarios[t]                               # (3,)
        pipe_delta = env.pipe_delta_scenarios[t]                       # (3,)
        w = env.weights                                                # (3,)

        for j in range(NP):
            pipe = pipes[j]
            actions = action_cache[j]

            # Precompute next-pipe under each action (same for all bank states in j)
            pipe_after_act = np.array([
                pipe - min(a.amount_paise, int(pipe)) if a.kind == "IS" else pipe
                for a in actions
            ])                                                          # (A,)

            for i, bank in enumerate(banks):
                # For each action, compute Q(s, a)
                best_q = np.inf
                best_a = 0
                for a_idx, act in enumerate(actions):
                    # Action effect on bank + immediate cost
                    if act.kind == "IS":
                        amt = min(act.amount_paise, int(pipe))
                        fee = int(amt * cfg.is_fee_bps / 10_000)
                        bank_after_act = bank + amt - fee
                        immediate = fee
                    elif act.kind == "credit":
                        bank_after_act = bank + act.amount_paise
                        # Charge total interest for the credit assuming it's
                        # held from t to horizon end (realistic for working-
                        # capital advances — merchants don't flip them daily).
                        # This keeps the state 2D (no credit_outstanding dim).
                        days_held = cfg.horizon_days - t
                        immediate = int(act.amount_paise * cfg.credit_apr * days_held / 365)
                    else:
                        bank_after_act = bank
                        immediate = 0

                    # Deterministic bill
                    bank_after = bank_after_act + fixed_out

                    # Apply 3 stochastic scenarios
                    bank_s = bank_after + inflow                        # (3,)
                    pipe_s = pipe_after_act[a_idx] + pipe_delta         # (3,)

                    # Overdraft penalty (per-scenario, then weighted)
                    penalty_s = np.where(bank_s < 0,
                                         -bank_s * cfg.overdraft_apr / 365,
                                         0.0)                            # (3,)

                    # V(s') lookup, weighted
                    bidx = _to_bank_idx(cfg, bank_s)
                    pidx = _to_pipe_idx(cfg, pipe_s)
                    Vnext = V[t + 1, bidx, pidx]                        # (3,)

                    q = immediate + float((w * (penalty_s + Vnext)).sum())
                    if q < best_q:
                        best_q = q
                        best_a = a_idx

                V[t, i, j] = best_q
                pi[t, i, j] = best_a

    return V, pi


# ─── Recommendation ─────────────────────────────────────────────────

def rollout_do_nothing_forever(cfg: VIConfig, env: DailyEnv,
                               bank_now: int, pipe_now: int,
                               n_samples: int = 200) -> float:
    """Simulate the 'never act' policy on n_samples forecast paths, return
    expected total overdraft cost over the whole horizon.

    Unlike Q(do_nothing, t=0) — which assumes optimal play from tomorrow —
    this rolls out the FULL horizon with zero actions ever. It's the honest
    'if the merchant did nothing all month' baseline for savings display."""
    H = cfg.horizon_days
    rng = np.random.default_rng(42)
    # Sample n_samples independent realisations from the 3-scenario distribution
    scenario_choices = rng.choice(3, size=(n_samples, H), p=env.weights)

    total_cost = np.zeros(n_samples)
    bank = np.full(n_samples, float(bank_now))
    pipe = np.full(n_samples, float(pipe_now))

    for t in range(H):
        # Apply deterministic bill
        bank += env.fixed_out[t]
        # Sample per-path stochastic delta
        inflow = env.inflow_scenarios[t][scenario_choices[:, t]]
        pipe_d = env.pipe_delta_scenarios[t][scenario_choices[:, t]]
        bank += inflow
        pipe += pipe_d
        # Accumulate overdraft penalty for this day
        penalty = np.where(bank < 0, -bank * cfg.overdraft_apr / 365, 0.0)
        total_cost += penalty

    return float(total_cost.mean())


@dataclass
class Recommendation:
    action_label: str
    action_kind: str
    action_amount_paise: int
    expected_cost_paise: float                        # Q(optimal, t=0) — includes optimal future play
    expected_cost_do_nothing_paise: float             # Q(do_nothing today, t=0) — still optimal after
    expected_cost_never_act_paise: float              # pure do-nothing whole month — the honest baseline
    savings_vs_do_nothing_paise: float                # today's marginal saving
    savings_vs_never_act_paise: float                 # the "not using Solvent at all" saving
    defensible_range: list[str]
    q_by_action: dict[str, float]
    reason: str


def _q_for_action(cfg: VIConfig, env: DailyEnv, V: np.ndarray, t: int,
                  bank: int, pipe: int, act: Action) -> float:
    """Q-value under the current V, for a single (state, action, t) triple."""
    if act.kind == "IS":
        amt = min(act.amount_paise, pipe)
        fee = int(amt * cfg.is_fee_bps / 10_000)
        bank_after = bank + amt - fee
        pipe_after = pipe - amt
        immediate = fee
    elif act.kind == "credit":
        bank_after = bank + act.amount_paise
        pipe_after = pipe
        days_held = cfg.horizon_days - t
        immediate = int(act.amount_paise * cfg.credit_apr * days_held / 365)
    else:
        bank_after = bank
        pipe_after = pipe
        immediate = 0

    bank_after += env.fixed_out[t]
    bank_s = bank_after + env.inflow_scenarios[t]                       # (3,)
    pipe_s = pipe_after + env.pipe_delta_scenarios[t]                   # (3,)

    penalty_s = np.where(bank_s < 0, -bank_s * cfg.overdraft_apr / 365, 0.0)
    bidx = _to_bank_idx(cfg, bank_s)
    pidx = _to_pipe_idx(cfg, pipe_s)
    Vnext = V[t + 1, bidx, pidx]
    return float(immediate + (env.weights * (penalty_s + Vnext)).sum())


def recommend(cfg: VIConfig, env: DailyEnv, V: np.ndarray, pi: np.ndarray,
              bank_now_paise: int, pipe_now_paise: int, t: int = 0) -> Recommendation:
    """Look up the current state, return the optimal action + explanation."""
    pipe_bucket = int(_to_pipe_idx(cfg, np.asarray([pipe_now_paise]))[0])
    actions = enumerate_actions(cfg, _pipe_of_idx(cfg, pipe_bucket))

    q_by_action = {a.label(): _q_for_action(cfg, env, V, t, bank_now_paise, pipe_now_paise, a)
                   for a in actions}

    # Optimal action = argmin
    best_label = min(q_by_action, key=lambda k: q_by_action[k])
    best_act = next(a for a in actions if a.label() == best_label)
    q_best = q_by_action[best_label]
    q_nothing = q_by_action["Do nothing"]

    # Defensible range = anything within ₹200 of the best (rounding + noise)
    threshold = q_best + 200.0
    defensible = [lbl for lbl, q in q_by_action.items() if q <= threshold]

    savings = q_nothing - q_best

    if best_act.kind == "nothing":
        reason = (
            f"Your expected balance stays comfortably above zero. Any IS or "
            f"credit fee would cost more than the risk it removes. Expected "
            f"cost of doing nothing: ₹{q_nothing/100:,.0f}."
        )
    elif best_act.kind == "IS":
        fee = int(best_act.amount_paise * cfg.is_fee_bps / 10_000)
        reason = (
            f"Pulling ₹{best_act.amount_paise/100:,.0f} forward from your "
            f"Razorpay pipeline costs ₹{fee/100:,.0f} in Instant Settlement fees, "
            f"but reduces your expected overdraft cost by ₹{(savings + fee)/100:,.0f}. "
            f"Net saving vs doing nothing: ₹{savings/100:,.0f}."
        )
    else:
        reason = (
            f"Drawing ₹{best_act.amount_paise/100:,.0f} from Razorpay Capital "
            f"is the cheapest option here — your pipeline is too thin for IS "
            f"to cover the projected gap alone. "
            f"Net saving vs doing nothing: ₹{savings/100:,.0f}."
        )

    # Honest "if you never act all month" baseline — the real comparison
    never_act_cost = rollout_do_nothing_forever(cfg, env, bank_now_paise, pipe_now_paise)
    savings_vs_never = never_act_cost - q_best

    return Recommendation(
        action_label=best_label,
        action_kind=best_act.kind,
        action_amount_paise=best_act.amount_paise,
        expected_cost_paise=q_best,
        expected_cost_do_nothing_paise=q_nothing,
        expected_cost_never_act_paise=never_act_cost,
        savings_vs_do_nothing_paise=savings,
        savings_vs_never_act_paise=savings_vs_never,
        defensible_range=defensible,
        q_by_action=q_by_action,
        reason=reason,
    )
