"""Parametric synthetic merchants for Solvent.

Wraps `core.settlement.synthetic.generate_day` to produce 90-day histories
per merchant, plus an expense stream. Deterministic per (merchant_id, seed).

A merchant is defined by:
  - id, name, sector
  - payment_intensity: expected captured payments per day
  - avg_ticket_paise: rough transaction size
  - refund_rate: fraction of gross that comes back
  - dispute_rate: chargebacks
  - fixed_expenses: list of (day_of_month, amount_inr, category, note)
  - variable_expenses: dict {category: daily_lambda_inr}
  - opening_bank_paise: starting bank balance
  - is_fee_bps: instant-settlement fee in basis points (Razorpay default 30)
  - credit_apr: overdraft/capital APR
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

# Import from the parent settlement module. The relative path here works
# because backend/ is on sys.path when the API boots (see api/main.py).
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.settlement.synthetic import generate_day  # noqa: E402
from core.cashflow.events import CashflowEvent  # noqa: E402


@dataclass
class MerchantProfile:
    id: str
    name: str
    sector: str
    payment_intensity: float
    avg_ticket_paise: int
    refund_rate: float = 0.03
    dispute_rate: float = 0.005
    opening_bank_paise: int = 500_000_00  # ₹5L
    fixed_expenses: list[tuple[int, int, str, str]] = field(default_factory=list)
    variable_expenses: dict[str, int] = field(default_factory=dict)
    is_fee_bps: int = 30
    credit_apr: float = 0.15


def _scale_day(day: dict, intensity: float, avg_ticket_paise: int,
               refund_rate: float, dispute_rate: float,
               rng: random.Random) -> dict:
    """Nudge a `generate_day` output toward the merchant's parameters.

    The base generator emits ~47 payments at fixed sizes. We resample sizes
    to hit the profile's average ticket, and thin the payment count to hit
    the profile's intensity. Refund and dispute counts are re-drawn.
    """
    target_n = max(1, int(rng.gauss(intensity, intensity * 0.15)))
    payments = day["payments"][:target_n]
    if len(payments) < target_n:
        # Duplicate-with-jitter to reach target_n
        while len(payments) < target_n:
            p = dict(rng.choice(day["payments"]))
            p["entity_id"] = p["entity_id"] + f"_x{len(payments)}"
            payments.append(p)

    for p in payments:
        p["amount"] = int(max(100_00, rng.gauss(avg_ticket_paise, avg_ticket_paise * 0.4)))
        # Refit fee to new amount using stored fee_rate on method
        method = p.get("method", "card")
        fee_rate = {"card": 0.024, "upi": 0.0, "netbanking": 0.015}.get(method, 0.02)
        p["fee"] = int(p["amount"] * fee_rate)
        p["tax"] = int(p["fee"] * 0.18)

    day["payments"] = payments

    # Refunds
    target_refunds = max(0, int(rng.poisson(refund_rate * target_n))) if hasattr(rng, "poisson") \
        else max(0, int(rng.gauss(refund_rate * target_n, 1)))
    day["refunds"] = day["refunds"][:target_refunds] if target_refunds <= len(day["refunds"]) else day["refunds"]

    # Disputes
    target_disputes = 1 if rng.random() < dispute_rate * target_n else 0
    day["disputes"] = day["disputes"][:target_disputes]

    # Adjustments — base generator emits 3/day which is unrealistic when
    # chained across 90 days (~270 adjustments = ₹12L that no real merchant
    # would see). Keep at most 1, with ~15% daily probability (~1/week).
    day["adjustments"] = day["adjustments"][:1] if rng.random() < 0.15 else []

    # Pending — scale roughly with intensity so the pipeline balance
    # depends on merchant size rather than being identical across merchants.
    n_pending_scaled = max(0, int(len(day.get("pending", [])) * (target_n / 47.0)))
    day["pending"] = day["pending"][:n_pending_scaled]

    # Recompute totals
    gross = sum(p["amount"] for p in day["payments"])
    total_fee = sum(p["fee"] for p in day["payments"])
    total_tax = sum(p["tax"] for p in day["payments"])
    total_refund = sum(r["amount"] for r in day["refunds"])
    total_dispute = sum(d_["amount"] for d_ in day["disputes"])
    total_adj = sum(a["amount"] for a in day["adjustments"])
    net = gross - total_fee - total_tax - total_refund - total_dispute - total_adj
    day["totals"] = {
        "gross_paise": gross,
        "fee_paise": total_fee,
        "tax_paise": total_tax,
        "refund_paise": total_refund,
        "dispute_paise": total_dispute,
        "adjustment_paise": total_adj,
        "net_paise": net,
        "pending_paise": sum(p["amount"] for p in day.get("pending", [])),
    }
    day["settlement"]["amount"] = net
    day["settlement"]["fees"] = total_fee
    day["settlement"]["tax"] = total_tax
    return day


def generate_history(
    profile: MerchantProfile,
    start_date: date,
    n_days: int = 90,
    seed: int = 1,
) -> list[dict]:
    """Return a list of daily payloads for the given merchant."""
    rng = random.Random(seed)
    days = []
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        base = generate_day(
            settlement_date=d.isoformat(),
            merchant_id=profile.id,
            seed=seed + i * 7919,
        )
        base["merchant"]["name"] = profile.name
        base["merchant"]["sector"] = profile.sector
        scaled = _scale_day(base, profile.payment_intensity, profile.avg_ticket_paise,
                            profile.refund_rate, profile.dispute_rate, rng)
        days.append(scaled)
    return days


def generate_expense_events(
    profile: MerchantProfile,
    start_date: date,
    n_days: int = 90,
    seed: int = 2,
) -> list[CashflowEvent]:
    """Realise the profile's fixed + variable expense schedule as events."""
    rng = random.Random(seed)
    events: list[CashflowEvent] = []

    # Fixed expenses on their calendar day each month
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        for day_of_month, amount_inr, category, note in profile.fixed_expenses:
            if d.day == day_of_month:
                cat = category if category.startswith("expense_") else f"expense_{category}"
                events.append(CashflowEvent(
                    date=d,
                    amount_paise=-abs(int(amount_inr * 100)),
                    category=cat,
                    direction="out",
                    source="csv_upload",
                    source_id=f"fixed:{profile.id}:{d.isoformat()}:{category}",
                    bucket="bank",
                    metadata={"note": note},
                ))

    # Variable expenses: daily draws
    for i in range(n_days):
        d = start_date + timedelta(days=i)
        for category, daily_lambda_inr in profile.variable_expenses.items():
            if daily_lambda_inr <= 0:
                continue
            amount_inr = max(0, rng.gauss(daily_lambda_inr, daily_lambda_inr * 0.3))
            if amount_inr < 1:
                continue
            cat = category if category.startswith("expense_") else f"expense_{category}"
            events.append(CashflowEvent(
                date=d,
                amount_paise=-int(amount_inr * 100),
                category=cat,
                direction="out",
                source="csv_upload",
                source_id=f"var:{profile.id}:{d.isoformat()}:{category}",
                bucket="bank",
                metadata={},
            ))
    return events


