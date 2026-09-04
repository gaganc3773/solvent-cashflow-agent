# Solvent — Autonomous Cashflow Agent for Any Payment Gateway

**Submission for the [Razorpay AI Buildathon 2026](https://razorpay.com/buildathon/), Track 04 (AI Finance Controller).**

Solvent watches a merchant's cash position on any payment gateway, forecasts where the balance is heading, decides the cheapest action to take today, gates that decision against merchant-set policy, executes it through the gateway API, and logs every step to an auditable trail.

Underneath: a compound-Poisson forecast, a Monte Carlo simulation of the next 30 days, and a Hamilton-Jacobi-Bellman value iteration on a discretised grid. Above it, an optional local LLM (Qwen 2.5 7B via Ollama) that only paraphrases numbers the deterministic engine already produced.

---

## The story — meet Nova

Nova Streetwear is a small D2C fashion brand on Razorpay. Her setup:

| | |
|---|---|
| Orders per day | about 48, averaging ₹2,500 each |
| Refund rate | 10% |
| Bank balance today | ₹1,20,000 |
| Razorpay pipeline (not settled yet) | ₹2,50,000 |
| Payroll (1st of month) | ₹3,20,000 |
| Rent (5th) | ₹1,20,000 |
| Inventory (15th) | ₹8,50,000 |
| GST (20th) | ₹65,000 |

Payday is in two days. She has ₹1.2L in the bank and a ₹3.2L payroll bill. She also has ₹2.5L sitting in her Razorpay pipeline that will settle to her bank over the next T+2 to T+3 days. She *could* pay Razorpay 0.30% to pull that pipeline into her bank right now (Instant Settlement), or wait for it to arrive free.

**What should she do today?**

If she instant-settles too much, she wastes fees. If she doesn't settle enough, payroll bounces, employees notice, her bank calls, and she pays an emergency Instant Settlement at a bad moment — plus the reputational damage.

The right answer isn't obvious, because it depends on the *joint* interaction of:

- how many payments will come in over the next two days (random)
- how many of those get refunded (random)
- when exactly her pipeline settles (partly random, gateway timing)
- what other bills hit before payroll (deterministic but she has to remember them)

**Nova can look at her bank app and her Razorpay dashboard. She cannot compute the joint probability.** That's the gap Solvent fills.

---

## Screenshots

The three pages of the app:

**Cash Position** — the daily dashboard.

![Cash Position tab](docs/screenshots/01_cash_position.png)

**Cashflow Drilldown** — every rupee traceable back to a payment or settlement.

![Cashflow Drilldown tab](docs/screenshots/02_cashflow_drilldown.png)

**Try Your Own Merchant** — cold-start path: move the sliders, click Compute, watch the whole pipeline run live.

![Try your own merchant tab](docs/screenshots/03_try_your_own_merchant.png)

---

## How Solvent answers Nova's question — in five steps

### Step 1 · Reconstruct the cashflow

Everything that ever moves in or out reduces to a single record type — a `CashflowEvent`:

```
date | amount_paise | category | direction (in/out) | source_id | confidence
```

We take Nova's last 90 days of Razorpay history — every payment captured, every refund, every settlement landing in her bank, every fee taken — and rebuild it into this canonical stream. Her expense calendar (payroll, rent, GST, inventory) goes in the same stream, tagged as fixed outflows.

Every rupee has a `source_id` pointing back to a Razorpay `payment_id`, `settlement_id`, or CSV row. Nothing is a black box. That's what the **Cashflow Drilldown** page above renders — a receipt where you can click any line to see the events behind it.

Code: [`backend/core/cashflow/reconstruction.py`](backend/core/cashflow/reconstruction.py)

### Step 2 · Fit the parameters — the compound-Poisson model

Now we need to describe *how Nova's cashflow behaves* — in a way that lets us simulate her future.

Payments don't arrive uniformly. A fashion merchant sees more orders on weekends. A B2B SaaS sees more on Tuesday mornings. So we don't fit one arrival rate — we fit **seven**, one per weekday.

The model:

```
Number of payments on day t  ~  Poisson(λ[weekday(t)])
Size of each payment         ~  Lognormal(μ, σ²)
Total captured on day t      =  sum of those payment sizes
```

That's a **compound Poisson**: a random count of random-sized events. It's the natural fit for payment arrivals — one line item per customer, and the count is what varies day-to-day.

To fit it, we just look at the historical data:

```
λ[Mon] = (payments that arrived on any Monday) / (number of Mondays observed)
λ[Tue] = ...
μ, σ   = mean, std of log(payment_size) across all payments
refund rate    = refunds / payments
dispute rate   = disputes / payments
MDR (fee rate) = total fees / total gross
```

Ten numbers total: 7 weekday intensities, μ, σ, and the fee/refund/dispute rates. That's the compound-Poisson fit — no gradient descent, no PyTorch, just closed-form maximum-likelihood estimators from 90 days of history. Runs in milliseconds.

Code: [`backend/core/cashflow/forecast.py`](backend/core/cashflow/forecast.py) (see `HistoryFit`)

### Step 3 · Simulate 5,000 futures — Monte Carlo

We now have a full statistical description of Nova. To find out where her bank balance is heading, we **simulate**. Not once — five thousand times.

Each simulation is one possible next-30-days story: "on Monday she gets 46 payments totalling ₹1.14L, 4 of them refund, Tuesday she gets 51 payments..." — day by day, sampling from the fitted distributions. We add her deterministic outflows (payroll on the 1st, rent on the 5th...) at their fixed dates and track her bank balance forward.

Result: 5,000 possible balance paths for the next 30 days. Vectorised in NumPy — no Python loop over paths, just three big array operations. Runs in a couple of seconds.

From those 5,000 paths we extract:

- **p10, p50, p90** at each day — the fan chart on the Cash Position page (worst 10% / median / best 10%)
- **Shortfall probability** — what fraction of paths dipped below zero at any point
- **Expected shortfall size** — average of "how far below zero did it go" across paths that dipped
- **Projected minimum balance** — the p50 of the minimum-over-30-days statistic

For Nova: 100% probability of dipping below zero, projected minimum around −₹2.04L on September 1st. That's why the risk banner is red.

Code: [`backend/core/cashflow/forecast.py`](backend/core/cashflow/forecast.py) (see `forecast_balance`)

### Step 4 · Solve for the best action — HJB value iteration

Simulation tells us *what will happen if Nova does nothing*. We want to know *what she should do*.

Every day Nova has a small menu of actions:

- do nothing
- instant-settle some amount `x` (costs `0.30% × x` in fees, moves `x` from pipeline to bank)
- draw credit (moves cash into bank, adds a debt with interest)

Each has a cost right now. Doing nothing has zero cost today, but might blow up tomorrow if payroll bounces. The trade-off is between **cost now** and **expected cost later**. This is a textbook **stochastic impulse control problem** — Miller-Orr 1966, Constantinides 1976 for cash management, extended here with a pipeline dimension.

The value function `V(state, day)` = "the minimum expected total cost from today to end-of-month, given this state, if I play optimally from now on." It satisfies the **Bellman equation**:

```
V(bank, pipeline, day) = min over actions a of [
    immediate_cost(bank, pipeline, a, day)
    + expected value of V(new_bank, new_pipeline, day+1) over the random inflows
]
```

To solve it we **discretise the state**:

- bank ∈ [−₹3L, +₹10L], step ₹20K  → 66 buckets
- pipeline ∈ [₹0, +₹3L], step ₹30K → 11 buckets
- day ∈ [0, 30]                     → 30 steps
- state space: **66 × 11 × 30 ≈ 22,000 states**

Then we solve **backwards** from day 30. At day 30 we set `V = 0` (end of horizon). For day 29, for every state we try every action, look up next-day's `V`, and pick the min. For day 28 the same, using day 29's `V`. And so on down to day 0. That's **value iteration**.

The `E[·]` over random next-day inflows would formally be an integral. We collapse it to a **weighted sum over 3 scenarios per day** — the p10 / p50 / p90 next-day inflow from the Monte Carlo output — with weights 0.25 / 0.50 / 0.25. This keeps the Bellman step a small matrix multiply rather than another simulation loop.

Whole solve runs in about 3 seconds on one CPU per merchant. Once we have `V`, the optimal action *today* is just the argmin at Nova's actual current state.

Code: [`backend/core/cashflow/optimizer_vi.py`](backend/core/cashflow/optimizer_vi.py)

### Step 5 · The recommendation

For Nova today, the optimizer's answer is: **Instant-settle ₹15,000**.

The Cash Position page shows the three numbers that matter:

| | |
|---|---|
| **Optimal play with Solvent** — expected cost this month, if she follows Solvent every day | **₹843** |
| **If she never acts all month** — expected overdraft damage with zero cash management | **₹19,491** |
| **Solvent saves her** | **₹18,648 / month** |

The "never act" baseline is a separate Monte-Carlo rollout of the "do nothing forever" policy on the same fitted parameters — an honest comparison against the counterfactual, not a marketing number.

The recommendation panel also shows every action the HJB considered, ranked by their expected total cost. If two actions come within ₹2 of each other the second is flagged "also defensible" — never pretending the optimizer's tiebreak matters when it doesn't.

---

## Scaling it — why the neural surrogate matters

Three seconds per merchant is fine when there's one merchant. Razorpay serves **five million active merchants**. If we ran the full VI once per merchant per day, that's ~4,200 CPU-hours daily just for cash management. Not viable.

So we train a **Fourier Neural Operator + MLP hybrid** surrogate. It takes the ten merchant parameters (the 7 weekday intensities + μ, σ, fee rate) and directly outputs the value function `V(bank, pipeline, day=0)` — the slice we actually need for today's recommendation.

Serving: **0.06 milliseconds per merchant**. Five orders of magnitude faster than the full solve. The full VI stays as our correctness ground truth — the surrogate is trained against it on ~15 sampled merchants and its recommendations are validated against fresh VI solves before deployment.

Code: [`backend/core/cashflow/surrogate.py`](backend/core/cashflow/surrogate.py) (pure-NumPy MLP), [`backend/core/cashflow/surrogate_fno.py`](backend/core/cashflow/surrogate_fno.py) (PyTorch FNO)

---

## The autonomous agent loop

The optimizer says "instant-settle ₹15,000." Solvent doesn't stop there. Wrapped around the optimizer is the actual agent:

```
observe   →   decide   →   gate   →   act   →   report
```

- **Observe** — pull the current bank + pipeline balance from the gateway adapter
- **Decide** — run the pipeline above, get the recommended action
- **Gate** — check against merchant policy: is auto-execute allowed? within daily IS cap? outside quiet hours? cash floor honored?
- **Act** — either execute the action via the gateway API, or surface it as an advisory
- **Report** — write to an append-only audit trail (state observed, decision made, policy check outcome, execution result)

Four modes: `off` (no ticks), `advisory` (recommendations only, never executes), `semi_auto` (executes only within policy caps), `full_auto` (executes anything policy allows, no ceiling per tick).

**Gateway-agnostic**: everything above talks to a `GatewayAdapter` Protocol with five methods (`list_payments`, `list_settlements`, `get_bank_balance`, `execute_instant_settle`, `request_credit_draw`). RazorpayAdapter is production-ready. StripeAdapter is a skeleton with the exact API mapping commented in. Cashfree, Adyen, PayPal — each is a single new adapter file.

Code: [`backend/core/agent/runtime.py`](backend/core/agent/runtime.py), [`backend/core/gateway/adapter.py`](backend/core/gateway/adapter.py)

---

## What's real, what's synthetic

Being explicit about this because it matters for the pitch:

- **Real**: the math, the code, the benchmark harness, the RazorpayAdapter API mapping (correct against public Razorpay docs), the LLM grounding layer with numeric validator, the audit trail
- **Synthetic**: the merchant data. All demo merchants (Nova and two others) are generated with the same parameter shape a real Razorpay merchant would have, but the numbers are simulated
- **The pilot ask**: replace every synthetic number in this repo with 10 real Razorpay merchants, 3 months, IS + Capital sandbox access

The whole point of building on the exact Razorpay data schema — payment_id, settlement_id, refund events, all of it — is that swapping synthetic for real should be a config change, not a rewrite.

---

## Run locally

Backend needs Python 3.10+; frontend needs Node 18+.

```bash
# Backend — FastAPI on :8000
cd backend
pip install -r requirements.txt
uvicorn api.main:app --reload
```

```bash
# Frontend — Vite on :5173, proxies /api to :8000
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Cash Position tab is the default landing.

**Optional — local LLM for the Explain drawer**: install [Ollama](https://ollama.com) and pull `qwen2.5:7b-instruct-q4_K_M`. Solvent detects it automatically and shows the LLM toggle in the Explain drawer. Every LLM answer goes through a numeric-fidelity validator that regex-extracts currency/percent tokens and checks them against the deterministic answer — if the LLM invents a number, Solvent falls back to the deterministic answer silently.

---

## Deeper reading

- [`CONTEXT.md`](./CONTEXT.md) — full pivot history from HedgeIQ, evidence tables, design decisions, positioning
- [`PLAN.md`](./PLAN.md) — day-by-day build plan
- [`Solvent_whitepaper.pdf`](./Solvent_whitepaper.pdf) — 24-page technical + product writeup

## Structure

```
SOLVENT/
├── CONTEXT.md                # single source of truth for decisions
├── PLAN.md                   # day-by-day build plan
├── README.md                 # this file
├── Solvent_whitepaper.pdf    # comprehensive technical + product document
├── docs/screenshots/         # README screenshots
├── backend/                  # FastAPI + NumPy
│   ├── api/                  # main.py · cashflow.py · agent.py
│   ├── core/
│   │   ├── cashflow/         # reconstruction · forecast · optimizer (VI) · benchmark · surrogate
│   │   ├── agent/            # AgentRuntime — observe → decide → gate → act → report
│   │   ├── gateway/          # GatewayAdapter Protocol + Razorpay + Stripe skeleton
│   │   ├── llm/              # local Qwen client + grounded explain layer
│   │   └── settlement/       # synthetic Razorpay-shape day generator
│   ├── data/                 # synthetic merchant profiles
│   └── tests/                # reconstruction invariants
└── frontend/                 # React + Vite + Tailwind
    └── src/
        ├── pages/            # CashPosition · CashflowDrilldown · CustomMerchant
        ├── components/       # ExplainDrawer · AuditDrawer · Drawer · Toast
        └── lib/              # api.ts · format.ts
```

## Deadline

**4 September 2026.**
