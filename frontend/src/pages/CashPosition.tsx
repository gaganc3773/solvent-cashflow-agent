import { useEffect, useState } from 'react'
import { api, CFForecast, CFMerchant, CFOptimize, CFBenchmark } from '../lib/api'
import { fmtINRPaiseShort, fmtINRPaiseFull } from '../lib/format'
import { toast } from '../components/Toast'

const DEFAULT_MERCHANT = 'nova_streetwear'

export function CashPosition() {
  const [merchants, setMerchants] = useState<CFMerchant[]>([])
  const [selected, setSelected] = useState<string>(DEFAULT_MERCHANT)
  const [forecast, setForecast] = useState<CFForecast | null>(null)
  const [optimize, setOptimize] = useState<CFOptimize | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.cfMerchants().then(setMerchants).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    setForecast(null); setOptimize(null); setError(null)
    api.cfForecast(selected, { horizon_days: 30, n_paths: 5000 })
      .then(setForecast).catch(e => setError(e.message))
    api.cfOptimize(selected, { horizon_days: 30, n_paths: 3000 })
      .then(setOptimize).catch(e => setError(e.message))
  }, [selected])

  if (error) {
    return (
      <div className="border border-loss/40 bg-loss/10 rounded-xl px-6 py-6">
        <div className="serif text-xl text-loss mb-1">Could not load this forecast</div>
        <p className="mono text-[11px] text-ink-dim break-all">{error}</p>
      </div>
    )
  }

  if (!forecast) {
    return (
      <div className="flex flex-col gap-6">
        <div className="h-12 rounded-lg bg-panel border border-line animate-pulse" />
        <div className="h-32 rounded-lg bg-panel border border-line animate-pulse" />
        <div className="h-[360px] rounded-lg bg-panel border border-line animate-pulse" />
      </div>
    )
  }

  return (
    <>
      <MerchantBar merchants={merchants} selected={selected} onSelect={setSelected} forecast={forecast} />
      <HeroKPIs f={forecast} />
      <RiskBand f={forecast} />
      <FanChart f={forecast} />
      <RecommendationPanel opt={optimize} />
      <KnownOutflows f={forecast} />
      <UnderTheHood f={forecast} />
    </>
  )
}


