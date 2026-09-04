# Solvent — Autonomous Cashflow Agent for Any Payment Gateway

**Submission for the [Razorpay AI Buildathon 2026](https://razorpay.com/buildathon/), Track 04 (AI Finance Controller).**

Solvent is an autonomous finance-ops agent. It observes a merchant's cash position through a payment-gateway adapter, forecasts the balance path distributionally, decides the optimal action via Hamilton-Jacobi-Bellman value iteration, gates the action against user-set policy, executes it via the gateway API, and reports to an append-only audit trail.

**Gateway-agnostic**: RazorpayAdapter ships today; StripeAdapter is a documented skeleton; adding Cashfree/Adyen/PayPal is a single new adapter file.

**Modes**: off · advisory · semi-auto · full-auto — with per-merchant policy caps (min cash floor, max IS per 24h, allow credit draws, quiet hours).

## Read this first

- [`CONTEXT.md`](./CONTEXT.md) — full pivot history, evidence, design decisions, positioning
- [`PLAN.md`](./PLAN.md) — day-by-day 7-day build plan

## Structure

```
SOLVENT/
├── CONTEXT.md              # single source of truth for decisions
├── PLAN.md                 # 6-day build plan
├── README.md               # this file
├── Solvent_whitepaper.pdf  # comprehensive technical + product document
├── backend/                # FastAPI + NumPy
│   ├── api/                # main.py · cashflow.py · agent.py
│   ├── core/
│   │   ├── cashflow/       # reconstruction · forecast · optimizer (VI) · benchmark · surrogate
│   │   ├── agent/          # AgentRuntime — observe → decide → gate → act → report
│   │   ├── gateway/        # GatewayAdapter Protocol + Razorpay + Stripe skeleton
│   │   ├── llm/            # local Qwen client + grounded explain layer
│   │   └── settlement/     # synthetic Razorpay-shape day generator (used by demo data)
│   ├── data/               # synthetic merchant profiles + audit log
│   └── tests/              # 11 reconstruction invariants
└── frontend/               # React + Vite + Tailwind
    └── src/
        ├── pages/          # CashPosition · CashflowDrilldown · CustomMerchant
        ├── components/     # ExplainDrawer · AuditDrawer · Drawer · Toast
        └── lib/            # api.ts · format.ts
```

## Run locally

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn api.main:app --reload
```

```bash
# Frontend
cd frontend
npm install
npm run dev
```

Backend on `http://localhost:8000`. Frontend on `http://localhost:5173` (Vite proxies `/api` → `:8000`).

## Deadline

**4 September 2026.**
