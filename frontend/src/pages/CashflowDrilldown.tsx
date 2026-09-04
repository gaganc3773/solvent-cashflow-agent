import { useEffect, useMemo, useState } from 'react'
import { api, CFEvent, CFEventStream, CFMerchant, CFSummary } from '../lib/api'
import { fmtINRPaiseFull, fmtINRPaiseShort } from '../lib/format'
import { toast } from '../components/Toast'

const DEFAULT_MERCHANT = 'nova_streetwear'

const CATEGORY_LABEL: Record<string, string> = {
  payment: 'Customer payments (captured)',
  settlement_credit: 'Settlements landed in bank',
  settlement_debit: 'Pipeline drained to bank',
  fee: 'Razorpay fees',
  tax: 'GST on fees',
  refund: 'Refunds to customers',
  dispute: 'Disputes / chargebacks',
  adjustment: 'Reconciliation adjustments',
  expense_payroll: 'Payroll',
  expense_rent: 'Rent',
  expense_gst: 'GST filing',
  expense_ads: 'Ad spend',
  expense_saas: 'SaaS subscriptions',
  expense_vendor: 'Vendor / inventory',
  expense_other: 'Other expenses',
  is_fee: 'Instant Settlement fee',
  credit_draw: 'Working capital advance',
  credit_repayment: 'Credit repayment',
}

const CATEGORY_KIND: Record<string, 'gain' | 'loss' | 'internal'> = {
  payment: 'gain',
  settlement_credit: 'gain',
  settlement_debit: 'internal',
  credit_draw: 'gain',
  fee: 'loss', tax: 'loss', refund: 'loss', dispute: 'loss', adjustment: 'loss',
  is_fee: 'loss', credit_repayment: 'loss',
  expense_payroll: 'loss', expense_rent: 'loss', expense_gst: 'loss',
  expense_ads: 'loss', expense_saas: 'loss', expense_vendor: 'loss',
  expense_other: 'loss',
}


