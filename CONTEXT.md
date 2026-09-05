# Solvent — Autonomous Cashflow Agent for Any Payment Gateway

**Submission**: Razorpay AI Buildathon 2026 · Track 04 (AI Finance Controller)
**Deadline**: 4 September 2026
**Codename / folder**: `SOLVENT` (formerly `HIQ`)
**Author**: Gagan C

---

## What Solvent is, in one paragraph

An **autonomous finance-ops agent** that closes the loop: observes a merchant's cash position via any payment-gateway adapter → forecasts the balance path distributionally → decides the optimal action using HJB value iteration → gates against user-set policy → executes via the gateway API → reports to an append-only audit trail. Runs on Razorpay today; the `GatewayAdapter` protocol is drop-in for Stripe, Cashfree, Adyen, PayPal, or any REST-shaped gateway. The neural-operator surrogate serves per-merchant policies in milliseconds at platform scale.

---

## Pivot history — conversation timeline

This folder was originally `HIQ` (HedgeIQ), a treasury / FX-hedging tool. Between 2026-08-25 and 2026-08-30 it was rebuilt around cashflow intelligence. This section carries the reasoning so a fresh context can pick up.

### Phase 1 · HedgeIQ v1 built (Days 1–4)
- FX hedging tool for Razorpay Global merchants receiving USD/EUR/GBP
- Analytical Garman-Kohlhagen pricing (ground truth) + FNO surrogate (research demo)
- Two tabs: FX Risk Explorer + Settlement Decomposer
- Backend: FastAPI + PyTorch; Frontend: React + Vite + Tailwind
- Three-way benchmark: analytical 2.27 μs · CN 4171 μs · FNO batched 0.84 μs (1.19M prices/sec on GPU)
- Multiple critique-rounds fixed: EEFC framing, protective-put semantics, "why FNO" defence, recommendation-as-range

### Phase 2 · External critique (Day 5)
- External research prompt executed on HedgeIQ product-market fit
- Result: **3/10 fit, 4/10 alignment** — Razorpay merchants who receive international payments have currency converted at PAYMENT CREATION, not at settlement, so the FX-risk framing was weak
- Reframed to Multi-Currency EEFC Account holding-period story; still felt strained

### Phase 3 · Search for something stronger (Days 5–6)
Explored: payment-retry HJB optimization ("Rupee Rescue"). Pitched with fabricated benchmark numbers (23% heuristic / 66% VI / 63% FNO). User challenged: "First I need evidence that our FNO HJB works better than other methods → show me evidence, do relative tests, be critical, don't just guess."

Response:
- Retracted the fabricated numbers openly
- Literature check: FNO / neural operators on HJB is **real** (SOC-MartNet up to dim-10K, Neural Hamiltonian Operator, Han-Jentzen deep BSDE, Waterloo decumulation benchmark)
- Payment-retry industry lifts are documented (Recurly 47.6% median, Paddle +20.2%, Cleverbridge 30→65%) but **no academic RCT** for HJB-optimal-vs-classifier gap specifically
- Honest verdict: retry-timing HJB is possible but forced; needs synthetic-data toy to prove; no Razorpay data available

### Phase 4 · The right pivot (Day 6 — this document)
User proposed the intersection: combine Cash-Flow Forecasting + Settlement Decomposer into **Razorpay Cashflow Intelligence**. Key insight — cash-position management is a **classical stochastic impulse control problem** (Constantinides 1976, Miller-Orr), so HJB is genuine here rather than forced.

Research validated:
- **Problem real**: 82% of small businesses fail from cash-flow issues; ₹10.7L cr in Indian MSME receivables delayed; ₹20-25L cr credit gap; 55.5% of Indian SMEs manage WC informally
- **Market huge**: 63M Indian SMBs · India D2C $87.5B (2025) → $322B (2031) · Razorpay serves 10M+ merchants processing $180B/year
- **Razorpay strategically wants this**: Instant Settlement is a paid 0.30% product (they earn on every use); Razorpay Capital repays as % of settlement flow (they NEED cashflow forecasting); 94% bundled retention thesis; Vulcan roadmap explicitly names lending as a target; Agent Studio for "managing payments, recovering revenue, financial operations"
- **Competitive gap clean**: Zoho / Tally / Vyapar / Khatabook — none combine PG data + distributional forecast + stochastic optimization

Folder renamed HIQ → SOLVENT. Empty `07_hedgeiq` scaffold removed.

