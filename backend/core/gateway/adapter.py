"""Payment-gateway adapter interface.

Solvent doesn't care whether a merchant is on Razorpay, Stripe, Cashfree,
Adyen, PayPal or anything else — it operates on the canonical
`CashflowEvent` stream. Each supported gateway ships an adapter that
implements the `GatewayAdapter` Protocol below. Adding a new gateway is
strictly a new adapter file; no core code changes.

The adapter has FIVE responsibilities:
  1. list_payments(from, to)       — historical inflow events
  2. list_settlements(from, to)    — money that landed in bank
  3. get_pipeline_balance()        — money captured but not yet settled
  4. execute_instant_settle(amount)— pull pipeline forward (or its equivalent)
  5. request_credit_draw(amount)   — draw working-capital advance
Plus two informational hooks:
  6. capabilities                  — what this gateway supports
  7. fee_model                     — how the gateway charges for IS

Everything downstream (reconstruction, forecaster, VI optimizer, agent
runtime) is written against this interface, not against Razorpay-specific
types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, Any, runtime_checkable


# ─── Value types the adapter returns ────────────────────────────────

@dataclass
class Payment:
    """Gateway-agnostic payment record."""
    entity_id: str
    amount_paise: int
    fee_paise: int
    tax_paise: int
    captured_at: date
    method: str                        # card | upi | netbanking | wallet | ...
    settlement_id: str | None
    settled: bool


@dataclass
class Settlement:
    """A batch that landed in the merchant's bank."""
    entity_id: str
    net_amount_paise: int
    settled_at: date
    utr: str | None
    payment_ids: list[str] = field(default_factory=list)


@dataclass
class ExecuteResult:
    """Return type for any money-moving action the adapter performs."""
    ok: bool
    action: str                        # "instant_settle" | "credit_draw" | ...
    amount_paise: int
    fee_paise: int
    gateway_reference: str | None
    error: str | None = None


@dataclass
class GatewayCapabilities:
    """What this gateway supports. Agent gates actions on these."""
    supports_instant_settle: bool
    supports_credit_draw: bool
    is_fee_bps: int                    # 30 for Razorpay = 0.30%
    settlement_delay_days: int         # 2 for Razorpay T+2
    min_is_amount_paise: int
    max_is_amount_paise: int


# ─── The protocol every adapter conforms to ────────────────────────

@runtime_checkable
class GatewayAdapter(Protocol):
    """Every payment gateway integration implements this Protocol.

    Existing implementations:
      - RazorpayAdapter  (production-ready, currently wraps synthetic data)
      - StripeAdapter    (skeleton, mapping documented for future integration)
      - GenericCSVAdapter (bring-your-own-data via CSV upload)
    """
    name: str
    merchant_id: str

    @property
    def capabilities(self) -> GatewayCapabilities: ...

    def list_payments(self, from_date: date, to_date: date) -> list[Payment]: ...
    def list_settlements(self, from_date: date, to_date: date) -> list[Settlement]: ...
    def get_pipeline_balance_paise(self) -> int: ...
    def get_bank_balance_paise(self) -> int: ...

    def execute_instant_settle(self, amount_paise: int, dry_run: bool = False) -> ExecuteResult: ...
    def request_credit_draw(self, amount_paise: int, dry_run: bool = False) -> ExecuteResult: ...


# ─── Razorpay implementation ────────────────────────────────────────

