"""Deterministic cashflow reconstruction.

Given the Razorpay history (list of daily payloads shaped like
`core.settlement.synthetic.generate_day`) and a merchant expense stream,
produce a chronological list of `CashflowEvent`s. Every rupee that ever
moves shows up exactly once and traces back to its source record.

This is the deterministic bottom layer. Forecast and optimizer stack on top.

Balance semantics:
- Pipeline balance = money captured but not yet settled into bank
- Bank balance = money the merchant can actually spend
- Fees & tax are deducted from bank on the settlement date (not on capture)
- Refunds / disputes / adjustments hit bank on their event date
- Expenses hit bank on their due date (from CSV)
- Normal settlement produces a settlement_debit (pipeline -X) and
  settlement_credit (bank +X) on the same date
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .events import CashflowEvent, validate


def _parse_iso_date(s: str) -> date:
    """Accepts either 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SSZ'."""
    return date.fromisoformat(s[:10])


def events_from_razorpay_day(
    day: dict,
    settlement_delay_days: int = 2,
) -> list[CashflowEvent]:
    """Convert one `generate_day`-shaped payload into events.

    A single payment produces two events:
      1. pipeline credit on capture date
      2. pipeline debit + bank credit on settlement date (net of fees)
    Fees & tax are separate bank-debit events on the settlement date,
    so the drilldown UI can show them as their own line.
    """
    events: list[CashflowEvent] = []
    settlement_date = _parse_iso_date(day["settlement_date"])

    for p in day["payments"]:
        capture_dt = _parse_iso_date(p["created_at"])
        # (1) Money enters the Razorpay pipeline on capture
        events.append(CashflowEvent(
            date=capture_dt,
            amount_paise=int(p["amount"]),
            category="payment",
            direction="in",
            source="razorpay",
            source_id=p["entity_id"],
            bucket="pipeline",
            metadata={"method": p.get("method"), "settlement_id": p.get("settlement_id")},
        ))
        # (2) Settlement moves it to bank. Two paired events + fee + tax.
        net = int(p["amount"]) - int(p.get("fee", 0)) - int(p.get("tax", 0))
        events.append(CashflowEvent(
            date=settlement_date,
            amount_paise=-int(p["amount"]),
            category="settlement_debit",
            direction="internal",
            source="razorpay",
            source_id=p["entity_id"],
            bucket="pipeline",
        ))
        events.append(CashflowEvent(
            date=settlement_date,
            amount_paise=net,
            category="settlement_credit",
            direction="in",
            source="razorpay",
            source_id=p["entity_id"],
            bucket="bank",
        ))
        if p.get("fee", 0):
            events.append(CashflowEvent(
                date=settlement_date,
                amount_paise=-int(p["fee"]),
                category="fee",
                direction="out",
                source="razorpay",
                source_id=p["entity_id"],
                bucket="bank",
            ))
        if p.get("tax", 0):
            events.append(CashflowEvent(
                date=settlement_date,
                amount_paise=-int(p["tax"]),
                category="tax",
                direction="out",
                source="razorpay",
                source_id=p["entity_id"],
                bucket="bank",
            ))

    for r in day["refunds"]:
        events.append(CashflowEvent(
            date=_parse_iso_date(r["created_at"]),
            amount_paise=-int(r["amount"]),
            category="refund",
            direction="out",
            source="razorpay",
            source_id=r["entity_id"],
            bucket="bank",
            metadata={"payment_id": r.get("payment_id")},
        ))

    for d_ in day.get("disputes", []):
        events.append(CashflowEvent(
            date=_parse_iso_date(d_["created_at"]),
            amount_paise=-int(d_["amount"]),
            category="dispute",
            direction="out",
            source="razorpay",
            source_id=d_["entity_id"],
            bucket="bank",
            metadata={"payment_id": d_.get("payment_id"),
                      "description": d_.get("description")},
        ))

    for a in day.get("adjustments", []):
        events.append(CashflowEvent(
            date=_parse_iso_date(a["created_at"]),
            amount_paise=-int(a["amount"]),
            category="adjustment",
            direction="out",
            source="razorpay",
            source_id=a["entity_id"],
            bucket="bank",
            metadata={"description": a.get("description")},
        ))

    # Pending — captured this cycle but not yet settled. The base generator
    # only surfaces them as a pipeline credit on their capture date; here we
    # also emit their expected settlement pair `settlement_delay_days` later,
    # flagged confidence < 1.0 so downstream can distinguish observed vs
    # projected. Without this the pipeline balance would grow unboundedly
    # over a multi-day history.
    for p in day.get("pending", []):
        capture_dt = _parse_iso_date(p["created_at"])
        expected_settle = capture_dt + timedelta(days=settlement_delay_days)
        events.append(CashflowEvent(
            date=capture_dt,
            amount_paise=int(p["amount"]),
            category="payment",
            direction="in",
            source="razorpay",
            source_id=p["entity_id"],
            bucket="pipeline",
            metadata={"method": p.get("method"), "settled": False,
                      "expected_settlement": expected_settle.isoformat()},
        ))
        net = int(p["amount"]) - int(p.get("fee", 0)) - int(p.get("tax", 0))
        events.append(CashflowEvent(
            date=expected_settle,
            amount_paise=-int(p["amount"]),
            category="settlement_debit",
            direction="internal",
            source="razorpay",
            source_id=p["entity_id"],
            bucket="pipeline",
            confidence=0.95,
        ))
        events.append(CashflowEvent(
            date=expected_settle,
            amount_paise=net,
            category="settlement_credit",
            direction="in",
            source="razorpay",
            source_id=p["entity_id"],
            bucket="bank",
            confidence=0.95,
        ))
        if p.get("fee", 0):
            events.append(CashflowEvent(
                date=expected_settle,
                amount_paise=-int(p["fee"]),
                category="fee",
                direction="out",
                source="razorpay",
                source_id=p["entity_id"],
                bucket="bank",
                confidence=0.95,
            ))
        if p.get("tax", 0):
            events.append(CashflowEvent(
                date=expected_settle,
                amount_paise=-int(p["tax"]),
                category="tax",
                direction="out",
                source="razorpay",
                source_id=p["entity_id"],
                bucket="bank",
                confidence=0.95,
            ))

    return events


