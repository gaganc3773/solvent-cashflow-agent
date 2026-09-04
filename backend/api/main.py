"""Solvent FastAPI backend — Autonomous Cashflow Agent for Any Payment Gateway.

Endpoints:
  - Cashflow (mounted via cashflow_router at /cashflow/*): merchants, events,
    balance, summary, forecast, optimize, benchmark, adhoc
  - Agent (mounted via agent_router at /agent/*): status, policy, tick,
    simulate, actions, reset
  - Explain: /explain (canned + optional LLM), /explain/free (LLM classify),
    /explain/questions (live-computed labels), /llm/status
  - Audit trail: /audit POST + GET
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, date as _date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.models import (
    ExplainRequest, FreeTextExplainRequest, ExplainResponse, LLMStatus,
)
from api.cashflow import router as cashflow_router
from api.agent import router as agent_router
from core.llm import ollama_client as _ollama
from core.llm.grounded_explain import (
    explain as _grounded_explain,
    classify_free_text,
)


app = FastAPI(title="Solvent", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(cashflow_router)
app.include_router(agent_router)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@app.on_event("startup")
def _startup_prewarm():
    """Pre-solve VI + forecast for demo merchants so first render is instant.
    Also fire a background thread to warm the local LLM if Ollama is up."""
    from api.cashflow import _prewarm
    _prewarm()

    import threading

    def _llm_warm():
        try:
            if _ollama.is_available(timeout_s=2.0):
                r = _ollama.warm()
                if r.ok:
                    print(f"[solvent] LLM warm: {_ollama.DEFAULT_MODEL} ready in {r.latency_ms:.0f}ms")
                else:
                    print(f"[solvent] LLM warm failed: {r.error}")
            else:
                print("[solvent] Ollama not reachable — LLM features will fall back to deterministic")
        except Exception as e:
            print(f"[solvent] LLM warm errored: {e}")

    threading.Thread(target=_llm_warm, daemon=True).start()


@app.get("/")
def root():
    return {"name": "Solvent", "version": "0.3.0", "status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


# ─── INR formatter used by explain builders ─────────────────────────

def _rs(x: float) -> str:
    """Compact INR string. Same glyphs and thresholds as the frontend's
    fmtINRShort, so a number quoted in the drawer is character-identical
    to what the merchant sees on screen."""
    a, sign = abs(x), "−" if x < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f}Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f}L"
    if a >= 1e3:
        return f"{sign}₹{a / 1e3:,.0f}K"
    return f"{sign}₹{a:,.0f}"


# ─── Cashflow explain builders ─────────────────────────────────────
# Every answer is COMPUTED from the same optimizer/forecast the page uses,
# so the drawer can never contradict what's on screen. Nova is the demo
# hero — that's the merchant these questions answer about.

DEMO_MERCHANT_KEY = "nova_streetwear"


def _cf_explain_why_settle():
    """Why does Solvent recommend this action?"""
    from api.cashflow import get_optimize, get_forecast
    opt = get_optimize(DEMO_MERCHANT_KEY, _date(2026, 8, 30), 30, 3_000, None, 2_50_000_00, "vi", 7)
    fc = get_forecast(DEMO_MERCHANT_KEY, _date(2026, 8, 30), 30, 5_000, 7)
    if opt.action_kind == "nothing":
        return {
            "question": "Why did Solvent recommend doing nothing?",
            "answer": (
                f"Your expected balance stays above zero and the fees on any "
                f"Instant Settlement or credit draw would cost more than the "
                f"risk they remove. Expected 30-day cost if you do nothing: "
                f"₹{opt.expected_cost_paise/100:,.0f}."
            ),
            "evidence": [
                ["Bank now", _rs(opt.current_bank_paise / 100)],
                ["Projected min balance", _rs(fc.projected_min_balance_paise / 100)],
                ["Shortfall probability", f"{fc.prob_shortfall*100:.0f}%"],
                ["Do-nothing expected cost", _rs(opt.expected_cost_paise / 100)],
            ],
        }
    fee = int(opt.action_amount_paise * 30 / 10_000) if opt.action_kind == "IS" else 0
    return {
        "question": f"Why did Solvent recommend {opt.action_label}?",
        "answer": (
            f"Because it minimises your expected 30-day total cost — the sum "
            f"of any action fees plus any overdraft cost from running short. "
            f"Doing nothing costs ₹{opt.expected_cost_do_nothing_paise/100:,.0f} "
            f"in expected overdraft; {opt.action_label.lower()} costs ₹{opt.expected_cost_paise/100:,.0f} "
            f"total. Net saving: ₹{opt.savings_vs_do_nothing_paise/100:,.0f}."
        ),
        "evidence": [
            ["Recommendation", opt.action_label],
            ["Expected cost if you act", _rs(opt.expected_cost_paise / 100)],
            ["Expected cost if you don't", _rs(opt.expected_cost_do_nothing_paise / 100)],
            ["Net saving", _rs(opt.savings_vs_do_nothing_paise / 100)],
            (["Immediate fee", _rs(fee / 100)] if fee else ["No immediate fee", "—"]),
        ],
    }


def _cf_explain_shortfall():
    """Where does the shortfall risk come from?"""
    from api.cashflow import get_forecast
    fc = get_forecast(DEMO_MERCHANT_KEY, _date(2026, 8, 30), 30, 5_000, 7)
    ratio = int(fc.prob_shortfall * 100)
    return {
        "question": f"What's driving the {ratio}% shortfall risk?",
        "answer": (
            f"Two forces. Your bank has ₹{fc.available_now_paise/100:,.0f} today. "
            f"Over the next 7 days about ₹{fc.expected_inflow_next_7d_paise/100:,.0f} will "
            f"land from settlements — but ₹{fc.expected_outflow_next_7d_paise/100:,.0f} is "
            f"already committed to payroll, rent, GST and other known bills. "
            f"That's a net outflow before customer traffic gets a chance to cover it. "
            f"Around {fc.expected_shortfall_date}, half the simulated paths dip below zero."
        ),
        "evidence": [
            ["Bank now", _rs(fc.available_now_paise / 100)],
            ["Expected in (7d)", _rs(fc.expected_inflow_next_7d_paise / 100)],
            ["Expected out (7d)", _rs(-fc.expected_outflow_next_7d_paise / 100)],
            ["Projected min", _rs(fc.projected_min_balance_paise / 100)],
            ["Expected shortfall size", _rs(fc.expected_shortfall_paise / 100)],
            ["When", str(fc.expected_shortfall_date or "not projected")],
        ],
    }


def _cf_explain_worst_case():
    """What's the worst case if I do nothing?"""
    from api.cashflow import get_forecast
    fc = get_forecast(DEMO_MERCHANT_KEY, _date(2026, 8, 30), 30, 5_000, 7)
    worst_p10 = min(fc.p10_paise) / 100.0
    return {
        "question": "What's the worst case if I do nothing?",
        "answer": (
            f"In the worst 10% of futures your bank hits ₹{worst_p10:,.0f} "
            f"at its lowest — a real overdraft. That would mean bouncing "
            f"a bill or paying emergency Instant Settlement at a bad time. "
            f"Solvent's recommendation is calibrated to keep even that "
            f"tail case close to zero."
        ),
        "evidence": [
            ["p10 worst balance in horizon", _rs(worst_p10)],
            ["p50 (median) worst", _rs(fc.projected_min_balance_paise / 100)],
            ["p90 (best 10%) worst", _rs(min(fc.p90_paise) / 100.0)],
            ["Chance of any negative day", f"{fc.prob_shortfall*100:.0f}%"],
        ],
    }


