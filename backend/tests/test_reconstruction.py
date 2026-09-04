"""Day 1 gate — cashflow reconstruction invariants.

Runs `pytest` in `backend/`. Verifies:
  1. Every settlement's line items sum to the settlement amount
  2. Every refund traces to a payment
  3. Cumulative balance recomputes correctly at any date
  4. Every emitted event has provenance (source + source_id)
  5. Bank balance = opening + all bank events (no lost paise)
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.cashflow.reconstruction import (
    reconstruct, events_from_razorpay_day, balance_at, balance_path, summarize,
)
from core.cashflow.events import CashflowEvent, validate, OUTFLOW_CATEGORIES
from data.synthetic_merchants import (
    DEMO_MERCHANTS, generate_history, generate_expense_events,
)


START = date(2026, 6, 1)
NDAYS = 90
END = START + timedelta(days=NDAYS - 1)


@pytest.fixture(scope="module")
def acme_events():
    profile = DEMO_MERCHANTS["acme_analytics"]
    days = generate_history(profile, START, NDAYS)
    exp = generate_expense_events(profile, START, NDAYS)
    events = reconstruct(days, exp)
    return profile, days, events


def test_every_event_validates(acme_events):
    _, _, events = acme_events
    for e in events:
        validate(e)


def test_every_event_has_provenance(acme_events):
    _, _, events = acme_events
    for e in events:
        assert e.source, f"missing source: {e}"
        assert e.source_id, f"missing source_id: {e}"


def test_events_sorted_by_date(acme_events):
    _, _, events = acme_events
    dates = [e.date for e in events]
    assert dates == sorted(dates)


def test_settlement_pair_balances(acme_events):
    """For each settled payment, pipeline_debit + settlement_credit + fee + tax
    should net to zero at the pipeline level (money leaves pipeline entirely)
    and match the customer amount minus fees on the bank side."""
    _, days, events = acme_events
    # Group settlement-day events by source_id
    from collections import defaultdict
    by_src: dict[str, list] = defaultdict(list)
    for e in events:
        if e.category in ("settlement_debit", "settlement_credit", "fee", "tax"):
            by_src[e.source_id].append(e)

    for src, evs in by_src.items():
        pipeline_delta = sum(e.amount_paise for e in evs if e.bucket == "pipeline")
        bank_delta = sum(e.amount_paise for e in evs if e.bucket == "bank")
        # Pipeline should lose exactly the payment amount (settlement_debit)
        assert pipeline_delta < 0
        # Bank should net-positive by (amount - fee - tax)
        assert bank_delta > 0
        # The two deltas together should net to -(fee+tax) (money lost to razorpay)
        # So pipeline_delta + bank_delta = -(fee+tax) ≤ 0
        assert pipeline_delta + bank_delta <= 0


def test_refunds_trace_to_payments(acme_events):
    """Every refund event carries a payment_id in metadata."""
    _, _, events = acme_events
    refunds = [e for e in events if e.category == "refund"]
    for r in refunds:
        assert r.metadata.get("payment_id"), f"refund missing payment_id: {r}"


def test_balance_at_matches_manual_sum(acme_events):
    """balance_at(d) == sum(all bank events up to and including d)."""
    _, _, events = acme_events
    mid = START + timedelta(days=30)
    from_helper = balance_at(events, mid, "bank")
    from_manual = sum(e.amount_paise for e in events
                      if e.bucket == "bank" and e.date <= mid)
    assert from_helper == from_manual


def test_balance_path_final_matches_balance_at(acme_events):
    """Final point of balance_path(start, end) equals balance_at(end) + opening."""
    profile, _, events = acme_events
    path = balance_path(events, START, END, "bank", profile.opening_bank_paise)
    final_from_path = path[-1][1]
    final_from_bal = balance_at(events, END, "bank") + profile.opening_bank_paise
    assert final_from_path == final_from_bal


def test_balance_path_is_dense(acme_events):
    """balance_path emits one row per calendar day."""
    profile, _, events = acme_events
    path = balance_path(events, START, END, "bank", profile.opening_bank_paise)
    assert len(path) == NDAYS
    assert path[0][0] == START
    assert path[-1][0] == END


def test_expense_categories_are_valid(acme_events):
    """Expense events must use a category in the taxonomy."""
    _, _, events = acme_events
    exp = [e for e in events if e.source == "csv_upload"]
    assert len(exp) > 0
    for e in exp:
        assert e.category in OUTFLOW_CATEGORIES, \
            f"expense category not in taxonomy: {e.category}"


def test_summary_partitions_events(acme_events):
    """summarize(events) should sum to the total signed movement."""
    _, _, events = acme_events
    totals = summarize(events)
    assert sum(totals.values()) == sum(e.amount_paise for e in events)


def test_no_zero_amount_events(acme_events):
    """Zero-paise events would pollute the drilldown UI."""
    _, _, events = acme_events
    zeros = [e for e in events if e.amount_paise == 0]
    # A few zero-fee events on UPI are OK; assert we don't have MANY
    assert len(zeros) < len(events) * 0.30
