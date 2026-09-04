import { useState } from 'react'
import { api, CFAdhocResp, CFFixedExpense } from '../lib/api'
import { fmtINRPaiseFull, fmtINRPaiseShort } from '../lib/format'

const PRESETS: Record<string, {
  label: string
  payment_intensity: number
  avg_ticket_paise: number
  refund_rate: number
  opening_bank_paise: number
  opening_pipeline_paise: number
  daily_variable_paise: number
  fixed_expenses: CFFixedExpense[]
}> = {
  // NOTE: all *_paise fields below are integer paise (100 paise = ₹1).
  // ₹1,45,000 = 14,500,000 paise. Ticket ₹1,800 = 180,000 paise.
  nova: {
    label: 'Nova-like — tight D2C',
    payment_intensity: 32,
    avg_ticket_paise: 1_80_000,               // ₹1,800
    refund_rate: 0.08,
    opening_bank_paise: 1_45_00_000,          // ₹1,45,000 = 14,500,000 paise
    opening_pipeline_paise: 75_00_000,        // ₹75,000
    daily_variable_paise: 20_500_00,          // ₹20,500 /day
    fixed_expenses: [
      { day_of_month: 1,  amount_inr: 180000, category: 'payroll', note: 'Team payroll' },
      { day_of_month: 5,  amount_inr:  90000, category: 'rent',    note: 'Studio rent' },
      { day_of_month: 15, amount_inr: 550000, category: 'vendor',  note: 'Inventory bulk' },
      { day_of_month: 20, amount_inr:  45000, category: 'gst',     note: 'GST filing' },
    ],
  },
  healthy: {
    label: 'Healthy SaaS',
    payment_intensity: 12,
    avg_ticket_paise: 25_000_00,              // ₹25,000
    refund_rate: 0.02,
    opening_bank_paise: 12_40_000_00,         // ₹12,40,000
    opening_pipeline_paise: 1_50_000_00,      // ₹1,50,000
    daily_variable_paise: 12_000_00,          // ₹12,000 /day
    fixed_expenses: [
      { day_of_month: 1,  amount_inr: 650000, category: 'payroll', note: 'Payroll' },
      { day_of_month: 5,  amount_inr: 120000, category: 'rent',    note: 'Office' },
      { day_of_month: 15, amount_inr:  45000, category: 'saas',    note: 'AWS + tooling' },
      { day_of_month: 20, amount_inr:  85000, category: 'gst',     note: 'GST filing' },
    ],
  },
  high_volume: {
    label: 'High-volume kirana',
    payment_intensity: 180,
    avg_ticket_paise: 380_00,                 // ₹380
    refund_rate: 0.01,
    opening_bank_paise: 1_80_000_00,          // ₹1,80,000
    opening_pipeline_paise: 45_000_00,        // ₹45,000
    daily_variable_paise: 15_000_00,          // ₹15,000 /day
    fixed_expenses: [
      { day_of_month: 1,  amount_inr: 140000, category: 'payroll', note: 'Staff' },
      { day_of_month: 10, amount_inr:  90000, category: 'vendor',  note: 'Distributor' },
      { day_of_month: 20, amount_inr:  25000, category: 'gst',     note: 'GST' },
    ],
  },
}