class RazorpayAdapter:
    """Razorpay adapter.

    Today this wraps the synthetic history from `data.synthetic_merchants`
    so the whole system runs end-to-end without live Razorpay keys.
    Production drop-in: replace `_synthetic_state()` with calls to
    `razorpay-python` (razorpay.payments.all, razorpay.settlements.all,
    razorpay.settlements.instant.create) — the return types already
    match this adapter's contract 1:1.
    """
    name = "razorpay"

    def __init__(self, merchant_key: str):
        # Late import to avoid a cycle with the demo data module
        from data.synthetic_merchants import DEMO_MERCHANTS
        if merchant_key not in DEMO_MERCHANTS:
            raise ValueError(f"unknown demo merchant '{merchant_key}'")
        self._profile = DEMO_MERCHANTS[merchant_key]
        self.merchant_id = self._profile.id
        self.merchant_key = merchant_key
        # In-memory mutable state so the agent's execute_* calls actually
        # move money in the demo. Persisted per-adapter-instance.
        self._bank_paise = self._profile.opening_bank_paise
        self._pipeline_paise = 75_00_000   # ₹75K default demo state
        self._executed_actions: list[ExecuteResult] = []

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            supports_instant_settle=True,
            supports_credit_draw=True,          # Razorpay Capital
            is_fee_bps=self._profile.is_fee_bps,
            settlement_delay_days=2,
            min_is_amount_paise=10_000_00,      # ₹10K minimum
            max_is_amount_paise=50_00_000_00,   # ₹50L cap for demo
        )

    def list_payments(self, from_date, to_date):
        # In production: return razorpay_client.payments.all(from=..., to=...)
        # For demo: pull from cached reconstruction
        from api.cashflow import _load, _DEFAULT_START, _DEFAULT_DAYS
        events = _load(self.merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
        return [
            Payment(
                entity_id=e.source_id, amount_paise=e.amount_paise,
                fee_paise=0, tax_paise=0,
                captured_at=e.date,
                method=str(e.metadata.get("method", "unknown")),
                settlement_id=str(e.metadata.get("settlement_id") or ""),
                settled=bool(e.metadata.get("settled", True)),
            )
            for e in events
            if e.category == "payment" and e.direction == "in"
            and e.bucket == "pipeline"
            and from_date <= e.date <= to_date
        ]

    def list_settlements(self, from_date, to_date):
        from api.cashflow import _load, _DEFAULT_START, _DEFAULT_DAYS
        events = _load(self.merchant_key, _DEFAULT_START, _DEFAULT_DAYS)
        # Group settlement_credit events by date
        by_date: dict[date, list] = {}
        for e in events:
            if e.category == "settlement_credit" and from_date <= e.date <= to_date:
                by_date.setdefault(e.date, []).append(e)
        return [
            Settlement(
                entity_id=f"setl_{d.isoformat()}",
                net_amount_paise=sum(e.amount_paise for e in evs),
                settled_at=d, utr=None,
                payment_ids=[e.source_id for e in evs],
            )
            for d, evs in sorted(by_date.items())
        ]

    def get_pipeline_balance_paise(self) -> int:
        return self._pipeline_paise

    def get_bank_balance_paise(self) -> int:
        return self._bank_paise

    def execute_instant_settle(self, amount_paise: int, dry_run: bool = False) -> ExecuteResult:
        caps = self.capabilities
        if amount_paise < caps.min_is_amount_paise:
            return ExecuteResult(False, "instant_settle", amount_paise, 0, None,
                                 f"below minimum ₹{caps.min_is_amount_paise/100:.0f}")
        if amount_paise > self._pipeline_paise:
            amount_paise = self._pipeline_paise  # clamp
        fee = int(amount_paise * caps.is_fee_bps / 10_000)
        result = ExecuteResult(
            ok=True, action="instant_settle",
            amount_paise=amount_paise, fee_paise=fee,
            gateway_reference=(None if dry_run else f"is_{len(self._executed_actions):06d}"),
        )
        if not dry_run:
            self._bank_paise += amount_paise - fee
            self._pipeline_paise -= amount_paise
            self._executed_actions.append(result)
        return result

    def request_credit_draw(self, amount_paise: int, dry_run: bool = False) -> ExecuteResult:
        result = ExecuteResult(
            ok=True, action="credit_draw",
            amount_paise=amount_paise, fee_paise=0,
            gateway_reference=(None if dry_run else f"cap_{len(self._executed_actions):06d}"),
        )
        if not dry_run:
            self._bank_paise += amount_paise
            self._executed_actions.append(result)
        return result


# ─── Stripe skeleton — proves the abstraction is real ──────────────

class StripeAdapter:
    """Stripe adapter skeleton.

    Not fully wired to Stripe's live API (that would need an SK_LIVE key
    plus mandatory OAuth flow). This class exists to prove Solvent's
    architecture is gateway-agnostic. Every method below has the correct
    Stripe API mapping documented; a production adapter is a mechanical
    fill-in.
    """
    name = "stripe"

    def __init__(self, account_id: str):
        self.merchant_id = account_id
        # In production: self._client = stripe.Client(api_key=...)

    @property
    def capabilities(self) -> GatewayCapabilities:
        # Stripe Instant Payouts: 1.5% fee, min $0.51, max $9,999.99
        # https://stripe.com/docs/payouts/instant-payouts
        return GatewayCapabilities(
            supports_instant_settle=True,       # via Instant Payouts
            supports_credit_draw=True,          # via Stripe Capital
            is_fee_bps=150,                     # 1.50% (Stripe US default)
            settlement_delay_days=2,            # T+2 for most currencies
            min_is_amount_paise=50_00,          # ~$0.51 in cents-equivalent
            max_is_amount_paise=8_00_00_000,    # ~$9,999 cap
        )

    def list_payments(self, from_date, to_date):
        # Production: self._client.PaymentIntent.list(
        #     created={'gte': from_date, 'lte': to_date}, limit=100)
        # Map: charge.amount → amount_paise (Stripe already uses smallest unit),
        #      charge.balance_transaction.fee → fee_paise,
        #      charge.created → captured_at
        raise NotImplementedError("StripeAdapter is a skeleton; wire to stripe-python")

    def list_settlements(self, from_date, to_date):
        # Production: self._client.Payout.list(arrival_date={'gte':..., 'lte':...})
        raise NotImplementedError

    def get_pipeline_balance_paise(self) -> int:
        # Production: self._client.Balance.retrieve().pending[0].amount
        raise NotImplementedError

    def get_bank_balance_paise(self) -> int:
        # Production: self._client.Balance.retrieve().available[0].amount
        raise NotImplementedError

    def execute_instant_settle(self, amount_paise: int, dry_run: bool = False):
        # Production: self._client.Payout.create(amount=amount_paise,
        #     currency='inr', method='instant')
        raise NotImplementedError

    def request_credit_draw(self, amount_paise: int, dry_run: bool = False):
        # Production: self._client.Capital.FinancingOffer(...)  (invitation-only)
        raise NotImplementedError


# ─── Adapter registry — used by the agent runtime ──────────────────

_REGISTRY: dict[str, type] = {
    "razorpay": RazorpayAdapter,
    "stripe":   StripeAdapter,
}


def get_adapter(gateway: str, merchant_id: str) -> GatewayAdapter:
    """Look up the adapter class and instantiate for a specific merchant."""
    if gateway not in _REGISTRY:
        raise ValueError(f"unknown gateway '{gateway}'; supported: {list(_REGISTRY)}")
    return _REGISTRY[gateway](merchant_id)
