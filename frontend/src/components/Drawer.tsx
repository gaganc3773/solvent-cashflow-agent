import { useEffect, useRef } from 'react'

/**
 * Shared right-side drawer shell.
 *
 * Handles the things every drawer needs and none of them should re-implement:
 * backdrop, slide transition, Escape to close, focus moved in on open and
 * returned to the trigger on close, and a focus trap while open.
 */
export function Drawer({
  open, onClose, title, subtitle, children, footer, width = 400,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: React.ReactNode
  children: React.ReactNode
  footer?: React.ReactNode
  width?: number
}) {
  const panelRef = useRef<HTMLElement>(null)
  const restoreTo = useRef<HTMLElement | null>(null)

  // Callers pass an inline arrow for onClose, so it is a new function on every
  // render. Hold it in a ref and key the effect on `open` alone — otherwise the
  // effect re-runs mid-open and re-captures `restoreTo` as the close button,
  // and focus never finds its way back to whatever opened the drawer.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    restoreTo.current = document.activeElement as HTMLElement | null
    // Move focus into the panel so screen readers and keyboards follow it.
    const t = window.setTimeout(() => {
      const first = panelRef.current?.querySelector<HTMLElement>(
        'button, input, [href], select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      ;(first ?? panelRef.current)?.focus()
    }, 60)

    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab' || !panelRef.current) return
      const focusables = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button, input, [href], select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(el => !el.hasAttribute('disabled'))
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKey)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener('keydown', onKey)
      restoreTo.current?.focus()
    }
  }, [open])

  return (
    <>
      <div
        onClick={onClose}
        aria-hidden="true"
        className={
          'fixed inset-0 z-40 bg-black/50 transition-opacity duration-300 ' +
          (open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none')
        }
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-hidden={!open}
        tabIndex={-1}
        style={{ width }}
        className={
          'fixed top-0 right-0 max-w-full h-screen z-50 outline-none ' +
          'bg-panel border-l border-line flex flex-col shadow-2xl ' +
          'transition-transform duration-300 ease-out ' +
          (open ? 'translate-x-0' : 'translate-x-full')
        }
      >
        <header className="px-6 py-5 border-b border-line flex items-start justify-between gap-4 shrink-0">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2.5">
              <span className="w-2 h-2 rounded-full bg-gain animate-pulse" />
              <h2 className="serif text-xl leading-none">{title}</h2>
            </div>
            {subtitle && <div className="text-[11px] text-ink-muted leading-snug">{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 shrink-0 rounded flex items-center justify-center text-ink-muted
                       hover:text-ink hover:bg-panel-hi text-2xl leading-none
                       focus-ring"
            aria-label={`Close ${title}`}
          >
            &times;
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">{children}</div>

        {footer && <div className="shrink-0 border-t border-line">{footer}</div>}
      </aside>
    </>
  )
}
