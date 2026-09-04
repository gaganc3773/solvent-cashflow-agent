"""Solvent autonomous cashflow agent.

The agent closes the finance-ops loop:

    OBSERVE  →  DECIDE  →  GATE  →  ACT  →  REPORT
      │            │         │        │        │
      pull       run VI     check    call     append
      state      optimizer  policy   gateway  audit log

Each `tick()` is one iteration of the loop for a single merchant. In
production this runs on a schedule (e.g. hourly) plus on-webhook triggers.
For the demo, ticks can be run manually via `POST /agent/tick` or in
bulk via `POST /agent/simulate?days=N`.

Modes
-----
- 'off'          — agent does nothing
- 'advisory'     — computes recommendation, logs but does NOT execute
- 'semi_auto'    — executes IFF within policy caps; else logs "would_have"
- 'full_auto'    — executes anything the optimizer suggests within caps

Policy caps
-----------
- min_cash_floor_paise         — target balance below which agent acts
- max_auto_is_per_day_paise    — cap on how much IS agent can trigger daily
- allow_credit_draw            — whether agent can draw Razorpay Capital
- max_auto_credit_paise        — cap on credit draws
- quiet_hours                  — hours during which agent won't act
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Any

import numpy as np

from core.gateway.adapter import GatewayAdapter, ExecuteResult, get_adapter


AgentMode = Literal["off", "advisory", "semi_auto", "full_auto"]


@dataclass
class AgentPolicy:
    mode: AgentMode = "advisory"
    min_cash_floor_paise: int = 50_000_00        # ₹50K target minimum
    max_auto_is_per_day_paise: int = 1_00_000_00 # ₹1L/day IS cap
    allow_credit_draw: bool = False
    max_auto_credit_paise: int = 50_000_00       # ₹50K per event if credit on
    quiet_hours: tuple[int, int] = (0, 6)        # UTC hours [start, end) blocked

    def is_in_quiet_hours(self, at: datetime) -> bool:
        h = at.hour
        s, e = self.quiet_hours
        return s <= h < e if s <= e else (h >= s or h < e)


@dataclass
class AgentDecision:
    """What the optimizer suggested for this tick."""
    action_kind: str                   # "nothing" | "IS" | "credit"
    action_amount_paise: int
    action_label: str
    expected_cost_paise: int
    expected_cost_do_nothing_paise: int
    savings_vs_do_nothing_paise: int


@dataclass
class AgentGate:
    """Whether the policy allowed the decision through."""
    allowed: bool
    reason: str


@dataclass
class AgentAction:
    """One tick of the agent — the whole record, observed → reported."""
    tick_id: str
    ts_iso: str
    merchant_id: str
    mode: AgentMode
    # Observed state
    bank_paise: int
    pipeline_paise: int
    # Decision
    decision: AgentDecision
    # Gate
    gate: AgentGate
    # Outcome
    executed: bool
    execute_result: ExecuteResult | None
    # Post-action state (bank/pipeline after the action, for verification)
    post_bank_paise: int
    post_pipeline_paise: int

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class AgentStatus:
    merchant_id: str
    gateway: str
    policy: AgentPolicy
    n_actions_last_24h: int
    total_is_last_24h_paise: int
    total_credit_last_24h_paise: int
    current_bank_paise: int
    current_pipeline_paise: int
    last_action_ts_iso: str | None


class AgentRuntime:
    """Per-merchant agent. Holds policy, adapter, and action history."""

    def __init__(self, gateway: str, merchant_key: str,
                 policy: AgentPolicy | None = None):
        self.gateway_name = gateway
        self.merchant_key = merchant_key
        self.adapter: GatewayAdapter = get_adapter(gateway, merchant_key)
        self.policy = policy or AgentPolicy()
        self._actions: list[AgentAction] = []

    # ─── Public API ────────────────────────────────────────────────

    def set_policy(self, policy: AgentPolicy) -> None:
        self.policy = policy

    def status(self) -> AgentStatus:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = [a for a in self._actions
                  if datetime.fromisoformat(a.ts_iso) >= cutoff]
        is_total = sum(a.execute_result.amount_paise for a in recent
                       if a.executed and a.execute_result and a.execute_result.action == "instant_settle")
        cr_total = sum(a.execute_result.amount_paise for a in recent
                       if a.executed and a.execute_result and a.execute_result.action == "credit_draw")
        return AgentStatus(
            merchant_id=self.adapter.merchant_id,
            gateway=self.gateway_name,
            policy=self.policy,
            n_actions_last_24h=len(recent),
            total_is_last_24h_paise=is_total,
            total_credit_last_24h_paise=cr_total,
            current_bank_paise=self.adapter.get_bank_balance_paise(),
            current_pipeline_paise=self.adapter.get_pipeline_balance_paise(),
            last_action_ts_iso=self._actions[-1].ts_iso if self._actions else None,
        )

    def recent_actions(self, limit: int = 50) -> list[AgentAction]:
        return list(reversed(self._actions[-limit:]))

    def tick(self, at: datetime | None = None) -> AgentAction:
        """One iteration of observe → decide → gate → act → report."""
        at = at or datetime.now(timezone.utc)
        tick_id = f"tick_{at.strftime('%Y%m%d_%H%M%S')}_{len(self._actions):04d}"

        # ─── OBSERVE ─────
        bank = self.adapter.get_bank_balance_paise()
        pipe = self.adapter.get_pipeline_balance_paise()

        # ─── DECIDE ─────
        decision = self._decide(bank, pipe)

        # ─── GATE ─────
        gate = self._gate(decision, at)

        # ─── ACT ─────
        executed = False
        result: ExecuteResult | None = None
        if gate.allowed and self.policy.mode in ("semi_auto", "full_auto") \
           and decision.action_kind != "nothing":
            if decision.action_kind == "IS":
                result = self.adapter.execute_instant_settle(decision.action_amount_paise)
                executed = result.ok
            elif decision.action_kind == "credit":
                result = self.adapter.request_credit_draw(decision.action_amount_paise)
                executed = result.ok
        elif self.policy.mode == "advisory":
            # Advisory: log the recommendation but don't move money
            result = None

        # ─── REPORT ─────
        action = AgentAction(
            tick_id=tick_id,
            ts_iso=at.isoformat(),
            merchant_id=self.adapter.merchant_id,
            mode=self.policy.mode,
            bank_paise=bank,
            pipeline_paise=pipe,
            decision=decision,
            gate=gate,
            executed=executed,
            execute_result=result,
            post_bank_paise=self.adapter.get_bank_balance_paise(),
            post_pipeline_paise=self.adapter.get_pipeline_balance_paise(),
        )
        self._actions.append(action)
        return action

    def simulate(self, days: int) -> list[AgentAction]:
        """Run one tick per day for the next `days` days. Demo control."""
        base = datetime.now(timezone.utc)
        return [self.tick(at=base + timedelta(days=i)) for i in range(days)]

    # ─── Internals ─────────────────────────────────────────────────

    def _decide(self, bank: int, pipe: int) -> AgentDecision:
        """Call the same VI optimizer as the interactive UI, then package."""
        # Lazy imports to avoid cycles
        from datetime import date as _d
        from data.synthetic_merchants import DEMO_MERCHANTS
        from core.cashflow.forecast import (
            fit_history, simulate_delta_paths, build_schedule,
        )
        from core.cashflow.reconstruction import reconstruct
        from data.synthetic_merchants import generate_history, generate_expense_events
        from core.cashflow.optimizer_vi import (
            VIConfig, build_env_from_forecast, solve_vi, recommend,
        )

        profile = DEMO_MERCHANTS[self.merchant_key]
        # Cheap: reuse cached events via the same _load path
        try:
            from api.cashflow import _load, _DEFAULT_START, _DEFAULT_DAYS
            events = _load(self.merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
        except Exception:
            days = generate_history(profile, _d(2026, 6, 1), 90)
            exp = generate_expense_events(profile, _d(2026, 6, 1), 90)
            events = reconstruct(days, exp)

        fit = fit_history(events)
        start = _d(2026, 8, 30)
        H = 30
        daily_var = sum(profile.variable_expenses.values()) * 100
        bp, pp = simulate_delta_paths(fit, start, H, n_paths=1_500,
                                      daily_variable_expense_paise=daily_var, seed=7)
        sched = build_schedule(profile, events, start, H)
        fo = np.zeros(H)
        for d, amt, _, _ in sched.fixed_outflows:
            idx = (d - start).days
            if 0 <= idx < H:
                fo[idx] += amt

        cfg = VIConfig(horizon_days=H)
        env = build_env_from_forecast(cfg, bp, pp, fo)
        V, pi = solve_vi(cfg, env)
        rec = recommend(cfg, env, V, pi, bank, pipe, t=0)

        return AgentDecision(
            action_kind=rec.action_kind,
            action_amount_paise=rec.action_amount_paise,
            action_label=rec.action_label,
            expected_cost_paise=int(rec.expected_cost_paise),
            expected_cost_do_nothing_paise=int(rec.expected_cost_do_nothing_paise),
            savings_vs_do_nothing_paise=int(rec.savings_vs_do_nothing_paise),
        )

    def _gate(self, decision: AgentDecision, at: datetime) -> AgentGate:
        """Check whether the policy permits the decision to execute."""
        p = self.policy
        if p.mode == "off":
            return AgentGate(False, "agent is off")
        if decision.action_kind == "nothing":
            return AgentGate(True, "no action needed")
        if p.mode == "advisory":
            return AgentGate(False, "advisory mode — action logged, not executed")
        if p.is_in_quiet_hours(at):
            return AgentGate(False, f"quiet hours ({p.quiet_hours[0]:02d}:00–{p.quiet_hours[1]:02d}:00 UTC)")
        if decision.action_kind == "credit" and not p.allow_credit_draw:
            return AgentGate(False, "policy disallows credit draws")
        if decision.action_kind == "credit" and decision.action_amount_paise > p.max_auto_credit_paise:
            return AgentGate(False, f"credit draw exceeds per-event cap ₹{p.max_auto_credit_paise/100:.0f}")

        # 24h IS cap
        cutoff = at - timedelta(hours=24)
        is_today = sum(a.execute_result.amount_paise for a in self._actions
                       if a.executed and a.execute_result
                       and a.execute_result.action == "instant_settle"
                       and datetime.fromisoformat(a.ts_iso) >= cutoff)
        if decision.action_kind == "IS" and is_today + decision.action_amount_paise > p.max_auto_is_per_day_paise:
            return AgentGate(False, f"would exceed 24h IS cap ₹{p.max_auto_is_per_day_paise/100:.0f}")

        return AgentGate(True, "within all policy caps")


# ─── Runtime registry — one per merchant ────────────────────────────

_RUNTIMES: dict[tuple[str, str], AgentRuntime] = {}


def get_runtime(gateway: str, merchant_key: str) -> AgentRuntime:
    key = (gateway, merchant_key)
    if key not in _RUNTIMES:
        _RUNTIMES[key] = AgentRuntime(gateway, merchant_key)
    return _RUNTIMES[key]


def list_runtimes() -> list[AgentRuntime]:
    return list(_RUNTIMES.values())
