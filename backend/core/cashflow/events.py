"""Canonical cashflow event schema.

Every downstream layer (forecast, optimizer, drilldown UI) consumes
`CashflowEvent` streams. Reconstruction produces them from Razorpay history
and merchant expense data; forecasting extends the stream forward in time
with events flagged as forecast (confidence < 1.0).

Design constraints:
- Amounts are integer paise (matches Razorpay API; no float rounding drift)
- Every event carries provenance (`source`, `source_id`) so the UI can
  drill from any projected rupee back to the record it came from
- Signed amounts: positive = inflow, negative = outflow. `direction` is
  redundant but kept for filter ergonomics.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Literal, Any


Direction = Literal["in", "out", "internal"]
Source = Literal["razorpay", "csv_upload", "razorpayx", "manual", "forecast"]


@dataclass(frozen=True)
class CashflowEvent:
    """One dated movement of money.

    `amount_paise` is signed: positive credits the merchant, negative debits.
    `bucket` distinguishes the ledger it moves against — 'pipeline' is the
    Razorpay pending-settlement queue, 'bank' is the merchant's current
    account. A normal settlement is TWO events: -X on pipeline, +X on bank
    (same day). An Instant Settlement is the same pair, on a chosen date,
    plus a small negative bank event for the 0.30% fee.
    """
    date: date
    amount_paise: int
    category: str
    direction: Direction
    source: Source
    source_id: str
    bucket: Literal["pipeline", "bank"] = "bank"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d


# ─── Category taxonomy ───────────────────────────────────────────────
# Keep this small and deliberate. The drilldown UI groups by category, so
# adding a new one is a UI change too.

INFLOW_CATEGORIES = {
    "payment",           # customer payment captured
    "settlement_credit", # normal or IS credit into bank
    "credit_draw",       # merchant took working-capital advance
}

OUTFLOW_CATEGORIES = {
    "refund",             # money returned to customer
    "dispute",            # chargeback withheld
    "adjustment",         # reconciliation correction
    "fee",                # razorpay MDR
    "tax",                # GST on the fee
    "is_fee",             # instant-settlement 0.30% charge
    "expense_payroll",    # from CSV
    "expense_rent",
    "expense_ads",
    "expense_saas",
    "expense_gst",
    "expense_vendor",
    "expense_other",
    "credit_repayment",   # capital advance being repaid from settlements
}

INTERNAL_CATEGORIES = {
    "settlement_debit",  # money leaving pipeline into bank (offset of settlement_credit)
}

ALL_CATEGORIES = INFLOW_CATEGORIES | OUTFLOW_CATEGORIES | INTERNAL_CATEGORIES


def validate(event: CashflowEvent) -> None:
    """Raise ValueError if the event is internally inconsistent.

    Kept as a free function so hot paths can skip it; tests and any
    boundary that accepts user input should call it.
    """
    if event.category not in ALL_CATEGORIES:
        raise ValueError(
            f"unknown category {event.category!r}; add it to events.py taxonomy"
        )
    if event.direction == "in" and event.amount_paise < 0:
        raise ValueError(f"'in' event has negative amount: {event}")
    if event.direction == "out" and event.amount_paise > 0:
        raise ValueError(f"'out' event has positive amount: {event}")
    if not (0.0 <= event.confidence <= 1.0):
        raise ValueError(f"confidence must be in [0,1], got {event.confidence}")
    if event.bucket not in ("pipeline", "bank"):
        raise ValueError(f"bucket must be 'pipeline' or 'bank', got {event.bucket!r}")