export function CashflowDrilldown() {
  const [merchants, setMerchants] = useState<CFMerchant[]>([])
  const [selected, setSelected] = useState<string>(DEFAULT_MERCHANT)
  const [stream, setStream] = useState<CFEventStream | null>(null)
  const [summary, setSummary] = useState<CFSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bucket, setBucket] = useState<'bank' | 'pipeline' | 'all'>('bank')
  const from = '2026-08-01'
  const to = '2026-08-30'

  useEffect(() => { api.cfMerchants().then(setMerchants).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    setStream(null); setSummary(null); setError(null)
    api.cfEvents(selected, from, to).then(setStream).catch(e => setError(e.message))
    api.cfSummary(selected, from, to).then(setSummary).catch(e => setError(e.message))
  }, [selected])

  const filtered = useMemo(() => {
    if (!stream) return []
    if (bucket === 'all') return stream.events
    return stream.events.filter(e => e.bucket === bucket)
  }, [stream, bucket])

  // Aggregate by category
  const totals = useMemo(() => {
    const t: Record<string, number> = {}
    for (const e of filtered) t[e.category] = (t[e.category] ?? 0) + e.amount_paise
    return t
  }, [filtered])

  const totalIn = useMemo(() =>
    Object.entries(totals)
      .filter(([, v]) => v > 0)
      .reduce((s, [, v]) => s + v, 0), [totals])
  const totalOut = useMemo(() =>
    Object.entries(totals)
      .filter(([, v]) => v < 0)
      .reduce((s, [, v]) => s + v, 0), [totals])
  const net = totalIn + totalOut

  if (error) {
    return (
      <div className="border border-loss/40 bg-loss/10 rounded-xl px-6 py-6">
        <div className="serif text-xl text-loss mb-1">Could not load cashflow</div>
        <p className="mono text-[11px] text-ink-dim break-all">{error}</p>
      </div>
    )
  }
  if (!stream || !summary) {
    return (
      <div className="flex flex-col gap-6">
        <div className="h-10 rounded-lg bg-panel border border-line animate-pulse" />
        <div className="h-28 rounded-lg bg-panel border border-line animate-pulse" />
        <div className="h-[420px] max-w-[720px] mx-auto w-full rounded-lg bg-panel border border-line animate-pulse" />
      </div>
    )
  }

  const merchant = merchants.find(m => m.key === selected)

  return (
    <>
      {/* Topbar */}
      <div className="flex items-center justify-between pb-5 mb-8 border-b border-line">
        <div className="flex items-center gap-2.5 text-xs text-ink-muted">
          <span>Merchant</span>
          <select
            className="bg-panel border border-line text-ink mono text-sm px-3 py-1.5 rounded outline-none"
            value={selected}
            onChange={e => setSelected(e.target.value)}
          >
            {merchants.map(m => (
              <option key={m.key} value={m.key}>{m.name}</option>
            ))}
          </select>
          <span className="ml-4">Period</span>
          <span className="mono text-sm text-ink">{from} → {to}</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <BucketToggle bucket={bucket} setBucket={setBucket} />
        </div>
      </div>

      {/* Hero totals */}
      <section className="grid grid-cols-3 gap-10 pb-6 mb-10 border-b border-line items-end">
        <HeroItem label="Money in" value={fmtINRPaiseFull(totalIn)} sub={`${filtered.filter(e => e.amount_paise > 0).length} events`} tone="gain" />
        <HeroItem label="Money out" value={fmtINRPaiseFull(-totalOut)} sub={`${filtered.filter(e => e.amount_paise < 0).length} events`} tone="loss" />
        <HeroItem
          label={<>Net change · <span className="text-brass">{bucket === 'bank' ? 'bank' : bucket === 'pipeline' ? 'pipeline' : 'combined'}</span></>}
          value={(net < 0 ? '−' : '+') + fmtINRPaiseFull(Math.abs(net))}
          sub={`Bank end: ${fmtINRPaiseShort(summary.bank_balance_end_paise)} · Pipeline end: ${fmtINRPaiseShort(summary.pipeline_balance_end_paise)}`}
          tone={net < 0 ? 'loss' : 'gain'}
          big
        />
      </section>

      {/* Receipt: category totals */}
      <div className="max-w-[720px] mx-auto bg-panel border border-line rounded-lg px-10 py-8 relative">
        <div className="text-center pb-5 mb-5 border-b-2 border-dotted border-line-hi">
          <div className="serif text-[22px] tracking-tight mb-1">◆ Solvent Cashflow Statement</div>
          <div className="mono text-[11px] text-ink-muted tracking-wide">
            {merchant?.name.toUpperCase()} · {from} → {to} · {bucket.toUpperCase()}
          </div>
          <div className="text-[11px] text-ink-dim mt-2">
            Click any line for the transactions behind it
          </div>
        </div>

        {Object.entries(totals)
          .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
          .map(([cat, amt], i) => {
            const kind = CATEGORY_KIND[cat] ?? 'internal'
            const cls = kind === 'gain' ? 'text-gain' : kind === 'loss' ? 'text-loss' : 'text-ink-muted'
            const dot = kind === 'gain' ? 'bg-gain' : kind === 'loss' ? 'bg-loss' : 'bg-ink-muted'
            const nEvents = filtered.filter(e => e.category === cat).length
            return (
              <button
                key={cat}
                onClick={() => toast('Drilldown', `${nEvents} events for ${CATEGORY_LABEL[cat] ?? cat}`)}
                className={
                  'group w-full text-left grid grid-cols-[1fr_auto_auto] gap-4 py-2.5 items-baseline ' +
                  'mono text-[13px] tabular rounded-sm px-1 -mx-1 focus-ring ' +
                  'hover:bg-panel-hi transition-colors ' +
                  (i > 0 ? 'border-t border-dotted border-line' : '')
                }
              >
                <div className="flex items-center gap-2.5">
                  <span className={`w-1.5 h-1.5 rounded-sm inline-block shrink-0 ${dot}`} />
                  <span className="text-ink">{CATEGORY_LABEL[cat] ?? cat}</span>
                  <span className="text-[10px] text-ink-dim opacity-0 group-hover:opacity-100
                                   group-focus:opacity-100 transition-opacity whitespace-nowrap">
                    {nEvents} event{nEvents === 1 ? '' : 's'} →
                  </span>
                </div>
                <div className="text-[11px] text-ink-muted text-right">{cat}</div>
                <div className={`text-right font-medium min-w-[100px] ${cls}`}>
                  {amt >= 0 ? '+' : '−'}{fmtINRPaiseFull(Math.abs(amt))}
                </div>
              </button>
            )
          })}

        <div className="pt-4 mt-2 border-t-2 border-ink grid grid-cols-[1fr_auto_auto] gap-4 items-baseline">
          <div className="serif text-[22px]">Net {bucket === 'all' ? 'across buckets' : bucket}</div>
          <div />
          <div className={`serif text-[32px] tabular text-right ${net < 0 ? 'text-loss' : 'text-gain'}`}>
            {net < 0 ? '−' : '+'}{fmtINRPaiseFull(Math.abs(net))}
          </div>
        </div>

        <div className="text-center pt-5 mt-3 border-t-2 border-dotted border-line-hi mono text-[11px] text-ink-muted tracking-wide">
          {stream.events.length} events reconstructed · every rupee traceable
        </div>
      </div>

      {/* Recent events feed */}
      <section className="mt-10">
        <h2 className="serif text-xl mb-3">Recent events</h2>
        <div className="bg-panel border border-line rounded-lg overflow-hidden">
          {filtered.slice(-20).reverse().map((e, i) => (
            <EventRow key={`${e.source_id}-${i}`} e={e} />
          ))}
        </div>
      </section>
    </>
  )
}


