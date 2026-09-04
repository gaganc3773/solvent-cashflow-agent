"""FastAPI router for the Solvent autonomous cashflow agent."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.agent.runtime import (
    AgentPolicy, AgentMode, get_runtime, list_runtimes,
)


router = APIRouter(prefix="/agent", tags=["agent"])


# ─── Request models ─────────────────────────────────────────────────

class PolicyReq(BaseModel):
    mode: str = Field("advisory", pattern="^(off|advisory|semi_auto|full_auto)$")
    min_cash_floor_paise: int = 50_000_00
    max_auto_is_per_day_paise: int = 1_00_000_00
    allow_credit_draw: bool = False
    max_auto_credit_paise: int = 50_000_00
    quiet_hours_start: int = Field(0, ge=0, le=23)
    quiet_hours_end: int = Field(6, ge=0, le=23)


# ─── Response models ────────────────────────────────────────────────

class PolicyOut(BaseModel):
    mode: str
    min_cash_floor_paise: int
    max_auto_is_per_day_paise: int
    allow_credit_draw: bool
    max_auto_credit_paise: int
    quiet_hours: tuple[int, int]


class DecisionOut(BaseModel):
    action_kind: str
    action_amount_paise: int
    action_label: str
    expected_cost_paise: int
    expected_cost_do_nothing_paise: int
    savings_vs_do_nothing_paise: int


class GateOut(BaseModel):
    allowed: bool
    reason: str


class ExecuteResultOut(BaseModel):
    ok: bool
    action: str
    amount_paise: int
    fee_paise: int
    gateway_reference: str | None
    error: str | None = None


class ActionOut(BaseModel):
    tick_id: str
    ts_iso: str
    merchant_id: str
    mode: str
    bank_paise: int
    pipeline_paise: int
    decision: DecisionOut
    gate: GateOut
    executed: bool
    execute_result: ExecuteResultOut | None
    post_bank_paise: int
    post_pipeline_paise: int


class StatusOut(BaseModel):
    merchant_id: str
    gateway: str
    policy: PolicyOut
    n_actions_last_24h: int
    total_is_last_24h_paise: int
    total_credit_last_24h_paise: int
    current_bank_paise: int
    current_pipeline_paise: int
    last_action_ts_iso: str | None


# ─── Helpers ───────────────────────────────────────────────────────

def _serialize_policy(p: AgentPolicy) -> PolicyOut:
    return PolicyOut(
        mode=p.mode,
        min_cash_floor_paise=p.min_cash_floor_paise,
        max_auto_is_per_day_paise=p.max_auto_is_per_day_paise,
        allow_credit_draw=p.allow_credit_draw,
        max_auto_credit_paise=p.max_auto_credit_paise,
        quiet_hours=(p.quiet_hours[0], p.quiet_hours[1]),
    )


def _serialize_action(a) -> ActionOut:
    return ActionOut(
        tick_id=a.tick_id,
        ts_iso=a.ts_iso,
        merchant_id=a.merchant_id,
        mode=a.mode,
        bank_paise=a.bank_paise,
        pipeline_paise=a.pipeline_paise,
        decision=DecisionOut(**{
            "action_kind": a.decision.action_kind,
            "action_amount_paise": a.decision.action_amount_paise,
            "action_label": a.decision.action_label,
            "expected_cost_paise": a.decision.expected_cost_paise,
            "expected_cost_do_nothing_paise": a.decision.expected_cost_do_nothing_paise,
            "savings_vs_do_nothing_paise": a.decision.savings_vs_do_nothing_paise,
        }),
        gate=GateOut(allowed=a.gate.allowed, reason=a.gate.reason),
        executed=a.executed,
        execute_result=(
            ExecuteResultOut(
                ok=a.execute_result.ok,
                action=a.execute_result.action,
                amount_paise=a.execute_result.amount_paise,
                fee_paise=a.execute_result.fee_paise,
                gateway_reference=a.execute_result.gateway_reference,
                error=a.execute_result.error,
            )
            if a.execute_result else None
        ),
        post_bank_paise=a.post_bank_paise,
        post_pipeline_paise=a.post_pipeline_paise,
    )


# ─── Endpoints ─────────────────────────────────────────────────────

@router.get("/status/{merchant_key}", response_model=StatusOut)
def get_status(merchant_key: str, gateway: str = Query("razorpay", pattern="^(razorpay|stripe)$")):
    rt = get_runtime(gateway, merchant_key)
    s = rt.status()
    return StatusOut(
        merchant_id=s.merchant_id,
        gateway=s.gateway,
        policy=_serialize_policy(s.policy),
        n_actions_last_24h=s.n_actions_last_24h,
        total_is_last_24h_paise=s.total_is_last_24h_paise,
        total_credit_last_24h_paise=s.total_credit_last_24h_paise,
        current_bank_paise=s.current_bank_paise,
        current_pipeline_paise=s.current_pipeline_paise,
        last_action_ts_iso=s.last_action_ts_iso,
    )


@router.post("/policy/{merchant_key}", response_model=StatusOut)
def set_policy(merchant_key: str, req: PolicyReq,
               gateway: str = Query("razorpay")):
    rt = get_runtime(gateway, merchant_key)
    rt.set_policy(AgentPolicy(
        mode=req.mode,                          # type: ignore[arg-type]
        min_cash_floor_paise=req.min_cash_floor_paise,
        max_auto_is_per_day_paise=req.max_auto_is_per_day_paise,
        allow_credit_draw=req.allow_credit_draw,
        max_auto_credit_paise=req.max_auto_credit_paise,
        quiet_hours=(req.quiet_hours_start, req.quiet_hours_end),
    ))
    return get_status(merchant_key, gateway)


@router.post("/tick/{merchant_key}", response_model=ActionOut)
def tick(merchant_key: str, gateway: str = Query("razorpay")):
    rt = get_runtime(gateway, merchant_key)
    a = rt.tick()
    return _serialize_action(a)


@router.post("/simulate/{merchant_key}", response_model=list[ActionOut])
def simulate(merchant_key: str, days: int = Query(7, ge=1, le=30),
             gateway: str = Query("razorpay")):
    rt = get_runtime(gateway, merchant_key)
    actions = rt.simulate(days)
    return [_serialize_action(a) for a in actions]


@router.get("/actions/{merchant_key}", response_model=list[ActionOut])
def actions(merchant_key: str, limit: int = Query(50, ge=1, le=500),
            gateway: str = Query("razorpay")):
    rt = get_runtime(gateway, merchant_key)
    return [_serialize_action(a) for a in rt.recent_actions(limit)]


@router.post("/reset/{merchant_key}", response_model=StatusOut)
def reset(merchant_key: str, gateway: str = Query("razorpay")):
    """Clear this merchant's agent history + reset bank/pipeline to opening.
    Useful during a demo to re-run scenarios from scratch."""
    from core.agent.runtime import _RUNTIMES
    key = (gateway, merchant_key)
    if key in _RUNTIMES:
        del _RUNTIMES[key]
    return get_status(merchant_key, gateway)