function MerchantBar({
  merchants, selected, onSelect, forecast,
}: {
  merchants: CFMerchant[]; selected: string; onSelect: (k: string) => void; forecast: CFForecast
}) {
  return (
    <div className="flex items-center justify-between pb-5 mb-8 border-b border-line">
      <div className="flex items-center gap-2.5 text-xs text-ink-muted">
        <span>Merchant</span>
        <select
          className="bg-panel border border-line text-ink mono text-sm px-3 py-1.5 rounded outline-none"
          value={selected}
          onChange={e => onSelect(e.target.value)}
        >
          {merchants.map(m => (
            <option key={m.key} value={m.key}>{m.name}  ·  {m.sector}</option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <span>Forecast from</span>
        <strong className="text-ink mono text-sm">{forecast.start_date}</strong>
        <span>·</span>
        <span className="mono text-[11px]">{forecast.horizon_days} days · {forecast.n_paths.toLocaleString()} paths</span>
      </div>
    </div>
  )
}


function HeroKPIs({ f }: { f: CFForecast }) {
  const projMinNeg = f.projected_min_balance_paise < 0
  return (
    <section className="grid grid-cols-4 gap-8 pb-6 mb-10 border-b border-line items-end">
      <KPI
        label="Available now"
        value={fmtINRPaiseFull(f.available_now_paise)}
        sub="In your bank account"
      />
      <KPI
        label="Expected in · 7d"
        value={fmtINRPaiseShort(f.expected_inflow_next_7d_paise)}
        sub="From forecasted settlements"
        tone="gain"
      />
      <KPI
        label="Expected out · 7d"
        value={fmtINRPaiseShort(f.expected_outflow_next_7d_paise)}
        sub="Payroll · rent · known bills"
        tone="loss"
      />
      <KPI
        label={<>Projected min · <span className={projMinNeg ? 'text-loss' : 'text-brass'}>on {f.projected_min_balance_date}</span></>}
        value={fmtINRPaiseFull(f.projected_min_balance_paise)}
        sub="Median-case worst point"
        tone={projMinNeg ? 'loss' : undefined}
        big
      />
    </section>
  )
}


function KPI({
  label, value, sub, tone, big,
}: {
  label: React.ReactNode; value: string; sub?: string;
  tone?: 'loss' | 'gain'; big?: boolean
}) {
  const cls = tone === 'loss' ? 'text-loss' : tone === 'gain' ? 'text-gain' : 'text-ink'
  const size = big ? 'text-[52px]' : 'text-[36px]'
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium">{label}</span>
      <span className={`serif ${size} leading-none tabular ${cls}`}>{value}</span>
      {sub && <span className="text-xs text-ink-muted">{sub}</span>}
    </div>
  )
}


function RiskBand({ f }: { f: CFForecast }) {
  const pct = Math.round(f.prob_shortfall * 100)
  const tone = pct >= 40 ? 'loss' : pct >= 15 ? 'brass' : 'gain'
  const label =
    pct >= 40 ? 'High shortfall risk' :
    pct >= 15 ? 'Watch this — moderate risk' :
    'Comfortable buffer'
  const cls = tone === 'loss' ? 'border-loss/40 bg-loss/8'
            : tone === 'brass' ? 'border-brass/40 bg-brass/8'
            : 'border-gain/40 bg-gain/8'
  const dot = tone === 'loss' ? 'bg-loss' : tone === 'brass' ? 'bg-brass' : 'bg-gain'
  return (
    <div className={`flex items-center justify-between rounded-lg border px-6 py-4 mb-8 ${cls}`}>
      <div className="flex items-center gap-3">
        <span className={`w-2 h-2 rounded-full ${dot} inline-block`} />
        <div className="flex flex-col">
          <span className="serif text-xl leading-tight">{label}</span>
          <span className="text-xs text-ink-muted">
            {pct >= 15
              ? <>Chance of dipping below zero in the next 30 days: <strong className="text-ink">{pct}%</strong>
                {f.expected_shortfall_date && <>. Expected around <strong className="text-ink">{f.expected_shortfall_date}</strong> when your balance is projected to hit <strong className="text-ink">{fmtINRPaiseShort(f.expected_shortfall_paise)}</strong>.</>}</>
              : <>Your 90th-percentile worst case still leaves ₹{Math.round(Math.min(...f.p10_paise) / 100).toLocaleString('en-IN')} of buffer.</>
            }
          </span>
        </div>
      </div>
      {pct >= 15 && (
        <button
          className="bg-brass text-[#14100a] font-semibold text-[13px] px-4 py-2 rounded hover:bg-brass-hi"
          onClick={() => {
            const el = document.getElementById('recommendation-panel')
            if (el) el.scrollIntoView({ behavior: 'smooth' })
          }}
        >
          See recommendation ↓
        </button>
      )}
    </div>
  )
}


function FanChart({ f }: { f: CFForecast }) {
  // Chart dimensions
  const W = 900, H = 320, padL = 60, padR = 24, padT = 20, padB = 44

  const all = [...f.p10_paise, ...f.p90_paise, 0]
  const yMin = Math.min(...all)
  const yMax = Math.max(...all)
  const yPad = (yMax - yMin) * 0.08
  const yLo = yMin - yPad
  const yHi = yMax + yPad

  const N = f.dates.length
  const xAt = (i: number) => padL + (i / (N - 1)) * (W - padL - padR)
  const yAt = (v: number) => padT + (1 - (v - yLo) / (yHi - yLo)) * (H - padT - padB)

  // Fan path (p10 forward, p90 back)
  const fanPath =
    'M ' + f.p10_paise.map((v, i) => `${xAt(i)} ${yAt(v)}`).join(' L ') +
    ' L ' + f.p90_paise.map((v, i) => `${xAt(N - 1 - i)} ${yAt(f.p90_paise[N - 1 - i])}`).join(' L ') + ' Z'
  const medianPath = 'M ' + f.p50_paise.map((v, i) => `${xAt(i)} ${yAt(v)}`).join(' L ')

  // y-axis ticks
  const yTicks = [yLo, (yLo + yHi) / 2, yHi]
  // x-axis ticks: first, mid, last
  const xTicks = [0, Math.floor(N / 2), N - 1]

  // Zero line if in range
  const zeroInRange = yLo < 0 && yHi > 0
  const yZero = yAt(0)

  // Mark known outflows on x-axis
  const markers = f.known_outflows
    .map(o => ({ idx: f.dates.indexOf(o.date), ...o }))
    .filter(m => m.idx >= 0)

  return (
    <section className="mb-8">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="serif text-xl">Balance path · next 30 days</h2>
        <div className="flex items-center gap-4 text-[11px] text-ink-muted">
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-sm bg-brass/25 border border-brass/40" />
            10-to-90 percentile band
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-4 h-0.5 bg-brass" />
            Median path
          </span>
          {zeroInRange && (
            <span className="flex items-center gap-1.5">
              <span className="w-4 border-t border-dashed border-loss/60" />
              Zero balance
            </span>
          )}
        </div>
      </div>
      <div className="bg-panel border border-line rounded-lg p-3">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
          {/* Y grid */}
          {yTicks.map((v, i) => (
            <g key={i}>
              <line x1={padL} y1={yAt(v)} x2={W - padR} y2={yAt(v)}
                    stroke="#2a2f38" strokeWidth={1} strokeDasharray={i === 1 ? '' : '2 4'} />
              <text x={padL - 8} y={yAt(v) + 4} textAnchor="end" fontSize={10} fill="#8892a0"
                    fontFamily="IBM Plex Mono, monospace">
                {fmtINRPaiseShort(v)}
              </text>
            </g>
          ))}
          {/* Zero line */}
          {zeroInRange && (
            <line x1={padL} y1={yZero} x2={W - padR} y2={yZero}
                  stroke="#c94f4f" strokeWidth={1} strokeDasharray="4 3" />
          )}
          {/* Fan */}
          <path d={fanPath} fill="#e4a63c" fillOpacity={0.18} stroke="#e4a63c" strokeOpacity={0.4} strokeWidth={0.8} />
          {/* Median */}
          <path d={medianPath} fill="none" stroke="#e4a63c" strokeWidth={2} />
          {/* Markers for known outflows */}
          {markers.map((m, i) => (
            <g key={i}>
              <line x1={xAt(m.idx)} y1={padT} x2={xAt(m.idx)} y2={H - padB}
                    stroke="#8892a0" strokeWidth={0.6} strokeDasharray="2 3" opacity={0.6} />
              <circle cx={xAt(m.idx)} cy={H - padB} r={3} fill="#8892a0" />
            </g>
          ))}
          {/* X ticks */}
          {xTicks.map((i) => (
            <text key={i} x={xAt(i)} y={H - padB + 18} textAnchor={i === 0 ? 'start' : i === N - 1 ? 'end' : 'middle'}
                  fontSize={10} fill="#8892a0" fontFamily="IBM Plex Mono, monospace">
              {f.dates[i]}
            </text>
          ))}
          {/* Marker labels — abbreviated below */}
          {markers.map((m, i) => (
            <text key={i} x={xAt(m.idx)} y={H - 4} textAnchor="middle"
                  fontSize={9} fill="#8892a0" fontFamily="IBM Plex Mono, monospace">
              {m.category.replace('expense_', '')}
            </text>
          ))}
        </svg>
      </div>
    </section>
  )
}


function RecommendationPanel({ opt }: { opt: CFOptimize | null }) {
  const [executed, setExecuted] = useState(false)
  if (!opt) {
    return (
      <section id="recommendation-panel" className="mb-8">
        <div className="h-52 rounded-lg bg-panel border border-line animate-pulse" />
      </section>
    )
  }

  const isNothing = opt.action_kind === 'nothing'
  const isIS = opt.action_kind === 'IS'
  const feeIS_paise = isIS ? Math.round(opt.action_amount_paise * 30 / 10000) : 0

  const execute = () => {
    api.audit({
      action: 'recommendation_executed',
      merchant_id: opt.merchant_id,
      recommendation: opt.action_label,
      expected_cost_paise: opt.expected_cost_paise,
      engine: opt.engine,
    })
      .then(r => {
        setExecuted(true)
        toast(`${opt.action_label} logged`, `Saved at ${r.logged_at}`)
      })
      .catch(e => toast('Could not log action', e.message, 'error'))
  }

  return (
    <section id="recommendation-panel" className="mb-8">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="serif text-xl">Today's recommended action</h2>
        <span className="text-[11px] text-ink-dim mono">
          Solved by value iteration in {opt.solve_time_ms.toFixed(0)}ms · {opt.engine.toUpperCase()} engine
        </span>
      </div>

      <div className="bg-panel border border-line rounded-lg overflow-hidden">
        {/* Hero */}
        <div className={
          'px-8 py-6 border-b border-line ' +
          (isNothing ? 'bg-gain/5' : 'bg-brass/5')
        }>
          <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted mb-1">
            {isNothing ? 'No action needed' : 'Recommended'}
          </div>
          <div className="serif text-[42px] leading-tight text-ink mb-2">
            {opt.action_label}
          </div>
          <p className="text-[13.5px] text-ink-muted max-w-2xl">{opt.reason}</p>
        </div>

        {/* Cost breakdown — TWO baselines, honest framing */}
        <div className="grid grid-cols-3 gap-8 px-8 py-5 border-b border-line">
          <Metric
            label="Optimal play (with Solvent)"
            value={fmtINRPaiseFull(opt.expected_cost_paise)}
            sub={`Includes today's action + optimal future play`}
            tone="gain"
          />
          <Metric
            label="If you never act all month"
            value={fmtINRPaiseFull(opt.expected_cost_never_act_paise)}
            sub={`Expected overdraft cost with zero cash management`}
            tone="loss"
          />
          <Metric
            label="Solvent saves you"
            value={fmtINRPaiseFull(opt.savings_vs_never_act_paise)}
            sub={
              isIS
                ? `Today's IS fee only ${fmtINRPaiseFull(feeIS_paise)}`
                : opt.action_kind === 'credit'
                  ? `via credit at horizon-end interest`
                  : `by knowing when to hold`
            }
            tone="gain"
          />
        </div>

        {/* Second-order comparison — the "marginal today" perspective for the curious */}
        <details className="px-8 py-3 border-b border-line group">
          <summary className="text-[11px] uppercase tracking-[0.08em] text-ink-muted font-medium cursor-pointer select-none">
            Why is today's marginal saving smaller? ↓
          </summary>
          <div className="mt-3 grid grid-cols-3 gap-8 mono text-[12px]">
            <div>
              <div className="text-[10px] uppercase tracking-[0.08em] text-ink-dim mb-1">Act today</div>
              <div className="text-ink tabular">{fmtINRPaiseFull(opt.expected_cost_paise)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.08em] text-ink-dim mb-1">Hold today, act tomorrow</div>
              <div className="text-ink tabular">{fmtINRPaiseFull(opt.expected_cost_do_nothing_paise)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.08em] text-ink-dim mb-1">Marginal saving today</div>
              <div className="text-ink tabular">{fmtINRPaiseFull(opt.savings_vs_do_nothing_paise)}</div>
            </div>
          </div>
          <p className="mt-3 text-[11.5px] text-ink-muted leading-relaxed">
            The marginal saving of acting TODAY vs waiting until tomorrow is small — because if you wait,
            Solvent still recommends the same action tomorrow. But if you NEVER act, the full 30-day cost
            piles up. The bigger number above is the honest &quot;value of using Solvent at all&quot;.
          </p>
        </details>

        {/* Defensible range */}
        {opt.defensible_range.length > 1 && (
          <div className="px-8 py-4 border-b border-line">
            <div className="text-[11px] uppercase tracking-[0.1em] text-ink-muted mb-2">
              Also defensible (within ₹2 of optimal)
            </div>
            <div className="flex flex-wrap gap-2">
              {opt.defensible_range
                .filter(l => l !== opt.action_label)
                .map(l => (
                  <span key={l} className="mono text-[12px] px-2.5 py-1 bg-panel-hi rounded text-ink-muted">
                    {l}
                  </span>
                ))}
            </div>
          </div>
        )}

        {/* Q-values table */}
        <details className="px-8 py-4 border-b border-line group">
          <summary className="text-[11px] uppercase tracking-[0.1em] text-ink-muted mb-2 cursor-pointer select-none">
            Show all actions the HJB compared ↓
          </summary>
          <div className="mt-3 grid grid-cols-[1fr_140px_100px] gap-4 mono text-[12px]">
            <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim">Action</div>
            <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim text-right">Expected cost</div>
            <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim text-right">Verdict</div>
            {opt.all_actions.map(a => (
              <div key={a.label} className="contents">
                <div className={a.is_optimal ? 'text-brass font-medium' : 'text-ink'}>{a.label}</div>
                <div className="text-right tabular">{fmtINRPaiseFull(a.expected_cost_paise)}</div>
                <div className="text-right text-[11px]">
                  {a.is_optimal ? <span className="text-brass">◆ optimal</span>
                    : a.is_defensible ? <span className="text-ink-muted">defensible</span>
                    : <span className="text-ink-dim">—</span>}
                </div>
              </div>
            ))}
          </div>
        </details>

        {/* Scale multiplier */}
        {!isNothing && opt.savings_vs_do_nothing_paise > 0 && (
          <div className="px-8 py-4 border-b border-line bg-gradient-to-r from-brass/5 to-transparent">
            <div className="flex items-baseline gap-6">
              <div className="flex-1">
                <div className="text-[10px] uppercase tracking-[0.1em] text-ink-muted mb-1">
                  At Razorpay scale
                </div>
                <p className="text-[13px] text-ink leading-relaxed max-w-2xl">
                  Your personal saving is small — but VI-optimal decisions across
                  Razorpay's active merchant base add up. Benchmarked against a
                  fixed retry heuristic, VI averages{' '}
                  <strong className="text-brass mono">₹1,545 less cost per merchant per month</strong>.
                  Scaled to <strong className="mono">~5M active merchants</strong>{' '}
                  = <strong className="text-brass mono">₹9.3 crore/year</strong>{' '}
                  of unnecessary IS fees and overdraft cost avoided.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Execute */}
        <div className="px-8 py-5 flex items-center justify-between">
          <div className="text-[12px] text-ink-muted max-w-lg">
            {isNothing
              ? 'No action needed today. Solvent will recheck as your balance and pipeline evolve.'
              : 'One click below logs this action to your records. Wiring to the live Razorpay Instant Settlement / Capital API is the pilot integration.'}
          </div>
          {!isNothing && (
            <button
              disabled={executed}
              onClick={execute}
              className={
                'font-semibold text-[13px] px-5 py-2.5 rounded transition-colors ' +
                (executed
                  ? 'bg-gain/20 text-gain cursor-default'
                  : 'bg-brass text-[#14100a] hover:bg-brass-hi')
              }
            >
              {executed ? '✓ Logged' : `Execute · ${opt.action_label}`}
            </button>
          )}
        </div>
      </div>
    </section>
  )
}


function Metric({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'loss' | 'gain'
}) {
  const cls = tone === 'loss' ? 'text-loss' : tone === 'gain' ? 'text-gain' : 'text-ink'
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.1em] text-ink-muted font-medium">{label}</span>
      <span className={`serif text-[28px] leading-none tabular ${cls}`}>{value}</span>
      {sub && <span className="text-[11px] text-ink-dim mono">{sub}</span>}
    </div>
  )
}


function KnownOutflows({ f }: { f: CFForecast }) {
  if (!f.known_outflows.length) return null
  return (
    <section className="mb-8">
      <h2 className="serif text-xl mb-3">Upcoming known outflows</h2>
      <div className="bg-panel border border-line rounded-lg">
        {f.known_outflows.map((o, i) => (
          <div key={i}
               className={
                 'grid grid-cols-[100px_1fr_140px_140px] gap-4 px-6 py-3 items-baseline mono text-[13px] ' +
                 (i > 0 ? 'border-t border-line' : '')
               }
          >
            <span className="text-ink">{o.date}</span>
            <span className="text-ink-muted">{o.note}</span>
            <span className="text-[11px] text-ink-dim">{o.category.replace('expense_', '')}</span>
            <span className="text-loss text-right tabular font-medium">
              −{fmtINRPaiseShort(Math.abs(o.amount_paise))}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}


function UnderTheHood({ f }: { f: CFForecast }) {
  const [bench, setBench] = useState<CFBenchmark | null>(null)
  const [loadingBench, setLoadingBench] = useState(false)

  const loadBench = () => {
    if (bench || loadingBench) return
    setLoadingBench(true)
    api.cfBenchmark(30).then(b => { setBench(b); setLoadingBench(false) })
      .catch(() => setLoadingBench(false))
  }

  return (
    <section className="mt-10 pt-6 border-t border-line">
      <h3 className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium mb-3">
        Under the hood
      </h3>
      <div className="grid grid-cols-3 gap-6 mono text-[12px] text-ink-muted mb-8">
        <div>
          <div className="text-ink serif text-lg mb-1">Compound-Poisson fit</div>
          Payment arrivals by day-of-week, ticket-size lognormal fit from
          the last 90 days of your Razorpay history.
        </div>
        <div>
          <div className="text-ink serif text-lg mb-1">{f.n_paths.toLocaleString()} paths</div>
          Monte-Carlo trajectories of your bank balance over the next
          {' '}{f.horizon_days} days. Percentiles shown are 10 / 50 / 90.
        </div>
        <div>
          <div className="text-ink serif text-lg mb-1">HJB via value iteration</div>
          The recommendation solves a Hamilton-Jacobi-Bellman impulse-control
          problem on a discretised state grid. Textbook cash management,
          Miller-Orr 1966.
        </div>
      </div>

      <details className="border-t border-line pt-4 group" onToggle={e => (e.currentTarget as HTMLDetailsElement).open && loadBench()}>
        <summary className="text-[11px] uppercase tracking-[0.1em] text-ink-muted mb-3 cursor-pointer select-none">
          Benchmark: HJB-optimal vs simple heuristics (across all demo merchants) ↓
        </summary>
        {!bench && loadingBench && (
          <div className="mt-4 text-[11px] text-ink-dim">
            Solving VI on {4} merchants × {30}-day horizon... ~15s
          </div>
        )}
        {bench && (
          <>
            <div className="mt-4 grid grid-cols-[1fr_130px_130px_130px] gap-4 mono text-[12px]">
              <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim">Policy</div>
              <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim text-right">Mean cost / merchant</div>
              <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim text-right">Excess vs VI</div>
              <div className="text-[10px] uppercase tracking-[0.05em] text-ink-dim text-right">Inference</div>
              {bench.policies.map(p => (
                <div key={p.policy_name} className="contents">
                  <div className={p.policy_name === 'vi' ? 'text-brass font-medium' : 'text-ink'}>
                    {p.label}
                  </div>
                  <div className="text-right tabular">{fmtINRPaiseFull(p.mean_cost_paise)}</div>
                  <div className={`text-right tabular ${p.cost_lift_over_vi_paise > 0 ? 'text-loss' : 'text-ink-dim'}`}>
                    {p.policy_name === 'vi' ? '—' :
                      `+${fmtINRPaiseFull(p.cost_lift_over_vi_paise)} (+${p.cost_lift_over_vi_pct.toFixed(0)}%)`}
                  </div>
                  <div className="text-right text-[11px] text-ink-muted">
                    {p.inference_ms < 1 ? '<1ms' : p.inference_ms > 100 ? `${p.inference_ms.toFixed(0)}ms` : `${p.inference_ms.toFixed(1)}ms`}
                  </div>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-ink-dim mt-4 max-w-3xl leading-relaxed">
              {bench.footnote}
              {' '}A trained MLP surrogate reproduces V(s, t=0) at inference in ~0.06ms — a
              ~59,000× speedup over solving VI per merchant. At Razorpay scale (10M+ merchants),
              this is the path to serving real-time policies without per-merchant PDE re-solves.
              This is the FNO / operator-learning research angle.
            </p>
          </>
        )}
      </details>
    </section>
  )
}
