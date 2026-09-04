import { useEffect, useRef, useState } from 'react'
import { api, ExplainResp, LLMStatusResp } from '../lib/api'
import { Drawer } from './Drawer'

interface Message {
  role: 'user' | 'bot'
  content: string
  evidence?: string[][] | null
  engine?: ExplainResp['engine']
  llmModel?: string | null
  llmLatencyMs?: number | null
  fallbackReason?: string | null
}

/** Fallback labels. Replaced at mount by the live-computed ones from
 *  `GET /explain/questions`, so a chip can never quote a stale number. */
const FALLBACK_QUESTIONS: { key: string; label: string }[] = [
  { key: 'why_settle_now',      label: 'Why did Solvent recommend this action?' },
  { key: 'whats_the_shortfall', label: "What's driving the shortfall risk?" },
  { key: 'worst_case',          label: "What's the worst case if I do nothing?" },
  { key: 'whats_pending',       label: 'What bills does Solvent already know about?' },
  { key: 'is_this_worth',       label: 'Is the recommended fee actually worth it?' },
]

/** Keyword router — used when LLM is off. Small and deterministic. */
function routeFreeText(text: string): string | null {
  const t = text.toLowerCase()
  if (/why|recommend|suggest|action|advice/.test(t)) return 'why_settle_now'
  if (/shortfall|risk|probability|chance|dip|negative|below zero/.test(t)) return 'whats_the_shortfall'
  if (/worst|nothing|do nothing|downside|lose|tail/.test(t)) return 'worst_case'
  if (/pending|upcoming|bill|expense|outflow|payroll|rent|gst|know/.test(t)) return 'whats_pending'
  if (/fee|worth|cost|save|is |settlement|charge/.test(t)) return 'is_this_worth'
  return null
}

