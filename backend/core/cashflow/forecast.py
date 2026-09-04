"""Distributional forecast of merchant cash position.

Fits a compound-Poisson intensity model to the observed payment stream
(one rate per day-of-week), refits refund fraction and ticket-size
lognormal, and Monte-Carlo simulates N balance paths over an H-day
horizon. Aggregates give:
  - percentile fan (10 / 50 / 90) at each horizon day
  - P(min bank balance < threshold) over the window
  - E[shortfall | shortfall] conditional expected drop
  - expected shortfall date (median first-cross date across paths)

Design choices:
- Vectorised: all N × H draws happen in a few NumPy calls
- Deterministic given `seed`; the demo is reproducible
- Fits from CashflowEvent stream, not raw Razorpay JSON — decoupled
  from the source format
- Payments are new draws (customer traffic); expenses are treated as
  KNOWN in the forecast window (fixed calendar + last-90d avg for
  variable). This is honest: merchants know their payroll date; ad
  spend is a decision, not a random walk.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import numpy as np

from .events import CashflowEvent


# ─── Fitted history model ───────────────────────────────────────────

@dataclass
class HistoryFit:
    """Parameters estimated from observed history.

    All rates and amounts in paise. `intensity_by_dow[i]` is expected
    daily payment count for weekday i (Mon=0..Sun=6). `ticket_lognorm`
    holds (mu, sigma) of log-paise; `refund_fraction` and `dispute_fraction`
    are per-payment probabilities.
    """
    intensity_by_dow: np.ndarray          # shape (7,)
    ticket_lognorm: tuple[float, float]   # (mu, sigma) in log-paise
    refund_fraction: float                # 0..1
    dispute_fraction: float               # 0..1
    fee_rate_avg: float                   # blended MDR, e.g. 0.017
    tax_rate: float                       # 0.18 GST on fee
    settlement_delay_days: int            # T+N


def fit_history(
    events: Iterable[CashflowEvent],
    settlement_delay_days: int = 2,
) -> HistoryFit:
    """Estimate compound-Poisson parameters from observed events."""
    events = list(events)
    # Payments (source of truth for arrivals + ticket size)
    payments = [e for e in events if e.category == "payment" and e.direction == "in"]
    if not payments:
        raise ValueError("no payment events found in history")

    # Daily payment counts, grouped by weekday
    per_day: dict[date, int] = {}
    per_day_amt: dict[date, int] = {}
    for p in payments:
        per_day[p.date] = per_day.get(p.date, 0) + 1
        per_day_amt[p.date] = per_day_amt.get(p.date, 0) + p.amount_paise

    dow_totals = np.zeros(7)
    dow_days = np.zeros(7)
    for d, n in per_day.items():
        dow = d.weekday()
        dow_totals[dow] += n
        dow_days[dow] += 1
    intensity_by_dow = np.where(dow_days > 0, dow_totals / dow_days, 1.0)

    # Ticket lognormal
    amounts = np.array([p.amount_paise for p in payments], dtype=np.float64)
    log_amounts = np.log(np.clip(amounts, 1.0, None))
    mu, sigma = float(log_amounts.mean()), float(max(log_amounts.std(), 0.01))

    # Refund + dispute per-payment fractions
    n_payments = len(payments)
    n_refunds = sum(1 for e in events if e.category == "refund")
    n_disputes = sum(1 for e in events if e.category == "dispute")
    refund_fraction = n_refunds / max(n_payments, 1)
    dispute_fraction = n_disputes / max(n_payments, 1)

    # Blended fee rate from actual fee/gross
    fees = np.array([abs(e.amount_paise) for e in events if e.category == "fee"], dtype=np.float64)
    gross = amounts.sum()
    fee_rate_avg = float(fees.sum() / gross) if gross > 0 else 0.017

    return HistoryFit(
        intensity_by_dow=intensity_by_dow,
        ticket_lognorm=(mu, sigma),
        refund_fraction=refund_fraction,
        dispute_fraction=dispute_fraction,
        fee_rate_avg=fee_rate_avg,
        tax_rate=0.18,
        settlement_delay_days=settlement_delay_days,
    )


# ─── Known future outflows / inflows ────────────────────────────────

@dataclass
class KnownSchedule:
    """Deterministic events the merchant already knows about.

    Fixed expenses (payroll on the 1st, rent on the 5th, GST on the 20th)
    live here as (date, amount_paise, category, note). Variable expenses
    (ads, vendor) can either live here as smoothed daily draws, or be left
    to the stochastic side of the simulation.
    """
    fixed_outflows: list[tuple[date, int, str, str]]           # negative amounts
    already_pipeline: list[tuple[date, int, str]]              # (arrival_date, amount, source_id)


def build_schedule(
    profile,
    events: list[CashflowEvent],
    start: date,
    horizon_days: int,
) -> KnownSchedule:
    """Build known-schedule from the merchant profile and pipeline residue.

    Fixed expenses come from `profile.fixed_expenses`; pipeline residue
    is any settlement_credit whose event date is in [start, start+H) that
    was already emitted by reconstruction (payments captured before start
    that will land during the window).
    """
    fixed: list[tuple[date, int, str, str]] = []
    for i in range(horizon_days):
        d = start + timedelta(days=i)
        for day_of_month, amount_inr, category, note in profile.fixed_expenses:
            if d.day == day_of_month:
                cat = category if category.startswith("expense_") else f"expense_{category}"
                fixed.append((d, -abs(int(amount_inr * 100)), cat, note))

    # Pipeline residue: settlement_credit events (from reconstruction)
    # that fall in the forecast window.
    pipeline: list[tuple[date, int, str]] = []
    end = start + timedelta(days=horizon_days - 1)
    for e in events:
        if e.date < start or e.date > end:
            continue
        if e.category == "settlement_credit" and e.bucket == "bank":
            pipeline.append((e.date, e.amount_paise, e.source_id))

    return KnownSchedule(fixed_outflows=fixed, already_pipeline=pipeline)


# ─── Monte-Carlo simulator ──────────────────────────────────────────

@dataclass
class ForecastResult:
    start_date: date
    horizon_days: int
    n_paths: int
    dates: list[date]                        # length horizon_days
    p10: np.ndarray                          # (H,) 10th percentile bank balance
    p50: np.ndarray                          # (H,) median
    p90: np.ndarray                          # (H,) 90th percentile
    mean: np.ndarray                         # (H,)
    min_by_path: np.ndarray                  # (N,)
    first_shortfall_day: np.ndarray          # (N,) index or -1 if never
    prob_shortfall: float                    # over full horizon, threshold=0
    expected_shortfall_paise: float          # E[min_bank | min_bank<0]
    expected_shortfall_date: date | None     # median first-cross date across paths that shortfall

    def to_json(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "horizon_days": self.horizon_days,
            "n_paths": self.n_paths,
            "dates": [d.isoformat() for d in self.dates],
            "p10_paise": self.p10.astype(int).tolist(),
            "p50_paise": self.p50.astype(int).tolist(),
            "p90_paise": self.p90.astype(int).tolist(),
            "mean_paise": self.mean.astype(int).tolist(),
            "prob_shortfall": float(self.prob_shortfall),
            "expected_shortfall_paise": float(self.expected_shortfall_paise),
            "expected_shortfall_date": (
                self.expected_shortfall_date.isoformat()
                if self.expected_shortfall_date else None
            ),
        }


def simulate_delta_paths(
    fit: HistoryFit,
    start: date,
    horizon_days: int = 30,
    n_paths: int = 5_000,
    daily_variable_expense_paise: int = 0,
    variable_expense_std_frac: float = 0.3,
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (bank_delta_paths, pipe_delta_paths), both shape (n_paths, H).

    These are the *stochastic* daily deltas (settlements landing, refunds,
    disputes, variable expenses on the bank side; new captures minus normal
    settlements on the pipeline side). Fixed calendar outflows and already-
    scheduled pipeline landings are NOT included here — the optimizer adds
    those deterministically.
    """
    rng = np.random.default_rng(seed)
    H = horizon_days
    D = fit.settlement_delay_days
    total = H + D

    dow = np.array([(start + timedelta(days=i)).weekday() for i in range(total)])
    lam = fit.intensity_by_dow[dow]
    counts = rng.poisson(lam[None, :], size=(n_paths, total))

    tot_pay = int(counts.sum())
    mu, sigma = fit.ticket_lognorm
    tickets = rng.lognormal(mean=mu, sigma=sigma, size=max(tot_pay, 1)) if tot_pay > 0 else np.zeros(0)
    gross = np.zeros((n_paths, total), dtype=np.float64)
    flat = counts.reshape(-1)
    offsets = np.concatenate(([0], np.cumsum(flat)[:-1]))
    flat_g = np.zeros(flat.shape, dtype=np.float64)
    for k, (start_idx, n) in enumerate(zip(offsets, flat)):
        if n > 0:
            flat_g[k] = tickets[start_idx:start_idx + n].sum()
    gross = flat_g.reshape(n_paths, total)

    net_settle_mult = 1.0 - fit.fee_rate_avg * (1.0 + fit.tax_rate)
    settle_credits = np.zeros_like(gross)
    settle_credits[:, D:] = gross[:, :total - D] * net_settle_mult

    refunds = fit.refund_fraction * gross
    disputes = fit.dispute_fraction * gross

    # Bank stochastic delta = settlements landing today - refunds - disputes - variable expenses
    bank_delta = settle_credits - refunds - disputes
    if daily_variable_expense_paise > 0:
        var_exp = rng.normal(daily_variable_expense_paise,
                             daily_variable_expense_paise * variable_expense_std_frac,
                             size=(n_paths, total))
        var_exp = np.clip(var_exp, 0, None)
        bank_delta -= var_exp

    # Pipeline stochastic delta = new captures today - normal settlements leaving
    pipe_delta = gross - settle_credits / max(net_settle_mult, 1e-9) * net_settle_mult
    # (equivalently: gross minus the settlement_credit at settle time, but pipe
    # loses the GROSS amount not the NET because fees are deducted at settlement.
    # In our simplified pipeline accounting we treat pipeline outflow = net; the
    # fees materialize as bank debits. Close enough for VI transitions.)
    pipe_delta_out = np.zeros_like(gross)
    pipe_delta_out[:, D:] = gross[:, :total - D]
    pipe_delta = gross - pipe_delta_out

    return bank_delta[:, :H], pipe_delta[:, :H]