function BucketToggle({ bucket, setBucket }: {
  bucket: 'bank'|'pipeline'|'all'; setBucket: (b: 'bank'|'pipeline'|'all') => void
}) {
  const opts: Array<'bank'|'pipeline'|'all'> = ['bank', 'pipeline', 'all']
  return (
    <div className="inline-flex items-center gap-1 bg-panel border border-line rounded p-0.5">
      {opts.map(o => (
        <button
          key={o}
          onClick={() => setBucket(o)}
          className={
            'text-[12px] px-2.5 py-1 rounded transition-colors ' +
            (bucket === o ? 'bg-brass/15 text-brass' : 'text-ink-muted hover:text-ink')
          }
        >
          {o}
        </button>
      ))}
    </div>
  )
}


function EventRow({ e }: { e: CFEvent }) {
  const kind = CATEGORY_KIND[e.category] ?? 'internal'
  const cls = kind === 'gain' ? 'text-gain' : kind === 'loss' ? 'text-loss' : 'text-ink-muted'
  return (
    <div className="grid grid-cols-[100px_130px_1fr_100px_140px] gap-3 px-6 py-2.5 items-baseline
                    mono text-[12px] border-t border-line first:border-t-0">
      <span className="text-ink">{e.date}</span>
      <span className="text-ink-muted">{CATEGORY_LABEL[e.category] ?? e.category}</span>
      <span className="text-[11px] text-ink-dim truncate">{e.source_id}</span>
      <span className="text-[11px] text-ink-dim">{e.bucket}</span>
      <span className={`text-right tabular font-medium ${cls}`}>
        {e.amount_paise >= 0 ? '+' : '−'}{fmtINRPaiseFull(Math.abs(e.amount_paise))}
      </span>
    </div>
  )
}


function HeroItem({
  label, value, sub, tone, big,
}: {
  label: React.ReactNode; value: string; sub?: string;
  tone?: 'loss' | 'gain'; big?: boolean
}) {
  const cls = tone === 'loss' ? 'text-loss' : tone === 'gain' ? 'text-gain' : 'text-ink'
  const size = big ? 'text-[52px]' : 'text-[38px]'
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-[0.1em] text-ink-muted font-medium">{label}</span>
      <span className={`serif ${size} leading-none tabular ${cls}`}>{value}</span>
      {sub && <span className="text-xs text-ink-muted">{sub}</span>}
    </div>
  )
}