def _cf_explain_upcoming_outflows():
    """What bills does Solvent already know about?"""
    from api.cashflow import get_forecast
    fc = get_forecast(DEMO_MERCHANT_KEY, _date(2026, 8, 30), 30, 5_000, 7)
    rows = [
        [f"{o.date} · {o.note}", _rs(-abs(o.amount_paise) / 100)]
        for o in fc.known_outflows
    ]
    total = sum(abs(o.amount_paise) for o in fc.known_outflows) / 100.0
    return {
        "question": "What bills does Solvent already know about?",
        "answer": (
            f"Every calendar-fixed outflow you've told us about is treated as "
            f"certain in the forecast — not stochastic. That's {len(fc.known_outflows)} "
            f"bills totalling {_rs(total)} over the next {fc.horizon_days} days."
        ),
        "evidence": rows + [["Total upcoming", _rs(-total)]],
    }


def _cf_explain_is_worth():
    """Is the IS fee actually worth it?"""
    from api.cashflow import get_optimize
    opt = get_optimize(DEMO_MERCHANT_KEY, _date(2026, 8, 30), 30, 3_000, None, 2_50_000_00, "vi", 7)
    if opt.action_kind != "IS":
        return {
            "question": "Is the recommended action actually worth the fee?",
            "answer": (
                f"Solvent didn't recommend Instant Settlement here — it recommends "
                f"{opt.action_label.lower()}. See 'why' for the reasoning."
            ),
            "evidence": None,
        }
    fee = int(opt.action_amount_paise * 30 / 10_000)
    return {
        "question": "Is the IS fee actually worth paying?",
        "answer": (
            f"Yes for this state. Paying ₹{fee/100:,.0f} in Instant Settlement fee "
            f"buys you ₹{opt.savings_vs_do_nothing_paise/100 + fee/100:,.0f} of "
            f"expected overdraft-cost avoidance, for a net saving of "
            f"₹{opt.savings_vs_do_nothing_paise/100:,.0f}. This is the argmin "
            f"across every action Solvent evaluated."
        ),
        "evidence": [
            ["IS fee to pay", _rs(fee / 100)],
            ["Overdraft cost avoided", _rs((opt.savings_vs_do_nothing_paise + fee) / 100)],
            ["Net saving", _rs(opt.savings_vs_do_nothing_paise / 100)],
            ["Actions Solvent compared", str(len(opt.all_actions))],
            ["Defensible alternatives", f"{len(opt.defensible_range)} within ₹2"],
        ],
    }