### Phase 8 · HedgeIQ cleanup (this pass)
User requested removal of all HedgeIQ files after the pivot was proven stable.
Deleted:
- `backend/core/pricing/` (GK analytic + CN solver + FNO surrogate + training)
- `backend/core/fx/` (scenarios, backtest)
- `backend/core/settlement/decomposer.py` (kept `synthetic.py` — needed by Solvent's synthetic-merchant generator)
- `backend/tests/test_gk_pricing.py`, `test_hedge_model.py`, `test_three_way_benchmark.py`
- `backend/data/gk_train_1500.npz`, `settlement_2024-08-*.json`
- `frontend/src/pages/FXRiskExplorer.tsx`, `SettlementDecomposer.tsx`
- `frontend/src/components/HistoricalReplay.tsx`, `EvidenceModal.tsx`
- Top-level `plan/` folder (HedgeIQ LaTeX plan + screenshots)
- All HedgeIQ endpoints from `api/main.py` (/price, /scenarios, /strategy, /recommend, /backtest, /settlement/*, /benchmark)
- All HedgeIQ Pydantic models from `api/models.py`
- All HedgeIQ types + endpoint methods from `frontend/src/lib/api.ts`

Also removed the Agent tab from the frontend (backend `AgentRuntime` + endpoints kept for architecture story). Three tabs remain: Cash Position · Cashflow Drilldown · Try your own merchant. Backend endpoints go from ~30 to 12. Tests still pass (11/11). Solvent build is now fully de-HedgeIQ'd.

### Phase 7 · LLM layer added (with numeric-fidelity guardrails)
User pushed back on the "no AI" perception. Added a local Qwen 2.5 7B (Ollama) as an OPTIONAL layer above the deterministic Explain drawer:
- **`backend/core/llm/ollama_client.py`**: thin HTTP wrapper, warm-up hook, timeout handling
- **`backend/core/llm/grounded_explain.py`**: builds evidence-grounded prompts, extracts numeric tokens from LLM output, validates against evidence with unit-normalisation (₹14K = ₹14,000 = 14000), falls back to deterministic answer on any drift, timeout, or unavailability
- **Prompt design**: system prompt forbids inventing numbers, forbids adding advice, caps response at 3-5 sentences; user prompt sends the FACTS + GROUND TRUTH answer for grounded rewriting
- **Number validator**: regex extraction of currency/percent/date tokens; sign-agnostic normalisation to canonical rupee integers; year-token whitelist so "September 2026" doesn't trigger fallback
- **Free-text classification path**: LLM routes arbitrary questions to the closest canned intent
- **/explain**: `use_llm=true` opt-in flag; **/explain/free**: LLM-classified free-text; **/llm/status**: reports Ollama availability
- **Frontend toggle**: Explain drawer shows "Deterministic mode ↔ LLM · Qwen 2.5 7B (local)". Each answer bubble carries an engine badge (deterministic / grounded / LLM fell back)
- **Startup warm** in background thread so first user click doesn't pay the 15-30s model-load cost
- **Zero LLM in the money-moving path** — LLM is Explain drawer only. Optimizer, agent runtime, forecaster all stay deterministic

Empirically: 10/10 calls succeed after normalisation fix (avg ~3.2s per call), fallback correctly triggers when LLM invents a number.

### Phase 6 · Agent + gateway-agnostic transformation (final push)
Response to "this looks like an unpolished tool, not a whole package." Rebuilt Solvent as an autonomous agent with a gateway-agnostic adapter layer:
- **`GatewayAdapter` protocol** ([backend/core/gateway/adapter.py](backend/core/gateway/adapter.py)): five-method interface (list_payments, list_settlements, get_pipeline_balance, get_bank_balance, execute_instant_settle, request_credit_draw) + capabilities. RazorpayAdapter is production-ready; StripeAdapter is a skeleton with the exact API mapping commented for future integration
- **`AgentRuntime`** ([backend/core/agent/runtime.py](backend/core/agent/runtime.py)): observe → decide → gate → act → report loop. Four modes: off / advisory / semi-auto / full-auto. Per-merchant policy caps (min_cash_floor, max_auto_is_per_day, allow_credit_draw, quiet_hours)
- **Agent endpoints** ([backend/api/agent.py](backend/api/agent.py)): `/agent/status`, `/agent/policy`, `/agent/tick`, `/agent/simulate?days=N`, `/agent/actions`, `/agent/reset`
- **Agent tab in the UI** — mode toggle, policy config sliders, "Run one tick" + "Simulate 7 days" controls, reverse-chrono activity feed with per-tick EXECUTED/ADVISED/BLOCKED/IDLE badges. **Removed from the frontend in Phase 8** to keep the pitch surface at three tabs; backend runtime + endpoints stay live at `/agent/*` and can be driven with `curl` or Postman for demo
- Track pivoted from 03 (Revenue Recovery — wrong fit) to 04 (AI Finance Controller — perfect match): "Run the books and the cash position ... close one finance-ops loop"

### Phase 5 · Solvent build (Days 1–5 complete; Day 6 = submission)
- **Day 1**: cashflow reconstruction + synthetic merchant generator + 4 API endpoints + 11 tests
- **Day 2**: distributional forecaster (Monte Carlo, compound Poisson fit) + `/cashflow/forecast` + `CashPosition.tsx` with fan chart + Nova tuned to 51% shortfall
- **Day 3**: VI-HJB optimizer (tabular, 3-scenario transitions) + `/cashflow/optimize` + recommendation panel (hero + cost breakdown + Q-values + Execute) + `CashflowDrilldown.tsx`
- **Day 4**: benchmark harness (VI vs 3 baselines, +97% lift vs heuristic, +66% vs classifier) + `/cashflow/benchmark` + MLP surrogate demo (59,000× speedup at inference, val rel-L² still high with 15-sample training)
- **Day 5**: prewarm at startup (10.7s → 0.24s), Explain drawer rewired for 5 cashflow questions with live-computed labels, AuditDrawer relabeled, "At Razorpay scale" callout (₹9.3cr/year platform framing)

See `PLAN.md` for full day-by-day breakdown.

---

## Evidence table for the pivot

| Claim | Source |
|---|---|
| 82% of SMBs fail from cash flow, not demand | [SMBcompass 2026](https://www.smbcompass.com/small-businesses-fail-cash-flow-data/) |
| Indian MSMEs owed ₹10.7L cr in delayed receivables | [CA India working-capital analysis](https://www.caindelhiindia.com/blog/working-capital-crunch-hidden-challenge-slowing-indian-msme/) |
| ₹20-25L cr MSME credit gap (UK Sinha Committee) | [CA Club India](https://www.caclubindia.com/articles/working-capital-management-for-indian-smes-why-cash-flow-kills-more-businesses-than-losses-55737.asp) |
| 55.5% of Indian SMEs manage WC informally; 81.1% want cash budgets | [Academia SME WCM study](https://www.academia.edu/118060843/Working_capital_management_evidence_from_Indian_SMEs) |
| 63M SMBs in India; 60% lack easy loan access | [Contrary Research on Razorpay](https://research.contrary.com/company/razorpay) |
| Razorpay's own words: "digital transactions take 2–5 days to settle… leads to working capital constraints" | [Razorpay blog](https://razorpay.com/blog/keep-your-digital-cash-register-ringing-with-razorpay-instant-settlements/) |
| India D2C: $87.5B (2025) → $322B (2031), 24.3% CAGR | [Mordor Intelligence](https://www.mordorintelligence.com/industry-reports/india-d2c-ecommerce-market) |
| Razorpay Instant Settlement priced at 0.30% per use | [Razorpay IS blog](https://razorpay.com/blog/instant-settlement-payment-gateway/) |
| Razorpay Cash Advance repays as % of daily settlements | [PYMNTS 2020](https://www.pymnts.com/news/b2b-payments/2020/razorpay-introduces-cash-advance-provide-instant-smb-loans/) |
| Vulcan foundation model (Aug 2026) — "every decision, from routing to fraud to **lending**" | [AWS press release](https://press.aboutamazon.com/aws-international/2026/8/razorpay-launches-vulcan-indias-first-ai-payments-foundation-model-fueled-by-nvidia-and-aws-re-architecting-payments-for-a-350-bn-e-comm-future-by-2030) |
| Razorpay 94% merchant retention from bundled products | Contrary Research |

Adoption math for pitch: 5% of 10M merchants × ₹5L avg settlement × 0.30% IS fee × 12 months ≈ **₹90 crore/year** additional IS revenue directly attributable to a cashflow tool surfacing "settle now."

---

## Problem statement (for the pitch deck)

An Indian D2C / SaaS / subscription merchant on Razorpay lives with money coming in at random times (payments, subscription renewals, some of which will refund) and going out at fixed times (payroll on the 1st, GST on the 20th, ad spend continuously). Razorpay's settlement rail delays receivables by T+2 to T+3 by default. Instant Settlement can pull them forward for 0.30%; a bank overdraft costs 12–18% APR.

Merchants currently manage this on gut feel — 55.5% do it informally. They routinely (a) hold too much idle cash "just in case," forgoing yield, or (b) get surprised by a shortfall and either bounce something or pay a bad-price emergency IS fee.

**Pain in one sentence**: *"I don't know if I'll be short next Tuesday, and if I am, I don't know whether it's cheaper to pull my Razorpay balance forward now or wait."*

---

## Solution architecture

```
Razorpay data (payments, settlements, refunds, subscriptions)
       + merchant expense calendar (CSV / RazorpayX / manual)
              ↓
   [1] Cashflow reconstruction — deterministic; every rupee traced
              ↓
   [2] Forecasting engine — distributional forecast of balance path
              ↓
   [3] Optimization — stochastic control chooses actions
              ↓
   Recommendation with explanation + one-click action
```

**Two UI surfaces**:
- **Cash Position** — KPI hero (available now / expected in / expected out / min projected / shortfall probability / recommended action)
- **Cashflow Drilldown** (evolves from Settlement Decomposer) — every projected rupee traceable back to a payment / settlement / subscription / expense line item

---

## The stochastic control formulation

**State** at time t: `X_t = (cash balance, pending settlements, calendar day)`

**Random processes**:
- Incoming payments — compound Poisson, rate λ(day-of-week, hour) fit from history
- Refunds/failures — thinned payment process with delay distribution
- Fixed outflows — deterministic on their date; stochastic if amount uncertain

**Control**: `a_t ∈ {do nothing, instant-settle amount y, draw credit z}`

**Objective**:
```
min E[ ∫ ( c_shortfall(X_t)^- + c_fee(a_t) + c_credit(z_t) ) dt ]
subject to X_t ≥ 0
```

Value function V(X_t, t) satisfies an HJB equation. State dim 2–3 → tabular VI solves it in seconds per merchant.

---

## HJB / FNO positioning — honest rating

| Component | Honesty | Note |
|---|---|---|
| HJB / stochastic control formulation | **10/10** | Textbook cash management |
| Per-merchant value iteration ground truth | **10/10** | Tabular, correct, fast enough offline |
| Neural operator surrogate across merchant parameters | **6-7/10** | Legitimate: input = (λ(t), c(t), θ) as functions; output = V(x,t). Deep BSDE / PINN arguably more natural than FNO; we use FNO for research continuity. Earns its keep at Razorpay platform scale, not per merchant. |
| "FNO is required for correctness" | **2/10 — don't say this** | VI works. FNO gives real-time per-merchant policy updates when parameters change. |

**How to pitch it**: *"The optimization is classical stochastic control — solvable per merchant by value iteration. Our contribution is a neural-operator surrogate trained across merchant parameter families so we can serve a fresh policy in milliseconds when a merchant's payment pattern or expense schedule changes — no per-merchant PDE re-solve."*

---

## Locked design decisions

1. **Product name**: Solvent. Full brand: "Solvent — Cashflow Intelligence for Razorpay merchants."
2. **Track**: Track 04 (AI Finance Controller) — reframed as cashflow intelligence
3. **Ground-truth optimizer**: tabular value iteration on discretized (cash, day) grid
4. **Research differentiator**: FNO surrogate across a synthetic merchant-parameter family
5. **Expense data source (demo)**: pre-seeded merchant + optional CSV upload — no vaporware "accounting integration"
6. **Positioning**: complement to Razorpay Capital + Instant Settlement, not replacement
7. **Recommendation format**: always as a range with abstention when constraints incompatible (carried over from HedgeIQ)
8. **No LLM in the hot path**: Explain drawer uses fixed evidence chains computed from the same engine as the numbers on screen
9. **Audit trail**: every merchant action logged JSONL, viewable in the Records drawer
10. **Backend contract**: FastAPI + Pydantic; all numbers Pydantic-validated; deterministic seeds for demo reproducibility

---

## What we KEEP from HedgeIQ (~60% reuse)

**Backend infrastructure** (all kept):
- `backend/api/main.py` FastAPI scaffold + CORS + audit endpoint
- Pydantic `models.py` patterns (rewritten with cashflow types)
- `backend/tests/` harness
- `backend/data/` folder for synthetic + generated data

**Frontend infrastructure** (all kept):
- Vite + React + TypeScript scaffold
- Tailwind config (brass amber palette, Instrument Serif + IBM Plex)
- `App.tsx` two-tab shell (rebranded, tabs relabeled)
- `EvidenceModal`, `ExplainDrawer`, `AuditDrawer`, `Toast` components
- `lib/api.ts` typed API client pattern
- `lib/format.ts` INR paise formatting

**Directly repurposed as-is**:
- `SettlementDecomposer.tsx` → becomes **Cashflow Drilldown** (same receipt UX, extended forward in time)
- `backend/core/settlement/synthetic.py` → generalized to synthetic merchant day-stream
- `backend/core/settlement/decomposer.py` → generalized to any cashflow event

**Retained but hidden** (research demo, off critical path):
- `backend/core/pricing/` — GK + FNO surrogate (referenced in the pitch as "we have shipped FNO before, here's the next step")
- `plan/plan.tex` HedgeIQ plan — kept in the repo as historical artifact

## What we RETIRE from HedgeIQ

- FX Risk Explorer tab (protective-put math, scenario lab, strategy compare)
- `backend/core/fx/scenarios.py` (recommend_range logic pattern is reused; the exposure semantics are dropped)
- `backend/core/fx/backtest.py` (replay across three FX regimes — not needed)
- Hedge-cost / hedged-PnL utilities

## What we ADD for Solvent (new files)

- `backend/core/cashflow/reconstruction.py` — canonical CashflowEvent stream
- `backend/core/cashflow/forecast.py` — Monte-Carlo balance-path forecaster
- `backend/core/cashflow/optimizer_vi.py` — tabular value iteration ground truth
- `backend/core/cashflow/optimizer_fno.py` — FNO surrogate wrapper
- `backend/core/cashflow/train_fno_hjb.py` — training loop for the merchant-parameter family
- `backend/data/synthetic_merchants.py` — parameterized merchant generator
- `frontend/src/pages/CashPosition.tsx` — replaces FXRiskExplorer
- `frontend/src/pages/CashflowDrilldown.tsx` — replaces SettlementDecomposer

---

## Cut list (what NOT to build — from 8 rounds of prior critique)

- ❌ Real bank integration (out of scope, would need OAuth and consent flow)
- ❌ Real accounting-software integration (Zoho/Tally APIs) — demo uses CSV upload
- ❌ Real Razorpay production API access (demo uses synthetic data structured to their schema)
- ❌ LLM in the hot path (explain drawer uses fixed evidence chains; no external LLM API calls)
- ❌ Mobile app (web-first for demo)
- ❌ Multi-user collaboration features (single-merchant view)
- ❌ Alerts / notifications (Day 7 stretch if time permits)
- ❌ Complex expense categorization ML — demo uses category from CSV column
- ❌ Realtime WebSocket updates (poll-based is fine for demo)
- ❌ Feature flags, A/B testing framework
- ❌ Multi-currency in Solvent (all INR; the FX story is HedgeIQ's, retired)

---

## Cut list — HedgeIQ-era critique lessons that still apply

Carried forward from 8 rounds of external critique on HedgeIQ:

- Don't overclaim FNO is "required" — it's a research demo layered on top of solvable classical methods
- Don't overclaim "nobody else can build this" — position as differentiator, not moat
- Always show recommendations as a range with abstention
- Always trace numbers to source (Evidence drill-down)
- Video must be ≤5 minutes with clear problem→solution→demo→ask flow
- Disclose synthetic-data limitation upfront in the pitch

---

## Deliverables checklist

- [ ] Working web app (Cash Position + Cashflow Drilldown)
- [ ] Value iteration optimizer (correctness ground truth)
- [ ] FNO surrogate trained across merchant-parameter family
- [ ] Benchmark table: VI vs heuristic vs FNO on 10k synthetic merchants
- [ ] 5-minute pitch video
- [ ] `PLAN.md` (10-day plan, embedded evidence, day-by-day)
- [ ] Public GitHub repo
- [ ] Submission form filled by 4 September 2026

---

## Pointers

- **Day-by-day plan**: [`PLAN.md`](./PLAN.md)
- **Backend API entry**: [`backend/api/main.py`](./backend/api/main.py)
- **Frontend entry**: [`frontend/src/App.tsx`](./frontend/src/App.tsx)
- **Agent runtime**: [`backend/core/agent/runtime.py`](./backend/core/agent/runtime.py) · endpoints [`backend/api/agent.py`](./backend/api/agent.py) · adapters [`backend/core/gateway/adapter.py`](./backend/core/gateway/adapter.py)
- _HedgeIQ historical assets (mockup + plan.pdf) were removed with the rest of the HedgeIQ scaffold in Phase 8_

---

_This document is the single source of truth for the Solvent build. If a decision is not in this file, it is not decided._
