import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from './api'
import { label, money, pct, signedPct } from './format'

export function Card({ title, extra, children, className = '' }: { title?: ReactNode; extra?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || extra) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {extra}
        </div>
      )}
      {children}
    </section>
  )
}

export function PageHead({ title, desc, actions }: { title: string; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {desc && <p>{desc}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  )
}

export function Kpi({ label: l, value, sub, delta, invert, title }: {
  label: string; value: ReactNode; sub?: ReactNode; delta?: number | null; invert?: boolean; title?: string
}) {
  const good = delta == null ? null : invert ? delta < 0 : delta > 0
  return (
    <div className="kpi" title={title}>
      <div className="k-label">{l}</div>
      <div className="k-value">{value}</div>
      <div className="k-sub">
        {delta != null && (
          <span className={good ? 'delta-up' : delta === 0 ? '' : 'delta-down'}>
            {delta > 0 ? '▲' : delta < 0 ? '▼' : '■'} {signedPct(delta)}{' '}
          </span>
        )}
        {sub}
      </div>
    </div>
  )
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <div className="loading">{what}…</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error)
  return <div className="error-box" role="alert">{msg}</div>
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  )
}

export function DemoBadge() {
  return <span className="badge demo" title="Synthetic demo data — not a real business">DEMO</span>
}

const STATUS_CLASS: Record<string, string> = {
  connected: 'good', fresh: 'good', success: 'good', verified_in_store: 'good', strong: 'good', high: 'good',
  succeeded: 'good', approved: 'info', live: 'good', adopt: 'good', positive: 'good',
  configured: 'info', planned: 'info', running: 'info', medium: 'info', moderate: 'info', measuring: 'info',
  pending_approval: 'warning', stale: 'warning', partial: 'warning', never_synced: 'warning', weak: 'warning',
  mismatch: 'critical', not_found_in_store: 'critical', error: 'critical', failed: 'critical', critical: 'critical',
  warning: 'warning', negative: 'critical', rejected: 'critical', low: '',
}
const STATUS_ICON: Record<string, string> = { good: '●', warning: '▲', critical: '✕', info: '◆', '': '○' }

export function Status({ value, text }: { value: string | null | undefined; text?: string }) {
  const cls = STATUS_CLASS[value ?? ''] ?? ''
  return (
    <span className={`badge ${cls}`}>
      <span aria-hidden>{STATUS_ICON[cls]}</span>
      {text ?? label(value)}
    </span>
  )
}

export function Confidence({ value, reason }: { value: string; reason?: string }) {
  return (
    <span className={`badge ${STATUS_CLASS[value] ?? ''}`} title={reason}>
      Confidence: {value}
    </span>
  )
}

export type Impact = { metric?: string; low?: number | null; base?: number | null; high?: number | null; unit?: string; basis?: string }

export function ImpactView({ impact, currency }: { impact: Impact | null | undefined; currency: string }) {
  if (!impact) return <span className="muted">Not estimated — see missing data.</span>
  const cur = impact.unit && impact.unit.length === 3 ? impact.unit : currency
  return (
    <div>
      <div className="impact">
        {impact.low != null && <div><small>Low</small>{money(impact.low, cur)}</div>}
        <div><small>{impact.low != null ? 'Base' : 'Estimate'}</small><strong>{money(impact.base ?? null, cur)}</strong></div>
        {impact.high != null && <div><small>High</small>{money(impact.high, cur)}</div>}
      </div>
      <small>{impact.metric}. {impact.basis}</small>
    </div>
  )
}

export function Seg<T extends string | number>({ value, options, onChange }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void
}) {
  return (
    <div className="seg" role="group">
      {options.map((o) => (
        <button key={String(o.value)} className={o.value === value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Dropdown with a checkbox per option: unchecked options are hidden. `hidden` holds the unchecked values. */
export function CheckDropdown({ label: text, options, hidden, onChange }: {
  label: string; options: { value: string; label: ReactNode; count?: number }[]; hidden: string[]; onChange: (hidden: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  const shown = options.filter((o) => !hidden.includes(o.value)).length
  const summary = shown === options.length ? 'All' : shown === 0 ? 'None' : `${shown} of ${options.length}`
  return (
    <div className="check-dd" ref={ref}>
      <button type="button" className={hidden.length ? 'on' : ''} aria-expanded={open} onClick={() => setOpen(!open)}>
        {text}: <strong>{summary}</strong> ▾
      </button>
      {open && (
        <div className="check-dd-menu" role="menu">
          {options.map((o) => (
            <label key={o.value} className="check-dd-item">
              <input type="checkbox" checked={!hidden.includes(o.value)}
                onChange={() => onChange(hidden.includes(o.value) ? hidden.filter((x) => x !== o.value) : [...hidden, o.value])} />
              <span>{o.label}</span>{o.count != null && <small className="muted">{o.count}</small>}
            </label>
          ))}
          <div className="check-dd-foot">
            <button type="button" className="ghost small" onClick={() => onChange([])}>Show all</button>
            <button type="button" className="ghost small" onClick={() => onChange(options.map((o) => o.value))}>Hide all</button>
          </div>
        </div>
      )}
    </div>
  )
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" className={t.id === value ? 'on' : ''} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  if (!open) return null
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>{title}</div>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </aside>
    </>
  )
}

export function Share({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="muted">—</span>
  return (
    <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
      <div className="bar-track" style={{ width: 60 }}>
        <div className="bar-inline" style={{ width: `${Math.min(100, value * 100)}%` }} />
      </div>
      <span className="num">{pct(value)}</span>
    </div>
  )
}

export function Source({ children }: { children: ReactNode }) {
  return <small className="muted" style={{ display: 'block', marginTop: 6 }}>Source: {children}</small>
}

export function Field({ label: l, children }: { label: string; children: ReactNode }) {
  return <label className="field">{l}{children}</label>
}

export function useSort<T>(rows: T[], initial: keyof T, desc = true) {
  const [key, setKey] = useState<keyof T>(initial)
  const [d, setD] = useState(desc)
  const sorted = [...rows].sort((a, b) => {
    const av = a[key] as unknown as number | string | null
    const bv = b[key] as unknown as number | string | null
    if (av == null) return 1
    if (bv == null) return -1
    const c = av < bv ? -1 : av > bv ? 1 : 0
    return d ? -c : c
  })
  const th = (k: keyof T, text: string, numeric = true, extra = '') => (
    <th className={`sortable ${numeric ? 'num' : ''} ${extra}`} onClick={() => (k === key ? setD(!d) : (setKey(k), setD(true)))}>
      {text}{k === key ? (d ? ' ↓' : ' ↑') : ''}
    </th>
  )
  return { sorted, th }
}

/** Minimal, safe Markdown rendering for AI commentary (headings, bullets, bold). No HTML injection. */
export function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const out: ReactNode[] = []
  let list: string[] = []
  const flush = () => {
    if (list.length) out.push(<ul className="tight" key={out.length}>{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>)
    list = []
  }
  for (const raw of lines) {
    const l = raw.trimEnd()
    if (/^\s*[-*] /.test(l)) { list.push(l.replace(/^\s*[-*] /, '')); continue }
    flush()
    if (/^#{1,4} /.test(l)) out.push(<h3 key={out.length}>{l.replace(/^#+ /, '')}</h3>)
    else if (l.trim()) out.push(<p key={out.length}>{inline(l)}</p>)
  }
  flush()
  return <div className="md">{out}</div>
}

function inline(s: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : <span key={i}>{part}</span>,
  )
}