EXPLAIN_BUILDERS = {
    "why_settle_now":       _cf_explain_why_settle,
    "whats_the_shortfall":  _cf_explain_shortfall,
    "worst_case":           _cf_explain_worst_case,
    "whats_pending":        _cf_explain_upcoming_outflows,
    "is_this_worth":        _cf_explain_is_worth,
}


# ─── Explain endpoints ──────────────────────────────────────────────

@app.get("/explain/questions")
def explain_questions():
    """The canned questions with their live-computed labels."""
    return {
        "questions": [
            {"key": k, "label": build()["question"]}
            for k, build in EXPLAIN_BUILDERS.items()
        ]
    }


@app.post("/explain", response_model=ExplainResponse)
def explain_endpoint(req: ExplainRequest):
    """Answer a canned question. Set use_llm=true to route the deterministic
    answer through Qwen 2.5 7B (local Ollama) for a warmer paraphrase.
    Every LLM output is numerically validated against the evidence chain;
    on any hallucination or timeout, we return the canned answer."""
    build = EXPLAIN_BUILDERS.get(req.question_key)
    if build is None:
        raise HTTPException(
            status_code=404,
            detail=f"no evidence chain for '{req.question_key}'",
        )
    b = build()
    out = _grounded_explain(
        question=b["question"],
        canned_answer=b["answer"],
        evidence=b.get("evidence") or [],
        use_llm=req.use_llm,
    )
    return ExplainResponse(
        question=out.question,
        answer=out.answer,
        evidence=out.evidence,
        engine=out.engine,
        llm_model=out.llm_model,
        llm_latency_ms=out.llm_latency_ms,
        fallback_reason=out.fallback_reason,
    )


@app.post("/explain/free", response_model=ExplainResponse)
def explain_free_endpoint(req: FreeTextExplainRequest):
    """Free-text explain. When use_llm=true, the LLM classifies the question
    into one of the canned intents; the answer then follows the same
    grounded path as /explain."""
    intent = classify_free_text(req.question) if req.use_llm else "unknown"
    build = EXPLAIN_BUILDERS.get(intent)
    if build is None:
        return ExplainResponse(
            question=req.question,
            answer=(
                "That question doesn't map to any of my evidence chains. "
                "Try one of the suggested questions — each answer comes "
                "back with the transactions behind it."
            ),
            evidence=None,
            engine="deterministic",
        )
    b = build()
    out = _grounded_explain(
        question=b["question"],
        canned_answer=b["answer"],
        evidence=b.get("evidence") or [],
        use_llm=req.use_llm,
    )
    return ExplainResponse(
        question=out.question,
        answer=out.answer,
        evidence=out.evidence,
        engine=out.engine,
        llm_model=out.llm_model,
        llm_latency_ms=out.llm_latency_ms,
        fallback_reason=out.fallback_reason,
    )


@app.get("/llm/status", response_model=LLMStatus)
def llm_status():
    """Return whether the local Ollama LLM is reachable and which models
    are installed. The Explain drawer uses this to decide whether to
    show the LLM toggle as available."""
    available = _ollama.is_available(timeout_s=1.5)
    models = _ollama.list_models() if available else []
    return LLMStatus(
        available=available, models=models, default_model=_ollama.DEFAULT_MODEL,
    )


# ─── Audit log ─────────────────────────────────────────────────────

AUDIT_LOG_PATH = DATA_DIR / "audit_log.jsonl"


@app.post("/audit")
def audit_write(entry: dict):
    """Append a JSONL entry to the audit log."""
    entry = {**entry, "logged_at": datetime.now(timezone.utc).isoformat()}
    AUDIT_LOG_PATH.parent.mkdir(exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return {"status": "logged", "logged_at": entry["logged_at"]}


@app.get("/audit")
def audit_read(limit: int = Query(20, ge=1, le=200)):
    if not AUDIT_LOG_PATH.exists():
        return {"entries": []}
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines[-limit:]]
    entries.reverse()
    return {"entries": entries}
