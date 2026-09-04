import { useEffect, useState } from 'react'
import { CashPosition } from './pages/CashPosition'
import { CashflowDrilldown } from './pages/CashflowDrilldown'
import { CustomMerchant } from './pages/CustomMerchant'
import { ExplainDrawer } from './components/ExplainDrawer'
import { AuditDrawer } from './components/AuditDrawer'
import { ToastHost } from './components/Toast'

type Tab = 'position' | 'drilldown' | 'custom'

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    if (typeof window === 'undefined') return 'position'
    if (window.location.hash === '#drilldown') return 'drilldown'
    if (window.location.hash === '#custom') return 'custom'
    return 'position'
  })
  const [explainOpen, setExplainOpen] = useState(false)
  const [auditOpen, setAuditOpen] = useState(false)

  // Keep the hash in step with the tab so a demo can deep-link either view.
  useEffect(() => {
    window.location.hash =
      tab === 'drilldown' ? '#drilldown' :
      tab === 'custom' ? '#custom' :
      '#position'
  }, [tab])

  // Only one panel at a time — two stacked drawers would fight for focus.
  function openExplain() { setAuditOpen(false); setExplainOpen(true) }
  function openAudit() { setExplainOpen(false); setAuditOpen(true) }

  return (
    <div className="min-h-screen bg-ground text-ink">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[80]
                   focus:bg-brass focus:text-[#14100a] focus:px-3 focus:py-2 focus:rounded
                   focus:text-[13px] focus:font-semibold"
      >
        Skip to content
      </a>

      {/* Ticker strip */}
      <div className="border-b border-line bg-ground px-8 py-1.5 mono text-[11px] text-ink-muted flex items-center gap-6 overflow-hidden whitespace-nowrap">
        <span className="w-1.5 h-1.5 rounded-full bg-gain animate-pulse" />
        <TickerItem pair="USD/INR" val="88.42" chg="+0.14" up />
        <span className="text-ink-dim">·</span>
        <TickerItem pair="EUR/INR" val="95.18" chg="−0.22" />
        <span className="text-ink-dim">·</span>
        <TickerItem pair="GBP/INR" val="112.63" chg="+0.31" up />
        <span className="text-ink-dim">·</span>
        <span className="flex items-center gap-1.5">
          <span className="text-ink-dim">Market moves (60d)</span>
          <span className="text-ink">Moderate · 8.2%</span>
        </span>
        <span className="text-ink-dim">·</span>
        <span className="flex items-center gap-1.5">
          <span className="text-ink-dim">Updated</span>
          <span className="text-ink">14:32:07 IST</span>
        </span>
      </div>

      {/* Top nav */}
      <header className="sticky top-0 z-20 bg-ground border-b border-line px-8 py-3.5 flex items-center gap-8">
        <div className="flex items-baseline gap-2.5">
          <span className="w-[22px] h-[22px] bg-brass rotate-45 relative top-1 inline-block" />
          <span className="serif text-2xl text-ink">Solvent</span>
          <span className="pl-3 ml-2 border-l border-line-hi text-[11px] uppercase tracking-[0.05em] text-ink-muted">
            Cashflow Intelligence · Razorpay Merchants
          </span>
        </div>

        <nav className="flex gap-1 mx-auto" aria-label="Views">
          <TabButton active={tab === 'position'} onClick={() => setTab('position')}>
            Cash Position
          </TabButton>
          <TabButton active={tab === 'drilldown'} onClick={() => setTab('drilldown')}>
            Cashflow Drilldown
          </TabButton>
          <TabButton active={tab === 'custom'} onClick={() => setTab('custom')}>
            Try your own merchant
          </TabButton>
        </nav>

        <div className="flex gap-2 items-center">
          <button
            onClick={openAudit}
            className="inline-flex items-center gap-1.5 mono text-[11px] text-ink-muted
                       px-2.5 py-1 border border-line rounded-full
                       hover:text-ink hover:border-line-hi transition-colors focus-ring"
          >
            <span className="serif text-[13px] leading-none">◆</span>
            Your records
          </button>
          <span className="inline-flex items-center gap-1.5 mono text-[11px] text-ink-muted
                           px-2.5 py-1 border border-line rounded-full">
            <span className="w-1.5 h-1.5 rounded-full bg-gain animate-pulse" />
            Live · Razorpay sandbox
          </span>
        </div>
      </header>

      <main id="main" className="max-w-[1360px] mx-auto px-8 py-10 pb-28">
        {tab === 'position' ? <CashPosition />
          : tab === 'drilldown' ? <CashflowDrilldown />
          : <CustomMerchant />}
      </main>

      {/* Explain-this FAB */}
      <button
        onClick={openExplain}
        aria-label="Open Explain this"
        title="Explain this"
        className="fixed bottom-7 right-7 z-30 w-14 h-14 rounded-full bg-brass text-[#14100a]
                   flex items-center justify-center shadow-[0_6px_24px_rgba(0,0,0,0.45)]
                   hover:bg-brass-hi hover:scale-105 active:scale-95 transition-transform
                   focus-ring"
      >
        <span className="serif text-[26px] leading-none relative -top-px">§</span>
      </button>

      <ExplainDrawer open={explainOpen} onClose={() => setExplainOpen(false)} />
      <AuditDrawer open={auditOpen} onClose={() => setAuditOpen(false)} />
      <ToastHost />
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={
        'text-[13.5px] font-medium px-4 py-2 rounded transition-colors focus-ring ' +
        (active
          ? 'text-ink bg-panel shadow-[inset_0_-2px_0_#e4a63c]'
          : 'text-ink-muted hover:text-ink')
      }
    >
      {children}
    </button>
  )
}

function TickerItem({ pair, val, chg, up }: { pair: string; val: string; chg: string; up?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-ink font-medium">{pair}</span>
      <span className="text-ink tabular">{val}</span>
      <span className={up ? 'text-gain' : 'text-loss'}>{chg}</span>
    </span>
  )
}