def events_from_expense_csv(csv_path: str | Path) -> list[CashflowEvent]:
    """Read expense CSV. Expected columns: date, amount_inr, category, note.

    `amount_inr` may be integer rupees; converted to paise here. `category`
    should map to one of the `expense_*` entries in `events.OUTFLOW_CATEGORIES`.
    """
    events: list[CashflowEvent] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            cat = row["category"].strip().lower()
            if not cat.startswith("expense_"):
                cat = "expense_" + cat
            amount_paise = int(round(float(row["amount_inr"]) * 100))
            events.append(CashflowEvent(
                date=date.fromisoformat(row["date"].strip()),
                amount_paise=-abs(amount_paise),
                category=cat,
                direction="out",
                source="csv_upload",
                source_id=f"csv:{Path(csv_path).name}:{i}",
                bucket="bank",
                metadata={"note": row.get("note", "").strip()},
            ))
    return events


def reconstruct(
    razorpay_days: Iterable[dict],
    expense_events: Iterable[CashflowEvent] = (),
    settlement_delay_days: int = 2,
) -> list[CashflowEvent]:
    """Merge Razorpay history and merchant expenses into a canonical stream.

    Sorted by (date, bucket, category) so downstream consumers can do
    deterministic cumulative sums. All events go through `validate` so
    schema drift shows up as a loud error, not silent wrong numbers.
    """
    events: list[CashflowEvent] = []
    for day in razorpay_days:
        events.extend(events_from_razorpay_day(day, settlement_delay_days))
    events.extend(expense_events)
    for e in events:
        validate(e)
    events.sort(key=lambda e: (e.date, e.bucket, e.category, e.source_id))
    return events


# ─── Balance helpers ────────────────────────────────────────────────

def balance_at(
    events: Iterable[CashflowEvent],
    at: date,
    bucket: str = "bank",
) -> int:
    """Cumulative signed-sum on `bucket` through and including `at`."""
    return sum(
        e.amount_paise for e in events
        if e.date <= at and e.bucket == bucket
    )


def balance_path(
    events: list[CashflowEvent],
    start: date,
    end: date,
    bucket: str = "bank",
    opening_paise: int = 0,
) -> list[tuple[date, int]]:
    """Return (date, balance) for every date in [start, end].

    Dates without events still emit a row so callers can plot without gaps.
    """
    # Aggregate per-day movement first (O(n)), then scan forward.
    per_day: dict[date, int] = {}
    for e in events:
        if e.bucket != bucket:
            continue
        per_day[e.date] = per_day.get(e.date, 0) + e.amount_paise

    out: list[tuple[date, int]] = []
    running = opening_paise
    # Also include any events strictly before `start` in the opening.
    for d, delta in per_day.items():
        if d < start:
            running += delta

    d = start
    while d <= end:
        running += per_day.get(d, 0) if d >= start else 0
        # Guard: if d == start we already counted pre-start above, but not d==start
        # — fix by only adding when strictly after start OR equal to start:
        # (the condition above already does that). Explicit:
        out.append((d, running))
        d = d + timedelta(days=1)
    return out


def summarize(events: list[CashflowEvent]) -> dict[str, int]:
    """Totals by category — useful for tests and the drilldown headers."""
    totals: dict[str, int] = {}
    for e in events:
        totals[e.category] = totals.get(e.category, 0) + e.amount_paise
    return totals
