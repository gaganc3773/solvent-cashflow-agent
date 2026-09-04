/**
 * Typed API client for the Solvent backend.
 *
 * All requests go through /api which the Vite dev server proxies to the
 * FastAPI backend on :8000.
 */

const BASE = '/api'

async function jsonPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`)
  return r.json()
}

async function jsonGet<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`)
  return r.json()
}


// ── Audit log ────────────────────────────────────────────────

export interface AuditEntry {
  action: string
  logged_at: string
  [k: string]: unknown
}
export interface AuditListResp {
  entries: AuditEntry[]
}


// ── Explain (canned + optional LLM) ──────────────────────────

export interface ExplainResp {
  question: string
  answer: string
  evidence: string[][] | null
  engine?: 'deterministic' | 'llm' | 'llm_fallback'
  llm_model?: string | null
  llm_latency_ms?: number | null
  fallback_reason?: string | null
}

export interface LLMStatusResp {
  available: boolean
  models: string[]
  default_model: string
}


// ── Cashflow (Solvent hot path) ──────────────────────────────

export interface CFMerchant {
  key: string
  id: string
  name: string
  sector: string
  opening_bank_paise: number
  payment_intensity: number
}

export interface CFEvent {
  date: string
  amount_paise: number
  category: string
  direction: 'in' | 'out' | 'internal'
  source: string
  source_id: string
  bucket: 'bank' | 'pipeline'
  confidence: number
  metadata: Record<string, unknown>
}

export interface CFEventStream {
  merchant_id: string
  from_date: string
  to_date: string
  events: CFEvent[]
}

export interface CFBalancePoint {
  date: string
  balance_paise: number
}

export interface CFBalanceResp {
  merchant_id: string
  bucket: string
  opening_paise: number
  from_date: string
  to_date: string
  points: CFBalancePoint[]
}

export interface CFSummary {
  merchant_id: string
  from_date: string
  to_date: string
  totals_by_category: Record<string, number>
  bank_balance_end_paise: number
  pipeline_balance_end_paise: number
}

export interface CFKnownOutflow {
  date: string
  amount_paise: number
  category: string
  note: string
}

export interface CFFixedExpense {
  day_of_month: number
  amount_inr: number
  category: string
  note: string
}

export interface CFAdhocReq {
  payment_intensity: number
  avg_ticket_paise: number
  refund_rate: number
  dispute_rate?: number
  opening_bank_paise: number
  opening_pipeline_paise: number
  fixed_expenses: CFFixedExpense[]
  daily_variable_paise: number
  horizon_days?: number
  n_paths?: number
  is_fee_bps?: number
  credit_apr?: number
}

export interface CFAdhocActionQ {
  label: string
  expected_cost_paise: number
  is_optimal: boolean
}

export interface CFAdhocResp {
  fit_ms: number
  forecast_ms: number
  vi_ms: number
  total_ms: number
  dates: string[]
  p10_paise: number[]
  p50_paise: number[]
  p90_paise: number[]
  available_now_paise: number
  projected_min_balance_paise: number
  projected_min_balance_date: string
  prob_shortfall: number
  expected_shortfall_paise: number
  expected_shortfall_date: string | null
  action_label: string
  action_kind: 'nothing' | 'IS' | 'credit'
  action_amount_paise: number
  reason: string
  expected_cost_paise: number
  expected_cost_do_nothing_paise: number
  savings_vs_do_nothing_paise: number
  top_actions: CFAdhocActionQ[]
}

export interface CFPolicyStats {
  policy_name: string
  label: string
  mean_cost_paise: number
  median_cost_paise: number
  p95_cost_paise: number
  cost_lift_over_vi_paise: number
  cost_lift_over_vi_pct: number
  inference_ms: number
}

export interface CFBenchmark {
  n_merchants: number
  horizon_days: number
  vi_solve_ms_mean: number
  policies: CFPolicyStats[]
  footnote: string
}

export interface CFActionQ {
  label: string
  expected_cost_paise: number
  is_optimal: boolean
  is_defensible: boolean
}

export interface CFOptimize {
  merchant_id: string
  merchant_name: string
  current_bank_paise: number
  current_pipeline_paise: number
  horizon_days: number
  engine: string
  action_label: string
  action_kind: 'nothing' | 'IS' | 'credit'
  action_amount_paise: number
  reason: string
  expected_cost_paise: number
  expected_cost_do_nothing_paise: number
  expected_cost_never_act_paise: number
  savings_vs_do_nothing_paise: number
  savings_vs_never_act_paise: number
  defensible_range: string[]
  all_actions: CFActionQ[]
  solve_time_ms: number
}

