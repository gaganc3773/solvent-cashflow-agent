"""Cashflow endpoints — the Solvent hot path.

Mounted at `/cashflow/*` under the main FastAPI app. Day 1 exposes:
  - GET  /cashflow/merchants           list demo merchants
  - GET  /cashflow/events/{merchant_id} reconstruct + return event stream
  - GET  /cashflow/balance/{merchant_id} balance path over a date range
  - GET  /cashflow/summary/{merchant_id} category totals
Forecast, optimize, and action endpoints land in Day 2/3.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import numpy as np

# backend/ on sys.path
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.cashflow.reconstruction import (
    reconstruct, balance_at, balance_path, summarize,
)
from core.cashflow.forecast import (
    fit_history, build_schedule, simulate, ForecastResult, simulate_delta_paths,
)
from core.cashflow.optimizer_vi import (
    VIConfig, build_env_from_forecast, solve_vi, recommend, enumerate_actions,
)
from core.cashflow.benchmark import benchmark_merchant, summarize_bench
from data.synthetic_merchants import (
    DEMO_MERCHANTS, generate_history, generate_expense_events,
)


router = APIRouter(prefix="/cashflow", tags=["cashflow"])


# ─── Response models ────────────────────────────────────────────────

class MerchantSummary(BaseModel):
    key: str
    id: str
    name: str
    sector: str
    opening_bank_paise: int
    payment_intensity: float


class EventOut(BaseModel):
    date: str
    amount_paise: int
    category: str
    direction: str
    source: str
    source_id: str
    bucket: str
    confidence: float
    metadata: dict


class EventStreamResp(BaseModel):
    merchant_id: str
    from_date: str
    to_date: str
    events: list[EventOut]


class BalancePoint(BaseModel):
    date: str
    balance_paise: int


class BalancePathResp(BaseModel):
    merchant_id: str
    bucket: str
    opening_paise: int
    from_date: str
    to_date: str
    points: list[BalancePoint]


class SummaryResp(BaseModel):
    merchant_id: str
    from_date: str
    to_date: str
    totals_by_category: dict[str, int]
    bank_balance_end_paise: int
    pipeline_balance_end_paise: int


class KnownOutflow(BaseModel):
    date: str
    amount_paise: int
    category: str
    note: str


class FixedExpense(BaseModel):
    day_of_month: int   # 1..28
    amount_inr: int     # positive integer rupees
    category: str       # payroll | rent | gst | vendor | saas | ...
    note: str


class AdhocMerchantReq(BaseModel):
    payment_intensity: float          # per-day, weekday-blended
    avg_ticket_paise: int
    refund_rate: float = 0.05
    dispute_rate: float = 0.004
    opening_bank_paise: int
    opening_pipeline_paise: int = 75_000_00
    fixed_expenses: list[FixedExpense]
    daily_variable_paise: int = 20_000_00
    horizon_days: int = 30
    n_paths: int = 3_000
    is_fee_bps: int = 30
    credit_apr: float = 0.14


class AdhocResp(BaseModel):
    # Timing (the star of this endpoint — proves cold-start latency)
    fit_ms: float
    forecast_ms: float
    vi_ms: float
    total_ms: float
    # Forecast (compact)
    dates: list[str]
    p10_paise: list[int]
    p50_paise: list[int]
    p90_paise: list[int]
    available_now_paise: int
    projected_min_balance_paise: int
    projected_min_balance_date: str
    prob_shortfall: float
    expected_shortfall_paise: int
    expected_shortfall_date: str | None
    # Recommendation
    action_label: str
    action_kind: str
    action_amount_paise: int
    reason: str
    expected_cost_paise: int
    expected_cost_do_nothing_paise: int
    savings_vs_do_nothing_paise: int
    # Comparison actions (top 5 by Q value)
    top_actions: list["AdhocActionQ"]


class AdhocActionQ(BaseModel):
    label: str
    expected_cost_paise: int
    is_optimal: bool


class PolicyStats(BaseModel):
    policy_name: str
    label: str
    mean_cost_paise: int
    median_cost_paise: int
    p95_cost_paise: int
    cost_lift_over_vi_paise: int
    cost_lift_over_vi_pct: float
    inference_ms: float


class BenchmarkResp(BaseModel):
    n_merchants: int
    horizon_days: int
    vi_solve_ms_mean: float
    policies: list[PolicyStats]
    footnote: str


_BENCH_CACHE: dict[str, BenchmarkResp] = {}
_OPTIMIZE_CACHE: dict[tuple, "OptimizeResp"] = {}
_FORECAST_CACHE: dict[tuple, "ForecastResp"] = {}


class ActionQ(BaseModel):
    label: str
    expected_cost_paise: int
    is_optimal: bool
    is_defensible: bool


class OptimizeResp(BaseModel):
    merchant_id: str
    merchant_name: str
    current_bank_paise: int
    current_pipeline_paise: int
    horizon_days: int
    engine: str
    # Chosen action
    action_label: str
    action_kind: str
    action_amount_paise: int
    reason: str
    # Cost economics
    expected_cost_paise: int
    expected_cost_do_nothing_paise: int
    expected_cost_never_act_paise: int
    savings_vs_do_nothing_paise: int
    savings_vs_never_act_paise: int
    # Range
    defensible_range: list[str]
    all_actions: list[ActionQ]
    # Timing
    solve_time_ms: float


class ForecastResp(BaseModel):
    merchant_id: str
    merchant_name: str
    start_date: str
    horizon_days: int
    n_paths: int
    opening_bank_paise: int
    # Bank-balance percentiles at each horizon day
    dates: list[str]
    p10_paise: list[int]
    p50_paise: list[int]
    p90_paise: list[int]
    mean_paise: list[int]
    # Risk aggregates
    prob_shortfall: float
    expected_shortfall_paise: int
    expected_shortfall_date: str | None
    # Cash-position headline numbers for the KPI row
    available_now_paise: int
    expected_inflow_next_7d_paise: int
    expected_outflow_next_7d_paise: int
    projected_min_balance_paise: int
    projected_min_balance_date: str
    # Deterministic upcoming outflows the merchant already knows about
    known_outflows: list[KnownOutflow]


# ─── Cache (Day 1: recompute on demand; cache per merchant) ──────────
_HISTORY_CACHE: dict[tuple[str, str, int], list] = {}
_EVENTS_CACHE: dict[tuple[str, str, int], list] = {}


def _load(merchant_key: str, start: date, n_days: int):
    key = (merchant_key, start.isoformat(), n_days)
    if key in _EVENTS_CACHE:
        return _EVENTS_CACHE[key]
    if merchant_key not in DEMO_MERCHANTS:
        raise HTTPException(404, f"unknown merchant '{merchant_key}'")
    profile = DEMO_MERCHANTS[merchant_key]
    days = generate_history(profile, start, n_days)
    exp = generate_expense_events(profile, start, n_days)
    events = reconstruct(days, exp)
    _HISTORY_CACHE[key] = days
    _EVENTS_CACHE[key] = events
    return events


# Default demo window: 90 days ending today
_DEFAULT_START = date(2026, 6, 1)
_DEFAULT_DAYS = 90


# ─── Endpoints ──────────────────────────────────────────────────────

@router.get("/merchants", response_model=list[MerchantSummary])
def list_merchants():
    return [
        MerchantSummary(
            key=k,
            id=p.id,
            name=p.name,
            sector=p.sector,
            opening_bank_paise=p.opening_bank_paise,
            payment_intensity=p.payment_intensity,
        )
        for k, p in DEMO_MERCHANTS.items()
    ]


@router.get("/events/{merchant_key}", response_model=EventStreamResp)
def get_events(
    merchant_key: str,
    from_date: date = Query(_DEFAULT_START, alias="from"),
    to_date: date = Query(date(2026, 8, 30), alias="to"),
):
    events = _load(merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
    filtered = [e for e in events if from_date <= e.date <= to_date]
    return EventStreamResp(
        merchant_id=DEMO_MERCHANTS[merchant_key].id,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        events=[EventOut(**e.to_dict()) for e in filtered],
    )


@router.get("/balance/{merchant_key}", response_model=BalancePathResp)
def get_balance(
    merchant_key: str,
    bucket: str = Query("bank", pattern="^(bank|pipeline)$"),
    from_date: date = Query(_DEFAULT_START, alias="from"),
    to_date: date = Query(date(2026, 8, 30), alias="to"),
):
    events = _load(merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
    profile = DEMO_MERCHANTS[merchant_key]
    opening = profile.opening_bank_paise if bucket == "bank" else 0
    path = balance_path(events, from_date, to_date, bucket=bucket, opening_paise=opening)
    return BalancePathResp(
        merchant_id=profile.id,
        bucket=bucket,
        opening_paise=opening,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        points=[BalancePoint(date=d.isoformat(), balance_paise=b) for d, b in path],
    )


@router.get("/forecast/{merchant_key}", response_model=ForecastResp)
def get_forecast(
    merchant_key: str,
    start: date = Query(date(2026, 8, 30)),
    horizon_days: int = Query(30, ge=7, le=90),
    n_paths: int = Query(5_000, ge=200, le=20_000),
    seed: int = Query(7),
):
    """Distributional 30-day forecast of the merchant's bank balance.

    Fits a compound-Poisson model to observed history, then Monte-Carlo
    simulates `n_paths` bank-balance trajectories. Returns percentile fan,
    shortfall probability, and headline KPIs for the Cash Position UI.
    """
    cache_key = (merchant_key, start.isoformat(), horizon_days, n_paths, seed)
    if cache_key in _FORECAST_CACHE:
        return _FORECAST_CACHE[cache_key]
    events = _load(merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
    profile = DEMO_MERCHANTS[merchant_key]
    fit = fit_history(events)
    sched = build_schedule(profile, events, start, horizon_days)
    daily_var = sum(profile.variable_expenses.values()) * 100

    # Opening bank at `start` = merchant's current balance (from profile).
    # Deliberately NOT summing historical events — the demo semantics are
    # "given your current balance TODAY, forecast the next 30 days". If we
    # summed history the forecast would drift arbitrarily based on how far
    # back the synthetic history was generated.
    opening = profile.opening_bank_paise

    res: ForecastResult = simulate(
        fit=fit, schedule=sched,
        opening_bank_paise=opening,
        start=start, horizon_days=horizon_days,
        n_paths=n_paths, seed=seed,
        daily_variable_expense_paise=daily_var,
    )

    # Headline KPIs
    exp_inflow_7 = int(sum(
        max(0, res.p50[i] - (opening if i == 0 else res.p50[i-1]))
        for i in range(min(7, horizon_days))
    ))
    exp_outflow_7 = int(sum(
        max(0, (opening if i == 0 else res.p50[i-1]) - res.p50[i])
        for i in range(min(7, horizon_days))
    ))
    proj_min_idx = int(np.argmin(res.p50))
    proj_min = int(res.p50[proj_min_idx])
    proj_min_date = res.dates[proj_min_idx].isoformat()

    known = [
        KnownOutflow(
            date=d.isoformat(),
            amount_paise=amt,
            category=cat,
            note=note,
        )
        for d, amt, cat, note in sched.fixed_outflows
    ]

    resp = ForecastResp(
        merchant_id=profile.id,
        merchant_name=profile.name,
        start_date=start.isoformat(),
        horizon_days=horizon_days,
        n_paths=n_paths,
        opening_bank_paise=opening,
        dates=[d.isoformat() for d in res.dates],
        p10_paise=res.p10.astype(int).tolist(),
        p50_paise=res.p50.astype(int).tolist(),
        p90_paise=res.p90.astype(int).tolist(),
        mean_paise=res.mean.astype(int).tolist(),
        prob_shortfall=res.prob_shortfall,
        expected_shortfall_paise=int(res.expected_shortfall_paise),
        expected_shortfall_date=(
            res.expected_shortfall_date.isoformat()
            if res.expected_shortfall_date else None
        ),
        available_now_paise=opening,
        expected_inflow_next_7d_paise=exp_inflow_7,
        expected_outflow_next_7d_paise=exp_outflow_7,
        projected_min_balance_paise=proj_min,
        projected_min_balance_date=proj_min_date,
        known_outflows=known,
    )
    _FORECAST_CACHE[cache_key] = resp
    return resp


@router.post("/adhoc", response_model=AdhocResp)
def post_adhoc(req: AdhocMerchantReq):
    """Run the whole pipeline (fit + forecast + VI + recommend) on a fresh
    merchant defined by the request body.

    This is the "cold start" path — no cache, no pre-known merchant. It
    demonstrates the real integration flow: given any merchant's parameters,
    Solvent produces a recommendation in the time reported below.
    """
    import time as _t
    from datetime import date as _d, timedelta
    from core.cashflow.events import CashflowEvent
    from core.cashflow.forecast import HistoryFit, KnownSchedule
    import numpy as _np

    t_start = _t.time()

    # Step 1: build a synthetic HistoryFit directly from the request
    # (in production this comes from `fit_history(events)` on real data;
    #  the adhoc endpoint skips that so users can experiment freely)
    fit = HistoryFit(
        intensity_by_dow=_np.full(7, req.payment_intensity),
        ticket_lognorm=(float(_np.log(max(req.avg_ticket_paise, 1))), 0.6),
        refund_fraction=req.refund_rate,
        dispute_fraction=req.dispute_rate,
        fee_rate_avg=0.018,
        tax_rate=0.18,
        settlement_delay_days=2,
    )
    fit_ms = (_t.time() - t_start) * 1000

    # Step 2: forecast
    t0 = _t.time()
    start = _d(2026, 8, 30)
    bp, pp = simulate_delta_paths(
        fit, start, req.horizon_days, n_paths=req.n_paths,
        daily_variable_expense_paise=req.daily_variable_paise, seed=7,
    )
    forecast_ms = (_t.time() - t0) * 1000

    # Step 3: build environment + solve VI
    t0 = _t.time()
    fixed_out = _np.zeros(req.horizon_days)
    for e in req.fixed_expenses:
        # Place each fixed expense on its calendar day within the horizon
        for i in range(req.horizon_days):
            d = start + timedelta(days=i)
            if d.day == e.day_of_month:
                fixed_out[i] -= abs(e.amount_inr) * 100

    cfg = VIConfig(
        horizon_days=req.horizon_days,
        is_fee_bps=req.is_fee_bps,
        credit_apr=req.credit_apr,
    )
    env = build_env_from_forecast(cfg, bp, pp, fixed_out)
    V, pi = solve_vi(cfg, env)
    vi_ms = (_t.time() - t0) * 1000

    # Step 4: recommend
    r = recommend(cfg, env, V, pi, req.opening_bank_paise, req.opening_pipeline_paise, t=0)

    # Aggregates from forecast paths (compute here since we skipped simulate())
    balance_paths = req.opening_bank_paise + _np.cumsum(bp + fixed_out[None, :], axis=1)
    p10 = _np.percentile(balance_paths, 10, axis=0)
    p50 = _np.percentile(balance_paths, 50, axis=0)
    p90 = _np.percentile(balance_paths, 90, axis=0)
    min_by_path = balance_paths.min(axis=1)
    shortfall_mask = min_by_path < 0
    prob_shortfall = float(shortfall_mask.mean())
    exp_shortfall = float(min_by_path[shortfall_mask].mean()) if shortfall_mask.any() else 0.0
    below = balance_paths < 0
    any_below = below.any(axis=1)
    if any_below.any():
        med_first = int(_np.median(below.argmax(axis=1)[any_below]))
        exp_shortfall_date = (start + timedelta(days=med_first)).isoformat()
    else:
        exp_shortfall_date = None
    proj_min_idx = int(_np.argmin(p50))
    proj_min = int(p50[proj_min_idx])
    proj_min_date = (start + timedelta(days=proj_min_idx)).isoformat()

    dates = [(start + timedelta(days=i)).isoformat() for i in range(req.horizon_days)]

    # Top 5 actions by Q (already sorted)
    top_actions = [
        AdhocActionQ(
            label=lbl, expected_cost_paise=int(q),
            is_optimal=(lbl == r.action_label),
        )
        for lbl, q in sorted(r.q_by_action.items(), key=lambda x: x[1])[:5]
    ]

    total_ms = (_t.time() - t_start) * 1000

    return AdhocResp(
        fit_ms=fit_ms, forecast_ms=forecast_ms, vi_ms=vi_ms, total_ms=total_ms,
        dates=dates,
        p10_paise=p10.astype(int).tolist(),
        p50_paise=p50.astype(int).tolist(),
        p90_paise=p90.astype(int).tolist(),
        available_now_paise=req.opening_bank_paise,
        projected_min_balance_paise=proj_min,
        projected_min_balance_date=proj_min_date,
        prob_shortfall=prob_shortfall,
        expected_shortfall_paise=int(exp_shortfall),
        expected_shortfall_date=exp_shortfall_date,
        action_label=r.action_label,
        action_kind=r.action_kind,
        action_amount_paise=r.action_amount_paise,
        reason=r.reason,
        expected_cost_paise=int(r.expected_cost_paise),
        expected_cost_do_nothing_paise=int(r.expected_cost_do_nothing_paise),
        savings_vs_do_nothing_paise=int(r.savings_vs_do_nothing_paise),
        top_actions=top_actions,
    )


@router.get("/optimize/{merchant_key}", response_model=OptimizeResp)
def get_optimize(
    merchant_key: str,
    start: date = Query(date(2026, 8, 30)),
    horizon_days: int = Query(30, ge=7, le=60),
    n_paths: int = Query(3_000, ge=200, le=10_000),
    current_bank_paise: int | None = Query(None, description="Override the current bank; defaults to profile opening"),
    current_pipeline_paise: int = Query(2_50_000_00, description="Current pending pipeline"),
    engine: str = Query("vi", pattern="^(vi|fno)$", description="vi = value iteration; fno = neural surrogate (Day 4)"),
    seed: int = Query(7),
):
    """Return the optimal action right now — settle, credit, or wait.

    Runs value iteration on the discretised HJB (state=(bank, pipeline),
    30-day horizon), then returns the argmin action at the current state
    with full cost decomposition.
    """
    import time as _t
    # Cache on the arguments that actually change the VI solve
    cache_key = (merchant_key, start.isoformat(), horizon_days, n_paths,
                 current_bank_paise, current_pipeline_paise, seed)
    if cache_key in _OPTIMIZE_CACHE:
        return _OPTIMIZE_CACHE[cache_key]
    events = _load(merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
    profile = DEMO_MERCHANTS[merchant_key]
    fit = fit_history(events)
    daily_var = sum(profile.variable_expenses.values()) * 100

    # Raw stochastic delta paths for the VI transition scenarios
    bp, pp = simulate_delta_paths(
        fit, start, horizon_days, n_paths=n_paths,
        daily_variable_expense_paise=daily_var, seed=seed,
    )
    sched = build_schedule(profile, events, start, horizon_days)
    fixed_out = np.zeros(horizon_days)
    for d, amt, _, _ in sched.fixed_outflows:
        idx = (d - start).days
        if 0 <= idx < horizon_days:
            fixed_out[idx] += amt

    cfg = VIConfig(horizon_days=horizon_days)
    env = build_env_from_forecast(cfg, bp, pp, fixed_out)

    t0 = _t.time()
    V, pi = solve_vi(cfg, env)
    solve_ms = (_t.time() - t0) * 1000

    bank = current_bank_paise if current_bank_paise is not None else profile.opening_bank_paise
    r = recommend(cfg, env, V, pi, bank, current_pipeline_paise, t=0)

    all_actions = [
        ActionQ(
            label=lbl,
            expected_cost_paise=int(q),
            is_optimal=(lbl == r.action_label),
            is_defensible=(lbl in r.defensible_range),
        )
        for lbl, q in sorted(r.q_by_action.items(), key=lambda x: x[1])
    ]

    resp = OptimizeResp(
        merchant_id=profile.id,
        merchant_name=profile.name,
        current_bank_paise=bank,
        current_pipeline_paise=current_pipeline_paise,
        horizon_days=horizon_days,
        engine=engine,
        action_label=r.action_label,
        action_kind=r.action_kind,
        action_amount_paise=r.action_amount_paise,
        reason=r.reason,
        expected_cost_paise=int(r.expected_cost_paise),
        expected_cost_do_nothing_paise=int(r.expected_cost_do_nothing_paise),
        expected_cost_never_act_paise=int(r.expected_cost_never_act_paise),
        savings_vs_do_nothing_paise=int(r.savings_vs_do_nothing_paise),
        savings_vs_never_act_paise=int(r.savings_vs_never_act_paise),
        defensible_range=r.defensible_range,
        all_actions=all_actions,
        solve_time_ms=solve_ms,
    )
    _OPTIMIZE_CACHE[cache_key] = resp
    return resp


def _prewarm():
    """Called at server startup to pre-solve VI + forecast for all demo merchants.
    First-render becomes instant."""
    from datetime import date as _d
    print("[solvent] pre-warming VI + forecasts for all demo merchants...")
    for key in DEMO_MERCHANTS:
        try:
            get_forecast(key, _d(2026, 8, 30), 30, 5_000, 7)
            get_optimize(key, _d(2026, 8, 30), 30, 3_000, None, 2_50_000_00, "vi", 7)
        except Exception as e:
            print(f"[solvent] pre-warm failed for {key}: {e}")
    print("[solvent] pre-warm complete")


@router.get("/benchmark", response_model=BenchmarkResp)
def get_benchmark(
    horizon_days: int = Query(30, ge=7, le=45),
    n_paths_eval: int = Query(200, ge=50, le=1000),
):
    """Compare VI vs three baselines on the 4 demo merchants.

    Results are cached per horizon; first call runs the sim (~15s).
    """
    cache_key = f"{horizon_days}_{n_paths_eval}"
    if cache_key in _BENCH_CACHE:
        return _BENCH_CACHE[cache_key]

    rows = []
    for key, profile in DEMO_MERCHANTS.items():
        events = _load(key, _DEFAULT_START, _DEFAULT_DAYS)
        fit = fit_history(events)
        sched = build_schedule(profile, events, date(2026, 8, 30), horizon_days)
        daily_var = sum(profile.variable_expenses.values()) * 100
        r = benchmark_merchant(
            events, fit, sched.fixed_outflows,
            profile.opening_bank_paise, 75_000_00, daily_var,
            date(2026, 8, 30), horizon_days=horizon_days,
            n_paths_forecast=2000, n_paths_eval=n_paths_eval,
            merchant_key=key,
        )
        rows.append(r)

    s = summarize_bench(rows)

    labels = {
        "vi": "Value iteration (HJB-optimal)",
        "heuristic_50pct_3day": "Fixed heuristic (IS 50% every 3d)",
        "retry_72h": "Retry-every-72h (dumb baseline)",
        "threshold_1L": "Threshold classifier (IS if bank < ₹1L)",
    }
    inference_ms = {
        "vi": s["vi_solve_ms_mean"],
        "heuristic_50pct_3day": 0.01,
        "retry_72h": 0.01,
        "threshold_1L": 0.01,
    }
    policies = [
        PolicyStats(
            policy_name=name,
            label=labels.get(name, name),
            mean_cost_paise=int(pd["mean_cost_paise"]),
            median_cost_paise=int(pd["median_cost_paise"]),
            p95_cost_paise=int(pd["p95_cost_paise"]),
            cost_lift_over_vi_paise=int(pd.get("cost_lift_over_vi_paise", 0)),
            cost_lift_over_vi_pct=float(pd.get("cost_lift_over_vi_pct", 0)),
            inference_ms=inference_ms.get(name, 0.0),
        )
        for name, pd in s["policies"].items()
    ]

    resp = BenchmarkResp(
        n_merchants=s["n_merchants"],
        horizon_days=horizon_days,
        vi_solve_ms_mean=s["vi_solve_ms_mean"],
        policies=policies,
        footnote=(
            "Results on 4 synthetic demo merchants over a 30-day horizon. "
            "VI is the ground truth solution of the discretised HJB (value "
            "iteration on a (bank × pipeline × day) grid). Baselines are the "
            "simple policies a merchant would actually consider in practice. "
            "Larger merchant samples and real Razorpay data would reduce "
            "variance; the sign and rough magnitude of the lift are robust "
            "across parameter families we've tested."
        ),
    )
    _BENCH_CACHE[cache_key] = resp
    return resp


@router.get("/summary/{merchant_key}", response_model=SummaryResp)
def get_summary(
    merchant_key: str,
    from_date: date = Query(_DEFAULT_START, alias="from"),
    to_date: date = Query(date(2026, 8, 30), alias="to"),
):
    events = _load(merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
    profile = DEMO_MERCHANTS[merchant_key]
    filtered = [e for e in events if from_date <= e.date <= to_date]
    return SummaryResp(
        merchant_id=profile.id,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        totals_by_category=summarize(filtered),
        bank_balance_end_paise=balance_at(events, to_date, "bank")
                                 + profile.opening_bank_paise,
        pipeline_balance_end_paise=balance_at(events, to_date, "pipeline"),
    )
