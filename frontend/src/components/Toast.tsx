import { useEffect, useState } from 'react'

/**
 * Minimal toast. Replaces `alert()` so confirming an action does not block
 * the page — and so the confirmation can carry the audit reference the
 * merchant would actually quote.
 */
export interface ToastMsg {
  id: number
  title: string
  detail?: string
  tone?: 'ok' | 'error'
}

type Listener = (t: ToastMsg) => void
const listeners = new Set<Listener>()
let nextId = 1

export function toast(title: string, detail?: string, tone: 'ok' | 'error' = 'ok') {
  const msg: ToastMsg = { id: nextId++, title, detail, tone }
  listeners.forEach(l => l(msg))
}

export function ToastHost() {
  const [items, setItems] = useState<ToastMsg[]>([])

  useEffect(() => {
    const l: Listener = msg => {
      setItems(prev => [...prev, msg])
      window.setTimeout(
        () => setItems(prev => prev.filter(m => m.id !== msg.id)),
        msg.tone === 'error' ? 7000 : 5000,
      )
    }
    listeners.add(l)
    return () => { listeners.delete(l) }
  }, [])

  return (
    <div
      className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[70] flex flex-col gap-2 items-center
                 pointer-events-none"
      role="status"
      aria-live="polite"
    >
      {items.map(m => (
        <div
          key={m.id}
          className={
            'pointer-events-auto flex items-start gap-3 min-w-[320px] max-w-[520px] ' +
            'bg-panel-hi border rounded-lg px-4 py-3 shadow-2xl ' +
            'animate-[toast-in_180ms_ease-out] ' +
            (m.tone === 'error' ? 'border-loss/60' : 'border-brass/60')
          }
        >
          <span
            className={
              'w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ' +
              (m.tone === 'error' ? 'bg-loss' : 'bg-brass')
            }
          />
          <div className="flex flex-col gap-0.5 min-w-0">
            <span className="text-[13px] text-ink leading-snug">{m.title}</span>
            {m.detail && (
              <span className="mono text-[11px] text-ink-muted break-all">{m.detail}</span>
            )}
          </div>
          <button
            onClick={() => setItems(prev => prev.filter(x => x.id !== m.id))}
            aria-label="Dismiss"
            className="ml-auto text-ink-dim hover:text-ink text-lg leading-none px-1 focus-ring rounded"
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  )
}