export function CustomMerchant() {
  // Start with Nova-like preset — the stressed demo hero
  const p = PRESETS.nova
  const [intensity, setIntensity] = useState(p.payment_intensity)
  const [ticket, setTicket] = useState(p.avg_ticket_paise / 100)      // in INR for slider
  const [refund, setRefund] = useState(p.refund_rate * 100)           // in %
  const [openingBank, setOpeningBank] = useState(p.opening_bank_paise / 100)
  const [openingPipe, setOpeningPipe] = useState(p.opening_pipeline_paise / 100)
  const [dailyVar, setDailyVar] = useState(p.daily_variable_paise / 100)
  const [expenses, setExpenses] = useState<CFFixedExpense[]>(p.fixed_expenses)

  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<CFAdhocResp | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadPreset = (key: string) => {
    const pr = PRESETS[key]
    setIntensity(pr.payment_intensity)
    setTicket(pr.avg_ticket_paise / 100)
    setRefund(pr.refund_rate * 100)
    setOpeningBank(pr.opening_bank_paise / 100)
    setOpeningPipe(pr.opening_pipeline_paise / 100)
    setDailyVar(pr.daily_variable_paise / 100)
    setExpenses(pr.fixed_expenses)
    setResult(null)
  }

  const compute = async () => {
    setPending(true); setError(null)
    try {
      const r = await api.cfAdhoc({
        payment_intensity: intensity,
        avg_ticket_paise: Math.round(ticket * 100),
        refund_rate: refund / 100,
        dispute_rate: 0.004,
        opening_bank_paise: Math.round(openingBank * 100),
        opening_pipeline_paise: Math.round(openingPipe * 100),
        daily_variable_paise: Math.round(dailyVar * 100),
        fixed_expenses: expenses,
        horizon_days: 30,
        n_paths: 2000,
      })
      setResult(r)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <div className="pb-5 mb-8 border-b border-line">
        <h1 className="serif text-3xl mb-2">Try your own merchant</h1>
        <p className="text-[13.5px] text-ink-muted max-w-3xl">
          Everything below runs the same pipeline the pre-canned merchants use — fit → forecast → HJB
          solve → recommendation. This is the <strong className="text-ink">cold-start path</strong>: no
          cache, no pre-baked answer. In production this is what happens when a Razorpay merchant signs
          up or their parameters change.
        </p>
      </div>

      {/* Preset chips */}
      <div className="mb-8 flex items-center gap-3 flex-wrap">
        <span className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium">Load a preset</span>
        {Object.entries(PRESETS).map(([k, pr]) => (
          <button
            key={k}
            onClick={() => loadPreset(k)}
            className="mono text-[12px] px-3 py-1.5 border border-line rounded hover:border-brass
                       hover:bg-brass-tint text-ink transition-colors focus-ring"
          >
            {pr.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-[380px_1fr] gap-10 mb-10">
        {/* Left: sliders */}
        <div className="flex flex-col gap-6">
          <Slider label="Payment intensity (per day)" value={intensity} min={2} max={300}
                  step={1} onChange={setIntensity} format={v => `${v} payments/day`} />
          <Slider label="Average ticket" value={ticket} min={200} max={30000}
                  step={100} onChange={setTicket} format={v => `₹${v.toLocaleString('en-IN')}`} />
          <Slider label="Refund rate" value={refund} min={0} max={20}
                  step={0.5} onChange={setRefund} format={v => `${v.toFixed(1)}%`} />
          <Slider label="Opening bank balance" value={openingBank} min={0} max={2000000}
                  step={5000} onChange={setOpeningBank} format={v => fmtINRPaiseShort(v * 100)} />
          <Slider label="Pending pipeline" value={openingPipe} min={0} max={500000}
                  step={5000} onChange={setOpeningPipe} format={v => fmtINRPaiseShort(v * 100)} />
          <Slider label="Daily variable expense (ads + vendor)" value={dailyVar} min={0} max={100000}
                  step={500} onChange={setDailyVar} format={v => `${fmtINRPaiseShort(v * 100)}/day`} />

          <div className="border-t border-line pt-5">
            <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium mb-3">
              Fixed monthly outflows
            </div>
            <div className="flex flex-col gap-2">
              {expenses.map((e, i) => (
                <div key={i} className="grid grid-cols-[60px_1fr_100px_28px] gap-2 items-center
                                        mono text-[12px]">
                  <input
                    type="number" min={1} max={28} value={e.day_of_month}
                    onChange={ev => {
                      const next = [...expenses]
                      next[i] = { ...e, day_of_month: parseInt(ev.target.value) || 1 }
                      setExpenses(next)
                    }}
                    className="bg-panel border border-line rounded px-2 py-1 text-ink outline-none focus:border-brass"
                  />
                  <input
                    type="text" value={e.note}
                    onChange={ev => {
                      const next = [...expenses]
                      next[i] = { ...e, note: ev.target.value }
                      setExpenses(next)
                    }}
                    className="bg-panel border border-line rounded px-2 py-1 text-ink outline-none focus:border-brass"
                  />
                  <input
                    type="number" min={0} step={1000} value={e.amount_inr}
                    onChange={ev => {
                      const next = [...expenses]
                      next[i] = { ...e, amount_inr: parseInt(ev.target.value) || 0 }
                      setExpenses(next)
                    }}
                    className="bg-panel border border-line rounded px-2 py-1 text-ink outline-none focus:border-brass text-right"
                  />
                  <button
                    onClick={() => setExpenses(expenses.filter((_, j) => j !== i))}
                    className="text-ink-dim hover:text-loss text-[16px] leading-none"
                    aria-label="Remove"
                  >×</button>
                </div>
              ))}
              <button
                onClick={() => setExpenses([...expenses, { day_of_month: 1, amount_inr: 50000, category: 'other', note: 'New expense' }])}
                className="mono text-[11px] text-ink-muted hover:text-brass text-left px-2 py-1"
              >+ add expense</button>
            </div>
          </div>

          <button
            onClick={compute}
            disabled={pending}
            className={
              'font-semibold text-[13.5px] px-4 py-3 rounded transition-colors mt-3 ' +
              (pending
                ? 'bg-brass/30 text-[#14100a] cursor-wait'
                : 'bg-brass text-[#14100a] hover:bg-brass-hi')
            }
          >
            {pending ? 'Solving HJB · running value iteration...' : 'Compute recommendation ▶'}
          </button>
        </div>

        {/* Right: result */}
        <div>
          {error && (
            <div className="border border-loss/40 bg-loss/10 rounded-xl px-6 py-6">
              <div className="serif text-xl text-loss mb-1">Compute failed</div>
              <p className="mono text-[11px] text-ink-dim break-all">{error}</p>
            </div>
          )}
          {!error && !result && !pending && (
            <div className="border border-dashed border-line rounded-lg h-full min-h-[420px] flex
                            items-center justify-center text-center px-8">
              <div className="max-w-md">
                <div className="serif text-[26px] text-ink-muted mb-3 leading-tight">
                  Move the sliders, click Compute.
                </div>
                <p className="text-[13px] text-ink-dim">
                  You'll see how long the whole pipeline takes end-to-end — this is the compute
                  cost of every fresh merchant coming into the system. Watch the VI number.
                </p>
              </div>
            </div>
          )}
          {pending && !result && (
            <div className="border border-line rounded-lg h-full min-h-[420px] flex items-center justify-center">
              <div className="flex flex-col items-center gap-4">
                <span className="w-3 h-3 rounded-full bg-brass animate-pulse" />
                <div className="mono text-[12px] text-ink-muted">Fitting compound-Poisson · sampling paths · solving HJB...</div>
              </div>
            </div>
          )}
          {result && !pending && <Result r={result} />}
        </div>
      </div>
    </>
  )
}


function Slider({
  label, value, min, max, step, onChange, format,
}: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; format: (v: number) => string
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between items-baseline">
        <label className="text-[11px] uppercase tracking-[0.08em] text-ink-muted font-medium">{label}</label>
        <span className="mono text-[13px] text-brass tabular">{format(value)}</span>
      </div>
      <input
        type="range" className="big-slider" min={min} max={max} step={step}
        value={value} onChange={e => onChange(parseFloat(e.target.value))}
      />
    </div>
  )
}


function Result({ r }: { r: CFAdhocResp }) {
  const isNothing = r.action_kind === 'nothing'
  const feePaise = r.action_kind === 'IS' ? Math.round(r.action_amount_paise * 30 / 10000) : 0
  return (
    <div className="flex flex-col gap-8">
      {/* Timing hero — the star of this tab */}
      <section>
        <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium mb-3">
          Cold-start compute time
        </div>
        <div className="bg-panel border border-line rounded-lg px-6 py-5">
          <div className="serif text-[52px] leading-none text-brass tabular mb-1">
            {r.total_ms.toFixed(0)} ms
          </div>
          <div className="text-[13px] text-ink-muted">
            End-to-end · from parameter vector to recommendation
          </div>
          <div className="mt-5 pt-5 border-t border-line grid grid-cols-4 gap-4 mono text-[12px]">
            <TimingCell label="Fit"      ms={r.fit_ms}      color="ink-muted" />
            <TimingCell label="Forecast" ms={r.forecast_ms} color="ink"       />
            <TimingCell label="VI solve" ms={r.vi_ms}       color="loss"      hi />
            <TimingCell label="Total"    ms={r.total_ms}    color="brass"     hi />
          </div>
          <div className="mt-4 pt-4 border-t border-line text-[11.5px] text-ink-muted">
            The VI stage is the bottleneck. Our neural-operator surrogate replaces it with a
            <span className="mono text-brass"> 0.06 ms</span> forward pass —{' '}
            <span className="text-brass mono">{(r.vi_ms / 0.06).toFixed(0)}×</span> speedup for
            this exact query. At Razorpay scale this is the difference between "batch nightly"
            and "real-time per merchant".
          </div>
        </div>
      </section>

      {/* Recommendation */}
      <section>
        <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium mb-3">
          Recommended action
        </div>
        <div className={
          'border rounded-lg px-6 py-5 ' +
          (isNothing ? 'border-line bg-gain/5' : 'border-brass bg-brass/5')
        }>
          <div className="serif text-[36px] text-ink leading-tight mb-3">{r.action_label}</div>
          <p className="text-[13px] text-ink-muted mb-5 max-w-2xl">{r.reason}</p>
          <div className="grid grid-cols-3 gap-6 pt-5 border-t border-line">
            <Metric label="If you act"     value={fmtINRPaiseFull(r.expected_cost_paise)} />
            <Metric label="If you don't"   value={fmtINRPaiseFull(r.expected_cost_do_nothing_paise)}
                   tone={r.expected_cost_do_nothing_paise > r.expected_cost_paise ? 'loss' : undefined} />
            <Metric label={r.savings_vs_do_nothing_paise > 0 ? 'You save' : 'Difference'}
                   value={fmtINRPaiseFull(Math.abs(r.savings_vs_do_nothing_paise))}
                   tone={r.savings_vs_do_nothing_paise > 0 ? 'gain' : undefined}
                   sub={r.action_kind === 'IS' ? `IS fee: ${fmtINRPaiseFull(feePaise)}` : undefined} />
          </div>
        </div>
      </section>

      {/* Forecast summary */}
      <section>
        <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium mb-3">
          30-day forecast · P10 / P50 / P90 fan
        </div>
        <div className="bg-panel border border-line rounded-lg px-4 py-4">
          <MiniFan p10={r.p10_paise} p50={r.p50_paise} p90={r.p90_paise} />
          <div className="mt-3 grid grid-cols-4 gap-6 mono text-[12px] tabular">
            <MiniStat label="Projected min" value={fmtINRPaiseFull(r.projected_min_balance_paise)}
                     sub={r.projected_min_balance_date} tone={r.projected_min_balance_paise < 0 ? 'loss' : undefined} />
            <MiniStat label="P(shortfall)"  value={`${(r.prob_shortfall * 100).toFixed(1)}%`}
                     sub="next 30 days" tone={r.prob_shortfall > 0.4 ? 'loss' : undefined} />
            <MiniStat label="E[shortfall]"  value={fmtINRPaiseFull(r.expected_shortfall_paise)}
                     sub={r.expected_shortfall_date ?? 'not projected'} />
            <MiniStat label="Available now" value={fmtINRPaiseFull(r.available_now_paise)} />
          </div>
        </div>
      </section>

      {/* Top actions considered */}
      <section>
        <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium mb-3">
          Top 5 actions the HJB compared
        </div>
        <div className="bg-panel border border-line rounded-lg overflow-hidden">
          {r.top_actions.map(a => (
            <div key={a.label}
                 className={
                   'grid grid-cols-[1fr_140px_100px] gap-4 px-6 py-3 items-baseline mono text-[13px] ' +
                   'border-t border-line first:border-t-0 ' +
                   (a.is_optimal ? 'bg-brass/8' : '')
                 }>
              <span className={a.is_optimal ? 'text-brass font-medium' : 'text-ink'}>{a.label}</span>
              <span className="text-right tabular">{fmtINRPaiseFull(a.expected_cost_paise)}</span>
              <span className="text-right text-[11px]">
                {a.is_optimal
                  ? <span className="text-brass">◆ optimal</span>
                  : <span className="text-ink-dim">—</span>}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}


function TimingCell({ label, ms, color, hi }: { label: string; ms: number; color: string; hi?: boolean }) {
  const cls = color === 'brass' ? 'text-brass' : color === 'loss' ? 'text-loss' : color === 'ink' ? 'text-ink' : 'text-ink-muted'
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.08em] text-ink-dim mb-1">{label}</div>
      <div className={`${cls} ${hi ? 'text-[18px] font-medium' : 'text-[14px]'} tabular`}>
        {ms < 1 ? ms.toFixed(2) : ms.toFixed(0)}ms
      </div>
    </div>
  )
}


function Metric({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'loss' | 'gain'
}) {
  const cls = tone === 'loss' ? 'text-loss' : tone === 'gain' ? 'text-gain' : 'text-ink'
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.1em] text-ink-muted font-medium">{label}</span>
      <span className={`serif text-[26px] leading-none tabular ${cls}`}>{value}</span>
      {sub && <span className="text-[11px] text-ink-dim mono">{sub}</span>}
    </div>
  )
}


function MiniStat({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'loss' | 'gain'
}) {
  const cls = tone === 'loss' ? 'text-loss' : tone === 'gain' ? 'text-gain' : 'text-ink'
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-[0.06em] text-ink-dim">{label}</span>
      <span className={`${cls}`}>{value}</span>
      {sub && <span className="text-[10px] text-ink-dim">{sub}</span>}
    </div>
  )
}


function MiniFan({ p10, p50, p90 }: { p10: number[]; p50: number[]; p90: number[] }) {
  const W = 800, H = 180, padL = 60, padR = 24, padT = 12, padB = 24
  const all = [...p10, ...p90, 0]
  const yMin = Math.min(...all), yMax = Math.max(...all)
  const yPad = (yMax - yMin) * 0.05
  const yLo = yMin - yPad, yHi = yMax + yPad
  const N = p50.length
  const xAt = (i: number) => padL + (i / (N - 1)) * (W - padL - padR)
  const yAt = (v: number) => padT + (1 - (v - yLo) / (yHi - yLo)) * (H - padT - padB)

  const fan =
    'M ' + p10.map((v, i) => `${xAt(i)} ${yAt(v)}`).join(' L ') +
    ' L ' + p90.map((_, i) => `${xAt(N - 1 - i)} ${yAt(p90[N - 1 - i])}`).join(' L ') + ' Z'
  const median = 'M ' + p50.map((v, i) => `${xAt(i)} ${yAt(v)}`).join(' L ')
  const zeroInRange = yLo < 0 && yHi > 0

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
      <text x={padL - 8} y={yAt(yHi) + 4} textAnchor="end" fontSize={9} fill="#8892a0" fontFamily="IBM Plex Mono, monospace">{fmtINRPaiseShort(yHi)}</text>
      <text x={padL - 8} y={yAt(yLo) + 4} textAnchor="end" fontSize={9} fill="#8892a0" fontFamily="IBM Plex Mono, monospace">{fmtINRPaiseShort(yLo)}</text>
      {zeroInRange && <>
        <line x1={padL} y1={yAt(0)} x2={W - padR} y2={yAt(0)} stroke="#c94f4f" strokeWidth={0.8} strokeDasharray="4 3" />
        <text x={padL - 8} y={yAt(0) + 4} textAnchor="end" fontSize={9} fill="#c94f4f" fontFamily="IBM Plex Mono, monospace">₹0</text>
      </>}
      <path d={fan} fill="#e4a63c" fillOpacity={0.18} stroke="#e4a63c" strokeOpacity={0.4} strokeWidth={0.6} />
      <path d={median} fill="none" stroke="#e4a63c" strokeWidth={1.6} />
    </svg>
  )
}