export function ExplainDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [questions, setQuestions] = useState(FALLBACK_QUESTIONS)
  const [messages, setMessages] = useState<Message[]>([])
  const [inputText, setInputText] = useState('')
  const [pending, setPending] = useState(false)
  const [useLlm, setUseLlm] = useState(false)
  const [llmStatus, setLlmStatus] = useState<LLMStatusResp | null>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/explain/questions')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { questions: { key: string; label: string }[] }) => {
        if (d.questions?.length) setQuestions(d.questions)
      })
      .catch(() => { /* keep fallback */ })
    api.llmStatus().then(setLlmStatus).catch(() => setLlmStatus({ available: false, models: [], default_model: '' }))
  }, [])

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [messages, pending])

  async function ask(key: string, questionText?: string) {
    const asked = questionText || questions.find(s => s.key === key)?.label || key
    setMessages(prev => [...prev, { role: 'user', content: asked }])
    setPending(true)
    try {
      const r: ExplainResp = await api.explain(key, useLlm)
      setMessages(prev => [...prev, {
        role: 'bot', content: r.answer, evidence: r.evidence,
        engine: r.engine, llmModel: r.llm_model,
        llmLatencyMs: r.llm_latency_ms, fallbackReason: r.fallback_reason,
      }])
      api.audit({ action: 'explain_asked', question_key: key, engine: r.engine }).catch(() => {})
    } catch (e) {
      setMessages(prev => [...prev, {
        role: 'bot',
        content:
          'I could not reach the numbers behind that answer just now. ' +
          'Nothing on the page has changed — try again in a moment.',
      }])
    } finally {
      setPending(false)
    }
  }

  async function askFree(text: string) {
    setMessages(prev => [...prev, { role: 'user', content: text }])
    setPending(true)
    try {
      const r: ExplainResp = await api.explainFree(text, useLlm)
      setMessages(prev => [...prev, {
        role: 'bot', content: r.answer, evidence: r.evidence,
        engine: r.engine, llmModel: r.llm_model,
        llmLatencyMs: r.llm_latency_ms, fallbackReason: r.fallback_reason,
      }])
    } catch (e) {
      setMessages(prev => [...prev, { role: 'bot', content: 'Sorry — something went wrong reaching the answer.' }])
    } finally {
      setPending(false)
    }
  }

  function handleSubmit() {
    const t = inputText.trim()
    if (!t || pending) return
    setInputText('')
    if (useLlm) {
      // Route free-text through the LLM classifier
      askFree(t)
      return
    }
    // Deterministic keyword route
    const key = routeFreeText(t)
    if (key) { ask(key, t); return }
    setMessages(prev => [...prev, { role: 'user', content: t }, {
      role: 'bot',
      content:
        'I only answer from the numbers on this page, and I do not have an ' +
        'evidence chain for that one. Try one of the suggested questions — or ' +
        'turn on Qwen (below) to route free-text queries.',
    }])
  }

  const showChips = messages.length === 0
  const llmAvailable = !!llmStatus?.available

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Explain this"
      subtitle={
        <>
          Answers computed from your own transactions. Numbers guaranteed,
          {useLlm ? ' paraphrased by a local LLM (grounded in evidence).' : ' rendered deterministically.'}
        </>
      }
      footer={
        <>
          {/* LLM toggle strip */}
          <div className="px-5 py-2 border-b border-line flex items-center justify-between text-[11px] mono">
            <div className="flex items-center gap-3">
              <button
                onClick={() => llmAvailable && setUseLlm(!useLlm)}
                disabled={!llmAvailable}
                className={
                  'inline-flex items-center gap-2 px-2 py-1 rounded border transition-colors ' +
                  (!llmAvailable
                    ? 'bg-panel border-line text-ink-dim cursor-not-allowed'
                    : useLlm
                      ? 'bg-brass/15 border-brass text-brass'
                      : 'bg-panel border-line text-ink-muted hover:text-ink hover:border-line-hi')
                }
              >
                <span className={
                  'inline-block w-2 h-2 rounded-full ' +
                  (useLlm ? 'bg-brass' : llmAvailable ? 'bg-ink-dim' : 'bg-ink-dim opacity-40')
                } />
                {useLlm ? 'LLM · Qwen 2.5 7B (local)' : 'Deterministic mode'}
              </button>
              {llmAvailable && useLlm && (
                <span className="text-ink-dim">grounded in evidence · numbers checked</span>
              )}
              {!llmAvailable && (
                <span className="text-ink-dim">Ollama not detected · deterministic only</span>
              )}
            </div>
          </div>
          <div className="px-5 py-3.5 flex gap-2">
            <input
              type="text"
              value={inputText}
              onChange={e => setInputText(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSubmit()}
              placeholder={useLlm ? 'Ask anything about your cash position…' : 'Ask about a number on this page…'}
              aria-label="Ask a question"
              className="flex-1 bg-ground border border-line text-ink text-[13px] px-3 py-2.5
                         rounded-md outline-none focus:border-brass placeholder:text-ink-dim"
            />
            <button
              onClick={handleSubmit}
              disabled={pending || !inputText.trim()}
              className="bg-brass text-[#14100a] font-semibold text-[13px] px-4 rounded-md
                         hover:bg-brass-hi focus-ring disabled:opacity-40 disabled:hover:bg-brass"
            >
              Ask
            </button>
          </div>
        </>
      }
    >
      <div ref={bodyRef} className="h-full overflow-y-auto px-6 py-5 flex flex-col gap-4">
        <p className="text-[13px] text-ink-muted leading-relaxed">
          Ask about your <strong className="text-ink font-medium">money coming in</strong>,
          your <strong className="text-ink font-medium">settlements</strong>, or what a{' '}
          <strong className="text-ink font-medium">recommendation means</strong>.
          Every number in the answer is guaranteed to come from your real transactions —
          the LLM (if you enable it) is only allowed to rewrite the phrasing.
        </p>

        {showChips && (
          <div className="flex flex-col gap-2 pt-1">
            <div className="text-[10px] uppercase tracking-[0.08em] text-ink-dim font-medium mb-0.5">
              Suggested questions
            </div>
            {questions.map(s => (
              <button
                key={s.key}
                onClick={() => ask(s.key)}
                className="text-left border border-line text-ink text-[13px] px-3 py-2 rounded-md
                           hover:border-brass hover:bg-brass-tint transition focus-ring"
              >
                {s.label}
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => <Bubble key={i} msg={m} />)}

        {pending && (
          <div className="flex items-center gap-2 text-xs text-ink-muted mono">
            <span className="w-1.5 h-1.5 rounded-full bg-brass animate-pulse" />
            {useLlm ? 'Qwen thinking…' : 'Pulling the numbers…'}
          </div>
        )}

        {!showChips && !pending && (
          <button
            onClick={() => setMessages([])}
            className="self-start mono text-[11px] text-ink-dim hover:text-brass rounded px-1 focus-ring"
          >
            ← back to suggested questions
          </button>
        )}
      </div>
    </Drawer>
  )
}

function EngineBadge({ msg }: { msg: Message }) {
  if (!msg.engine || msg.engine === 'deterministic') {
    return <span className="text-[10px] text-ink-dim mono">Solvent · deterministic</span>
  }
  if (msg.engine === 'llm') {
    return (
      <span className="text-[10px] text-ink-dim mono">
        Solvent · Qwen 2.5 7B <span className="opacity-70">· {msg.llmLatencyMs?.toFixed(0) ?? '?'}ms · grounded ✓</span>
      </span>
    )
  }
  // llm_fallback
  return (
    <span className="text-[10px] text-ink-dim mono">
      Solvent · <span className="text-loss">LLM fell back</span>
      <span className="opacity-70"> · {msg.fallbackReason?.replace('LLM output contains numbers not in evidence:', 'stray:')}</span>
    </span>
  )
}

function Bubble({ msg }: { msg: Message }) {
  const isUser = msg.role === 'user'
  return (
    <div className={'flex flex-col ' + (isUser ? 'items-end' : 'items-start')}>
      <div
        className={
          'max-w-[88%] px-3.5 py-2.5 rounded-xl text-[13.5px] leading-relaxed border ' +
          (isUser
            ? 'bg-brass-tint border-brass text-ink'
            : msg.engine === 'llm'
              ? 'bg-panel-hi border-brass/40 text-ink'
              : 'bg-panel-hi border-line text-ink')
        }
      >
        <div>{msg.content}</div>
        {msg.evidence && msg.evidence.length > 0 && (
          <div className="mt-2.5 bg-ground rounded-md p-2.5 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5
                          mono text-[12px] tabular">
            {msg.evidence.map(([k, v], i) => {
              const cls =
                v.startsWith('−') || v.startsWith('-') ? 'text-loss'
                : v.startsWith('+') ? 'text-gain'
                : 'text-ink'
              return (
                <div key={i} className="contents">
                  <span className="text-ink-muted">{k}</span>
                  <span className={`text-right font-medium ${cls}`}>{v}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
      <span className="px-1 mt-0.5">
        {isUser ? <span className="text-[10px] text-ink-dim mono">you</span> : <EngineBadge msg={msg} />}
      </span>
    </div>
  )
}
