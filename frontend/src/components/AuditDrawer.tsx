import { useCallback, useEffect, useState } from 'react'
import { api, AuditEntry } from '../lib/api'
import { Drawer } from './Drawer'

/** Fields the drawer renders in the header line rather than the detail grid. */
const HEADER_KEYS = new Set(['action', 'logged_at'])

const ACTION_LABELS: Record<string, string> = {
  recommendation_executed: 'Solvent recommendation executed',
  hedge_recommendation_sent: 'Hedge recommendation sent to treasury',
  settlement_report_exported: 'Settlement breakdown exported',
  scenario_explored: 'Scenario explored',
  evidence_viewed: 'Transaction evidence opened',
  explain_asked: 'Explanation requested',
}

function label(action: string): string {
  return ACTION_LABELS[action] ?? action.replace(/_/g, ' ')
}

function fmtTime(iso: string): { date: string; time: string; rel: string } {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return { date: iso, time: '', rel: '' }
  const mins = Math.floor((Date.now() - d.getTime()) / 60000)
  const rel =
    mins < 1 ? 'just now'
    : mins < 60 ? `${mins}m ago`
    : mins < 1440 ? `${Math.floor(mins / 60)}h ago`
    : `${Math.floor(mins / 1440)}d ago`
  return {
    date: d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }),
    time: d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
    rel,
  }
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') {
    return Number.isInteger(v) ? v.toLocaleString('en-IN') : v.toString()
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function AuditDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.auditList(20)
      .then(r => setEntries(r.entries))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Refresh every time the drawer is opened, so an action taken on the page
  // is visible the moment the merchant goes looking for it.
  useEffect(() => {
    if (open) load()
  }, [open, load])

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Your records"
      width={440}
      subtitle={
        <>
          Append-only. Every action Solvent takes on your behalf is written here
          before anything else happens.
        </>
      }
      footer={
        <div className="px-5 py-3 flex items-center justify-between">
          <span className="mono text-[10px] text-ink-dim">
            {entries ? `${entries.length} most recent` : '—'} · newest first
          </span>
          <button
            onClick={load}
            disabled={loading}
            className="mono text-[11px] text-ink-muted hover:text-brass px-2 py-1 rounded
                       focus-ring disabled:opacity-40"
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      }
    >
      <div className="px-6 py-5 flex flex-col gap-3">
        <div className="flex gap-3 items-start bg-panel-hi border border-line rounded-lg px-4 py-3">
          <span className="serif text-lg text-brass leading-none pt-0.5">§</span>
          <p className="text-[12px] text-ink-muted leading-relaxed">
            Solvent never moves money. These entries record what was{' '}
            <strong className="text-ink font-medium">recommended and logged</strong> —
            the actual Instant Settlement or Capital draw stays with the Razorpay
            APIs and requires your explicit confirmation there.
          </p>
        </div>

        {error && (
          <div className="border border-loss/40 bg-loss/10 rounded-lg px-4 py-3 flex flex-col gap-2">
            <span className="text-[13px] text-loss">Could not load your records.</span>
            <span className="mono text-[11px] text-ink-muted break-all">{error}</span>
            <button
              onClick={load}
              className="self-start mono text-[11px] text-brass hover:text-brass-hi focus-ring rounded px-1"
            >
              Try again
            </button>
          </div>
        )}

        {!error && entries === null && (
          <div className="flex flex-col gap-2">
            {[0, 1, 2].map(i => (
              <div key={i} className="h-[68px] rounded-lg border border-line bg-panel-hi animate-pulse" />
            ))}
          </div>
        )}

        {!error && entries?.length === 0 && (
          <div className="border border-dashed border-line rounded-lg px-5 py-10 flex flex-col items-center gap-2 text-center">
            <span className="serif text-[28px] text-ink-dim leading-none">◆</span>
            <span className="text-[13px] text-ink">Nothing recorded yet</span>
            <span className="text-[11.5px] text-ink-muted max-w-[260px] leading-relaxed">
              Execute a Solvent recommendation or view transaction evidence, and it
              will appear here with a timestamp you can quote to your auditor.
            </span>
          </div>
        )}

        {entries?.map((e, i) => {
          const t = fmtTime(e.logged_at)
          const detail = Object.entries(e).filter(([k]) => !HEADER_KEYS.has(k))
          return (
            <article
              key={`${e.logged_at}-${i}`}
              className="border border-line rounded-lg bg-panel-hi px-4 py-3 flex flex-col gap-2.5
                         hover:border-line-hi transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-brass shrink-0" />
                  <span className="text-[13px] text-ink leading-snug">{label(e.action)}</span>
                </div>
                <span className="mono text-[10px] text-ink-dim shrink-0 pt-0.5">{t.rel}</span>
              </div>

              {detail.length > 0 && (
                <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 mono text-[11px] pl-4">
                  {detail.map(([k, v]) => (
                    <div key={k} className="contents">
                      <span className="text-ink-dim">{k.replace(/_/g, ' ')}</span>
                      <span className="text-ink-muted break-all">{fmtValue(v)}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="pl-4 mono text-[10px] text-ink-dim border-t border-line pt-2">
                {t.date} · {t.time} IST
              </div>
            </article>
          )
        })}
      </div>
    </Drawer>
  )
}