export interface CFForecast {
  merchant_id: string
  merchant_name: string
  start_date: string
  horizon_days: number
  n_paths: number
  opening_bank_paise: number
  dates: string[]
  p10_paise: number[]
  p50_paise: number[]
  p90_paise: number[]
  mean_paise: number[]
  prob_shortfall: number
  expected_shortfall_paise: number
  expected_shortfall_date: string | null
  available_now_paise: number
  expected_inflow_next_7d_paise: number
  expected_outflow_next_7d_paise: number
  projected_min_balance_paise: number
  projected_min_balance_date: string
  known_outflows: CFKnownOutflow[]
}


// ── Agent (backend runtime; no dedicated UI tab, kept for architecture) ──

export type AgentMode = 'off' | 'advisory' | 'semi_auto' | 'full_auto'

export interface AgentPolicy {
  mode: AgentMode
  min_cash_floor_paise: number
  max_auto_is_per_day_paise: number
  allow_credit_draw: boolean
  max_auto_credit_paise: number
  quiet_hours: [number, number]
}


// ── endpoints ────────────────────────────────────────────────

export const api = {
  // Explain (deterministic + LLM)
  explain:     (key: string, useLlm = false) =>
    jsonPost<ExplainResp>('/explain', { question_key: key, use_llm: useLlm }),
  explainFree: (question: string, useLlm = false) =>
    jsonPost<ExplainResp>('/explain/free', { question, use_llm: useLlm }),
  llmStatus:   () => jsonGet<LLMStatusResp>('/llm/status'),

  // Audit
  audit:       (entry: object)    => jsonPost<{status: string; logged_at: string}>('/audit', entry),
  auditList:   (limit = 20)       => jsonGet<AuditListResp>(`/audit?limit=${limit}`),

  // Cashflow
  cfMerchants: () => jsonGet<CFMerchant[]>('/cashflow/merchants'),

  cfEvents:   (key: string, from?: string, to?: string) =>
    jsonGet<CFEventStream>(
      `/cashflow/events/${key}` +
      (from || to ? `?${from ? `from=${from}` : ''}${from && to ? '&' : ''}${to ? `to=${to}` : ''}` : ''),
    ),

  cfBalance:  (key: string, opts?: { bucket?: 'bank'|'pipeline'; from?: string; to?: string }) =>
    jsonGet<CFBalanceResp>(
      `/cashflow/balance/${key}?` +
      new URLSearchParams({
        bucket: opts?.bucket ?? 'bank',
        ...(opts?.from ? { from: opts.from } : {}),
        ...(opts?.to ? { to: opts.to } : {}),
      }).toString(),
    ),

  cfSummary:  (key: string, from?: string, to?: string) =>
    jsonGet<CFSummary>(
      `/cashflow/summary/${key}` +
      (from || to ? `?${from ? `from=${from}` : ''}${from && to ? '&' : ''}${to ? `to=${to}` : ''}` : ''),
    ),

  cfBenchmark: (horizon_days = 30) =>
    jsonGet<CFBenchmark>(`/cashflow/benchmark?horizon_days=${horizon_days}`),

  cfAdhoc:    (req: CFAdhocReq) => jsonPost<CFAdhocResp>('/cashflow/adhoc', req),

  cfOptimize: (key: string, opts?: { horizon_days?: number; n_paths?: number; current_bank_paise?: number; current_pipeline_paise?: number; engine?: 'vi'|'fno' }) =>
    jsonGet<CFOptimize>(
      `/cashflow/optimize/${key}?` +
      new URLSearchParams({
        horizon_days: String(opts?.horizon_days ?? 30),
        n_paths: String(opts?.n_paths ?? 3000),
        current_pipeline_paise: String(opts?.current_pipeline_paise ?? 7500000),
        engine: opts?.engine ?? 'vi',
        ...(opts?.current_bank_paise !== undefined ? { current_bank_paise: String(opts.current_bank_paise) } : {}),
      }).toString(),
    ),

  cfForecast: (key: string, opts?: { horizon_days?: number; n_paths?: number; start?: string }) =>
    jsonGet<CFForecast>(
      `/cashflow/forecast/${key}?` +
      new URLSearchParams({
        horizon_days: String(opts?.horizon_days ?? 30),
        n_paths: String(opts?.n_paths ?? 5000),
        ...(opts?.start ? { start: opts.start } : {}),
      }).toString(),
    ),
}
