# Solvent — 6-Day Sprint Plan

**Today**: Saturday, 2026-08-30 · **Submission**: Thursday, 2026-09-04
**Days remaining**: 6 including submission day
**Working hours available**: ~40–50 hours (assuming 8h/day incl. Sun)

This is a tight schedule. The plan below is prioritized so that **Day 5 is the last cut-off for scope changes** — anything not working by end of Day 5 is dropped from the demo, not squeezed into Day 6.

---

## Sequencing principle

Build **bottom-up** from data to UI so every layer is testable in isolation before the layer above it depends on it. Each day ends with a demoable checkpoint.

```
Day 1 → deterministic cashflow reconstruction (bottom)
Day 2 → distributional forecast + Cash Position UI skeleton
Day 3 → value iteration optimizer + Drilldown UI
Day 4 → FNO surrogate + benchmark table
Day 5 → explain / audit / polish
Day 6 → video + submit
```

---

## Day 1 — Saturday 2026-08-30 ✅ DONE
### Cashflow reconstruction + synthetic merchant

**Deliverables**:
- `backend/core/cashflow/events.py` — `CashflowEvent` dataclass (date, amount_paise, direction, category, source_id, confidence)
- `backend/core/cashflow/reconstruction.py` — given Razorpay-shaped payment/settlement/refund history + expense CSV, produce a canonical `List[CashflowEvent]` sorted by date
- `backend/data/synthetic_merchants.py` — parameterized merchant generator: takes (λ, expense_schedule, refund_rate, seed) → 90 days of history in Razorpay JSON shape
- `backend/api/cashflow.py` — endpoint `GET /cashflow/events/{merchant_id}?from=&to=` returns the event stream
- Unit test: `test_reconstruction.py` — every settlement's line items sum to its total; every refund traces to its original payment

**Cut**: no forecast yet, no optimizer, no UI changes. Just verify the data plumbing.

**Checkpoint**: `curl /cashflow/events/demo_001?from=2026-06-01&to=2026-08-30` returns clean stream, every event has provenance.

---

## Day 2 — Sunday 2026-08-31 ✅ DONE
### Distributional forecast + Cash Position UI skeleton

**Backend**:
- `backend/core/cashflow/forecast.py`:
  - Fit compound-Poisson intensity λ(day-of-week, hour) from event history
  - Fit refund delay distribution
  - Monte-Carlo simulator: `simulate(n_paths=10000, horizon_days=30) → np.ndarray[n_paths, horizon]` of balance paths
  - Aggregates: `E[balance(t)]`, `P(min balance < threshold)`, `E[shortfall | shortfall]`, expected shortfall date
- `backend/api/cashflow.py`: `POST /cashflow/forecast` endpoint

**Frontend**:
- New `frontend/src/pages/CashPosition.tsx` (replaces `FXRiskExplorer.tsx` in tab 1)
- Hero KPIs: Available now / Expected next 7d in / Expected next 7d out / Projected min balance / Shortfall probability / Expected shortfall date
- Balance path fan chart (10th/50th/90th percentile) — use inline SVG for speed
- Tab label change in `App.tsx`: "FX Risk Explorer" → "Cash Position"

**Checkpoint**: Cash Position page loads, shows KPIs and fan chart for demo merchant. No optimizer output yet.

---

## Day 3 — Monday 2026-09-01 ✅ DONE
### Value iteration + Cashflow Drilldown page

**Backend**:
- `backend/core/cashflow/optimizer_vi.py`:
  - State: `(cash_bucket, day_of_horizon)` on discretized grid
  - Actions: `{do_nothing, IS(amount), draw_credit(amount)}`
  - Reward: `-c_shortfall(x)^- - c_fee(a) - c_credit(z)`
  - Backward VI over 30-day horizon
  - Returns `V(state)`, `π(state)`, and diagnostics
- `POST /cashflow/optimize` endpoint returning: recommended action, expected cost with vs without, defensible range, abstention reason if any

**Frontend**:
- `frontend/src/pages/CashflowDrilldown.tsx` (evolves from `SettlementDecomposer.tsx`)
  - Same receipt UX
  - Extended forward in time (upcoming inflows + outflows itemized)
  - Every line clickable → `EvidenceModal` with source records
- Recommendation panel on `CashPosition.tsx` — shows action + explanation + one-click "Execute" (mocked audit log entry)

**Checkpoint**: End-to-end demo: pick merchant → see forecast → see recommendation → drill into any projected line.

---

## Day 4 — Tuesday 2026-09-02 ✅ DONE
### FNO surrogate + benchmark table

**Backend**:
- `backend/core/cashflow/train_fno_hjb.py`:
  - Sample 1000 synthetic merchants (varied λ, expense calendars, credit costs)
  - For each: solve VI, store `V(state)` on grid
  - Train FNO: input = (λ_profile as function of time, expense schedule as function of time), output = V(state) on grid
  - Target rel-L² < 0.05 on held-out 200 merchants
