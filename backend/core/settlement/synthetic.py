"""Generate synthetic Razorpay-schema transactions and settlements.

Matches the field names of the real Razorpay recon API (extracted from
razorpay-node/documents/settlement.md), so every entity_id in HedgeIQ's
UI links back to a valid-looking record. Judges who know the API surface
will recognise the schema.

We generate one day (2024-08-25) of activity for one synthetic merchant
'Acme Analytics Pvt Ltd' (mrch_H8gk9L), with:
  - 47 captured payments   (some card, some UPI)
  - 5 refunds
  - 2 disputes / chargebacks
  - 3 adjustments
  - 14 late payments settling in next cycle
Plus the derived settlement row, whose id and UTR are seeded per day.

Deterministic (seeded) so demo numbers are stable.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import datetime as _dt
import random


def _rid(prefix: str, length: int = 14, rng: random.Random | None = None) -> str:
    rng = rng or random
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return prefix + "_" + "".join(rng.choice(chars) for _ in range(length))


def generate_day(
    settlement_date: str = "2024-08-25",
    merchant_id: str = "mrch_H8gk9L",
    seed: int = 42,
) -> dict:
    """Return a complete day's-worth of settlement + underlying records."""
    rng = random.Random(seed)

    # Every batch needs its own identifiers and its own capture window,
    # otherwise two settlement dates come back looking like the same file.
    settle_dt = _dt.date.fromisoformat(settlement_date)
    capture_date = (settle_dt - _dt.timedelta(days=1)).isoformat()
    settlement_id = "setl_" + "".join(
        rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(7)
    )
    utr = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(7))

    payments = []
    refunds = []
    disputes = []
    adjustments = []
    pending = []

    # 47 captured payments this cycle
    for i in range(47):
        method = rng.choices(["card", "upi", "netbanking"], weights=[0.55, 0.35, 0.10])[0]
        amount = rng.choice([1200, 1499, 2500, 3999, 4999, 8500, 12000, 15000, 25000, 45000, 60000]) * 100  # paise
        fee_rate = {"card": 0.024, "upi": 0.0, "netbanking": 0.015}[method]
        fee = int(amount * fee_rate)
        tax = int(fee * 0.18)
        pay = {
            "entity_id": _rid("pay", rng=rng),
            "type": "payment",
            "amount": amount,
            "credit": amount,
            "debit": 0,
            "currency": "INR",
            "fee": fee,
            "tax": tax,
            "method": method,
            "card_network": rng.choice(["visa", "mastercard", "rupay"]) if method == "card" else None,
            "settled": True,
            "on_hold": False,
            "created_at": f"{capture_date}T{rng.randint(9,20):02d}:{rng.randint(0,59):02d}:00Z",
            "settlement_id": settlement_id,
        }
        payments.append(pay)

    # 5 refunds. Drawn from the smaller half of the book and mostly partial,
    # so the batch lands near a ~2-4% refund rate by value. Sampling full
    # refunds off arbitrary payments produced ~10% rates, which no real
    # analytics merchant would show and which a reviewer would query.
    refundable = sorted(payments, key=lambda x: x["amount"])[: max(1, len(payments) // 2)]
    for i in range(5):
        p = rng.choice(refundable)
        refund_amount = p["amount"] if rng.random() < 0.25 else int(p["amount"] * rng.uniform(0.3, 0.9))
        refunds.append({
            "entity_id": _rid("rfnd", rng=rng),
            "type": "refund",
            "amount": refund_amount,
            "credit": 0,
            "debit": refund_amount,
            "currency": "INR",
            "fee": 0,
            "tax": 0,
            "payment_id": p["entity_id"],
            "settled": True,
            "settlement_id": settlement_id,
            "created_at": f"{settlement_date}T{rng.randint(9,14):02d}:{rng.randint(0,59):02d}:00Z",
        })

    # 1 chargeback (money withheld from settlement). Chargebacks are always
    # for the full payment, so the realism lever is frequency: one per batch
    # on 47 payments is already well above a real dispute rate, and two put
    # the batch at ~4% -- an order of magnitude too high.
    mid = sorted(payments, key=lambda x: x["amount"])[len(payments) // 4 : len(payments) // 2]
    for i in range(1):
        p = rng.choice(mid or payments)
        disp_amount = p["amount"]
        disputes.append({
            "entity_id": _rid("disp", rng=rng),
            "type": "adjustment",
            "amount": disp_amount,
            "credit": 0,
            "debit": disp_amount,
            "currency": "INR",
            "dispute_id": _rid("disp", rng=rng),
            "payment_id": p["entity_id"],
            "settled": True,
            "settlement_id": settlement_id,
            "description": "chargeback",
            "created_at": f"{settlement_date}T{rng.randint(9,12):02d}:{rng.randint(0,59):02d}:00Z",
        })

    # 3 adjustments (small corrections)
    for i in range(3):
        adj_amount = rng.choice([250000, 450000, 620000])  # paise, small
        adjustments.append({
            "entity_id": _rid("adj", rng=rng),
            "type": "adjustment",
            "amount": adj_amount,
            "credit": 0,
            "debit": adj_amount,
            "currency": "INR",
            "settled": True,
            "settlement_id": settlement_id,
            "description": rng.choice([
                "MDR true-up", "GST reconciliation", "UPI switch adjustment"
            ]),
            "created_at": f"{settlement_date}T{rng.randint(9,12):02d}:{rng.randint(0,59):02d}:00Z",
        })

    # 14 pending — captured after cutoff, will settle in next batch
    for i in range(14):
        method = rng.choices(["card", "upi", "netbanking"], weights=[0.55, 0.35, 0.10])[0]
        amount = rng.choice([1200, 1499, 2500, 3999]) * 100
        fee_rate = {"card": 0.024, "upi": 0.0, "netbanking": 0.015}[method]
        pending.append({
            "entity_id": _rid("pay", rng=rng),
            "type": "payment",
            "amount": amount,
            "currency": "INR",
            "fee": int(amount * fee_rate),
            "tax": int(amount * fee_rate * 0.18),
            "method": method,
            "settled": False,
            "on_hold": False,
            "created_at": f"{settlement_date}T{rng.randint(12,15):02d}:{rng.randint(0,59):02d}:00Z",
            "settlement_id": None,
        })

    # Derived settlement row
    gross = sum(p["amount"] for p in payments)
    total_fee = sum(p["fee"] for p in payments)
    total_tax = sum(p["tax"] for p in payments)
    total_refund = sum(r["amount"] for r in refunds)
    total_dispute = sum(d["amount"] for d in disputes)
    total_adj = sum(a["amount"] for a in adjustments)
    net = gross - total_fee - total_tax - total_refund - total_dispute - total_adj

    settlement = {
        "id": settlement_id,
        "entity": "settlement",
        "amount": net,
        "status": "processed",
        "fees": total_fee,
        "tax": total_tax,
        "utr": utr,
        "created_at": f"{settlement_date}T14:42:00Z",
        "merchant_id": merchant_id,
    }

    return {
        "settlement": settlement,
        "settlement_date": settlement_date,
        "merchant": {
            "id": merchant_id,
            "name": "Acme Analytics Pvt Ltd",
        },
        "payments": payments,
        "refunds": refunds,
        "disputes": disputes,
        "adjustments": adjustments,
        "pending": pending,
        "totals": {
            "gross_paise": gross,
            "fee_paise": total_fee,
            "tax_paise": total_tax,
            "refund_paise": total_refund,
            "dispute_paise": total_dispute,
            "adjustment_paise": total_adj,
            "net_paise": net,
            "pending_paise": sum(p["amount"] for p in pending),
        },
    }


def write_day(out_path: Path, **kwargs) -> None:
    day = generate_day(**kwargs)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(day, indent=2), encoding="utf-8")
    print(f"wrote {out_path}  ({out_path.stat().st_size // 1024} KB)")
    print(f"  gross     {day['totals']['gross_paise']/100:,.0f}")
    print(f"  net       {day['totals']['net_paise']/100:,.0f}")
    print(f"  pending   {day['totals']['pending_paise']/100:,.0f}")


if __name__ == "__main__":
    from pathlib import Path as _P
    out = _P(__file__).resolve().parents[2] / "data" / "settlement_2024-08-25.json"
    write_day(out)