# ─── Canned demo merchants ──────────────────────────────────────────

DEMO_MERCHANTS: dict[str, MerchantProfile] = {
    # Healthy B2B SaaS — no cash flow stress. Used as the "control" merchant
    # in the demo so judges see Solvent correctly say "you're fine, no action".
    "acme_analytics": MerchantProfile(
        id="mrch_acme001",
        name="Acme Analytics Pvt Ltd",
        sector="B2B SaaS",
        payment_intensity=12.0,
        avg_ticket_paise=25_000_00,
        refund_rate=0.02,
        dispute_rate=0.003,
        opening_bank_paise=12_40_000_00,  # ₹12.4L
        fixed_expenses=[
            (1, 6_50_000, "payroll", "Aug payroll"),
            (5, 1_20_000, "rent", "Office rent"),
            (20, 85_000, "gst", "GST filing"),
            (15, 45_000, "saas", "AWS + tooling"),
        ],
        variable_expenses={"ads": 8_000, "vendor": 4_000},
        is_fee_bps=30,
        credit_apr=0.14,
    ),
    # Nova is the DEMO HERO — new D2C brand, moderate cash burn, big vendor
    # payment on the 15th (inventory bulk), thin operating balance. History
    # is roughly breakeven; the stress in the forecast window comes from the
    # inventory payment landing when opening bank is only ₹2.2L. Designed so
    # the 30-day forecast fan chart clearly dips below zero on the p10 line
    # and gives Solvent something to recommend against.
    "nova_streetwear": MerchantProfile(
        id="mrch_nova002",
        name="Nova Streetwear",
        sector="D2C Fashion",
        payment_intensity=48.0,             # ~48 orders/day
        avg_ticket_paise=2_500_00,          # ₹2,500 avg ticket
        refund_rate=0.10,                    # 10% (fashion is refund-heavy)
        dispute_rate=0.005,
        opening_bank_paise=1_20_000_00,     # ₹1.20L — tight relative to payroll
        fixed_expenses=[
            (1, 3_20_000, "payroll", "Team payroll (8 people)"),
            (5, 1_20_000, "rent", "Studio + warehouse rent"),
            (15, 8_50_000, "vendor", "Monthly inventory bulk"),
            (20, 65_000, "gst", "GST filing"),
        ],
        variable_expenses={"ads": 42_000, "vendor": 8_000, "saas": 4_500},
    ),
    # Moderately stressed D2C — larger but with high burn.
    "monsoon_apparel": MerchantProfile(
        id="mrch_moon003",
        name="Monsoon Apparel D2C",
        sector="D2C Fashion",
        payment_intensity=80.0,
        avg_ticket_paise=2_400_00,
        refund_rate=0.07,
        dispute_rate=0.005,
        opening_bank_paise=6_50_000_00,   # ₹6.5L
        fixed_expenses=[
            (1, 3_20_000, "payroll", "Team payroll"),
            (7, 2_10_000, "rent", "Warehouse rent"),
            (15, 4_20_000, "vendor", "Fabric order"),
            (20, 55_000, "gst", "GST filing"),
        ],
        variable_expenses={"ads": 32_000, "vendor": 8_000},
    ),
    # Kirana — very healthy, small predictable cash flow, low ticket high volume.
    "kirana_essentials": MerchantProfile(
        id="mrch_kir004",
        name="Kirana Essentials",
        sector="Grocery / Kirana",
        payment_intensity=180.0,
        avg_ticket_paise=380_00,
        refund_rate=0.01,
        dispute_rate=0.001,
        opening_bank_paise=1_80_000_00,   # ₹1.8L
        fixed_expenses=[
            (1, 1_40_000, "payroll", "Store staff"),
            (10, 90_000, "vendor", "Distributor payment"),
            (20, 25_000, "gst", "GST filing"),
        ],
        variable_expenses={"vendor": 12_000, "other": 3_000},
    ),
}


if __name__ == "__main__":
    # Smoke test — write one merchant to disk and print its 30-day summary
    from core.cashflow.reconstruction import reconstruct, summarize, balance_at

    p = DEMO_MERCHANTS["acme_analytics"]
    days = generate_history(p, start_date=date(2026, 6, 1), n_days=90)
    exp = generate_expense_events(p, start_date=date(2026, 6, 1), n_days=90)
    events = reconstruct(days, exp)
    totals = summarize(events)
    print(f"merchant: {p.name}  ({len(events)} events)")
    for k, v in sorted(totals.items()):
        sign = "+" if v >= 0 else "-"
        print(f"  {k:30s}  {sign}₹{abs(v)/100:>14,.0f}")
    print(f"bank balance on 2026-08-30: ₹{balance_at(events, date(2026,8,30), 'bank')/100:,.0f}")