- `backend/core/cashflow/optimizer_fno.py` — inference wrapper
- Benchmark harness: for 10k held-out merchants, measure:
  - **Fixed heuristic** (settle at day 3 if pending > threshold) — expected total cost
  - **Threshold classifier** (log-reg on features → settle decision) — expected total cost
  - **Value iteration** (optimal) — expected total cost
  - **FNO surrogate** — expected total cost + inference time
- `GET /benchmark/optimizer` endpoint returning the table

**Frontend**:
- "Under the hood" drawer on `CashPosition.tsx` shows the benchmark table live from API
- Metric cards: cost lift over heuristic, throughput of FNO in policies/sec

**Cut here if behind schedule**: skip FNO surrogate, keep VI only. FNO is the research angle but VI alone is enough for the pitch.

**Checkpoint**: Benchmark table has real numbers on synthetic data. All four methods compared honestly.

---

## Day 5 — Wednesday 2026-09-03 ✅ DONE
### Explain drawer + audit + polish + integration

**Backend**:
- Rewrite `EXPLAIN_BUILDERS` in `api/main.py` for cashflow questions:
  - `why_settle_now` — pulls from optimizer output
  - `whats_the_shortfall` — pulls from forecast
  - `whats_pending` — pulls from reconstruction
  - `worst_case` — from forecast distribution
  - `is_this_worth_the_fee` — cost/benefit from optimizer
- `POST /explain` returns fixed evidence chains from the same engine as the numbers on screen

**Frontend**:
- `ExplainDrawer` wired to new questions
- `AuditDrawer` wired to `/audit` — shows merchant's history of viewed evidence, executed recommendations
- Toast on any action
- Polish: skeleton loaders on every fetch, error states, no jank
- Integration test: full demo path from load → forecast → recommendation → execute → audit trail entry

**Cut here if behind schedule**: skip AuditDrawer wiring, leave explain drawer with 3 questions instead of 5.

**Checkpoint**: Full demo runs end-to-end without touching backend logs. Every number on screen traceable.

---

## Day 6 — Thursday 2026-09-04 (SUBMISSION DAY)
### Video + submit

**Morning (4h)**:
- Record 5-minute pitch video (screen capture + voice-over)
- Script structure:
  1. Problem — 82% of SMBs fail from cash flow; 55.5% manage it informally (30s)
  2. Solution demo — Solvent shows Cash Position → shortfall detected → recommendation → drill-down explains → merchant executes (2m30s)
  3. Under the hood — stochastic control formulation + neural operator surrogate benchmarks (1m)
  4. Alignment with Razorpay — IS + Capital cross-sell math + Vulcan roadmap (30s)
  5. Ask — pilot with 10 real merchants for validation (30s)
- Cut and export

**Afternoon (2h)**:
- Push code to public GitHub repo
- Write repo README (already done)
- Fill Buildathon submission form
- Submit

**Buffer (2h)**: contingency for video re-record or submission form snags.

---

## What NOT to do this week (from prior critique lessons)

- Do not add features not in this plan
- Do not touch the retired HedgeIQ FX code (kept in repo but hidden from UI)
- Do not attempt real bank / accounting integration — CSV upload + synthetic merchant only
- Do not put LLM in the hot path — Explain drawer uses fixed evidence chains
- Do not overclaim FNO — it's the research differentiator, VI is the correctness ground truth
- Do not skip the honest disclosure of synthetic data limitations in the pitch video

---

## Risk register

| Risk | Mitigation |
|---|---|
| FNO doesn't converge in one day | Skip FNO; keep VI + honest positioning of "FNO next step" |
| Forecaster produces unrealistic paths | Manually tune synthetic merchant parameters to match Recurly benchmarks |
| Frontend has too much HedgeIQ styling for cashflow story | Retain the brass/serif visual language; only change tab labels + KPI copy |
| Submission form asks for things we don't have (deck? one-pager?) | Reserve 2h Day 6 buffer for this |
| Video recording eats more than 4h | Practice run Day 5 evening; script is written |

---

## Success criteria for submission

- ✅ Working web app deployable locally (backend + frontend both boot)
- ✅ Two functional pages (Cash Position + Cashflow Drilldown)
- ✅ Value iteration optimizer running on synthetic merchant, producing recommendations
- ✅ Explain drawer with ≥3 canned questions returning evidence chains
- ✅ Benchmark table with VI vs heuristic vs classifier (FNO optional)
- ✅ 5-minute pitch video with problem→solution→demo→ask flow
- ✅ Public GitHub repo with clean README
- ✅ Submission form filled by 23:59 IST on 2026-09-04

---

_Live plan — updates as we go._