def simulate(
    fit: HistoryFit,
    schedule: KnownSchedule,
    opening_bank_paise: int,
    start: date,
    horizon_days: int = 30,
    n_paths: int = 10_000,
    daily_variable_expense_paise: int = 0,
    variable_expense_std_frac: float = 0.3,
    seed: int = 7,
) -> ForecastResult:
    """Simulate N bank-balance paths.

    Payments arrive by Poisson(rate=intensity_by_dow[weekday]) per day, with
    lognormal ticket sizes. Each payment settles `T+delay` days later at
    net-of-fee amount. Refunds and disputes debit bank on payment day.
    Fixed outflows from schedule.fixed_outflows debit on their date.
    Already-known pipeline residue lands on its date.

    Vectorised: builds a (n_paths, horizon+delay) matrix of daily bank
    deltas in a few numpy calls; cumulative sum → paths.
    """
    rng = np.random.default_rng(seed)
    H = horizon_days
    D = fit.settlement_delay_days
    total_days = H + D  # payments captured near end may land after H

    # Weekday index per horizon day
    dow = np.array([(start + timedelta(days=i)).weekday() for i in range(total_days)])
    lam = fit.intensity_by_dow[dow]                          # (total_days,)

    # (n_paths, total_days) payment counts
    counts = rng.poisson(lam[None, :], size=(n_paths, total_days))
    total_payments = counts.sum()

    if total_payments == 0:
        # Degenerate case — no payment traffic. Still simulate expenses.
        gross_by_capture = np.zeros((n_paths, total_days), dtype=np.float64)
    else:
        # Flat draw of all ticket sizes, then split by (path, day) via counts
        mu, sigma = fit.ticket_lognorm
        tickets = rng.lognormal(mean=mu, sigma=sigma, size=total_payments)
        # Assemble gross-by-day per path
        gross_by_capture = np.zeros((n_paths, total_days), dtype=np.float64)
        cursor = 0
        # Small loop — one iter per path, vectorised over its days
        flat_counts = counts.reshape(-1)
        flat_gross = np.zeros(flat_counts.shape, dtype=np.float64)
        offsets = np.concatenate(([0], np.cumsum(flat_counts)[:-1]))
        for i, (start_idx, k) in enumerate(zip(offsets, flat_counts)):
            if k > 0:
                flat_gross[i] = tickets[start_idx:start_idx + k].sum()
        gross_by_capture = flat_gross.reshape(n_paths, total_days)

    # Refunds and disputes: fraction of gross debited on same day
    refunds = fit.refund_fraction * gross_by_capture
    disputes = fit.dispute_fraction * gross_by_capture

    # Bank credits (settlement) land T+D days after capture, net of fee+tax
    net_fee_multiplier = 1.0 - fit.fee_rate_avg * (1.0 + fit.tax_rate)
    settle_credits = np.zeros((n_paths, total_days), dtype=np.float64)
    settle_credits[:, D:] = gross_by_capture[:, :total_days - D] * net_fee_multiplier

    # Bank deltas from stochastic side
    bank_delta = settle_credits - refunds - disputes

    # Add fixed outflows on their dates
    for d, amt, _, _ in schedule.fixed_outflows:
        offset = (d - start).days
        if 0 <= offset < total_days:
            bank_delta[:, offset] += amt

    # Add already-known pipeline residue (deterministic)
    for d, amt, _ in schedule.already_pipeline:
        offset = (d - start).days
        if 0 <= offset < total_days:
            bank_delta[:, offset] += amt

    # Variable expenses (ads, vendor) drawn stochastically
    if daily_variable_expense_paise > 0:
        var_exp = rng.normal(
            loc=daily_variable_expense_paise,
            scale=daily_variable_expense_paise * variable_expense_std_frac,
            size=(n_paths, total_days),
        )
        var_exp = np.clip(var_exp, 0, None)
        bank_delta -= var_exp

    # Cumulative bank balance
    balance_paths = opening_bank_paise + np.cumsum(bank_delta, axis=1)
    balance_paths = balance_paths[:, :H]  # trim tail

    # Aggregates
    p10 = np.percentile(balance_paths, 10, axis=0)
    p50 = np.percentile(balance_paths, 50, axis=0)
    p90 = np.percentile(balance_paths, 90, axis=0)
    mean = balance_paths.mean(axis=0)

    min_by_path = balance_paths.min(axis=1)
    shortfall_mask = min_by_path < 0
    prob_shortfall = float(shortfall_mask.mean())
    if shortfall_mask.any():
        expected_shortfall = float(min_by_path[shortfall_mask].mean())
    else:
        expected_shortfall = 0.0

    # First day balance dips below zero, per path (-1 if never)
    below = balance_paths < 0
    any_below = below.any(axis=1)
    first_shortfall_day = np.where(any_below, below.argmax(axis=1), -1)
    if any_below.any():
        med_day = int(np.median(first_shortfall_day[any_below]))
        expected_shortfall_date = start + timedelta(days=med_day)
    else:
        expected_shortfall_date = None

    dates = [start + timedelta(days=i) for i in range(H)]

    return ForecastResult(
        start_date=start,
        horizon_days=H,
        n_paths=n_paths,
        dates=dates,
        p10=p10, p50=p50, p90=p90, mean=mean,
        min_by_path=min_by_path,
        first_shortfall_day=first_shortfall_day,
        prob_shortfall=prob_shortfall,
        expected_shortfall_paise=expected_shortfall,
        expected_shortfall_date=expected_shortfall_date,
    )
