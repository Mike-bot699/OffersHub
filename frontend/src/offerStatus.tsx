// Offer status (operational stage, set by people or by store sync for detected offers) and
// technical status (what the connected store shows) are kept apart everywhere they appear.
import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { date, label, money, num, pct } from './format'
import { DemoBadge, ErrorBox, useSort } from './ui'
import { BannerThumb } from './banner'

export type OfferStatus = 'in_progress' | 'scheduled' | 'live' | 'paused' | 'completed' | 'cancelled'
export type HistoryEntry = { at: string; from: string | null; to: string | null; source: string; actor: string; note: string | null }

export type Ev = {
  id: number; store_id: number; store_code: string; currency: string; is_demo: boolean; name: string; offer_text: string | null
  offer_type: string | null; start_date: string | null; end_date: string | null; channels: string[]
  display: string | null; activation: string | null; coupon_codes: string[]; threshold_amount: number | null; threshold_text: string | null
  bonus: string | null; offer_orders: number | null; total_orders: number | null; offer_sales: number | null
  offer_order_share: number | null; results_source: string | null; verification_status: string; data_issues: string[]
  targets: Record<string, number>; analysis: string | null; conclusions: string | null; notes: string | null; source: string; source_ref: string | null
  status: OfferStatus | null; status_label: string; status_source: string | null; status_changed_at: string | null
  status_history: HistoryEntry[]; review_prompts: string[]; conflict_open: boolean; needs_review: boolean
  status_conflict: string | null; technical_status: string; technical_evidence: string | null; technical_checked_at: string | null
  technical_verified: boolean; shopify_discount_id: string | null; sync_status_from_store: boolean
  description: string | null; level: 'store' | 'collection' | 'product' | null; level_targets: string[]
  landing_page: string | null; articles: string | null; banner_message: string | null
  has_banner: boolean; banner_kind: string | null; banner_filename: string | null
  duration_days: number | null; code_active_after_end: boolean
  linked_discounts: string[]; tags: string[]
  discount_results: null | {
    status: 'ok' | 'no_match' | 'after_report'; report_period?: [string, string]; report_end?: string; by_day?: boolean
    matched?: string[]; missing?: string[]; orders?: number; discounts?: number; net_sales?: number; aov?: number | null
    shared_with?: number; late?: { orders: number; discounts: number } | null
  }
}

export const OFFER_STATUSES: { value: OfferStatus; label: string; icon: string; hint: string }[] = [
  { value: 'in_progress', label: 'In Progress', icon: '◐', hint: 'Being prepared, configured or implemented; not live yet' },
  { value: 'scheduled', label: 'Scheduled', icon: '◷', hint: 'Ready and planned to launch at a future date' },
  { value: 'live', label: 'Live', icon: '●', hint: 'Launched and currently running' },
  { value: 'paused', label: 'Paused', icon: '❚❚', hint: 'Temporarily suspended' },
  { value: 'completed', label: 'Completed', icon: '✓', hint: 'Ended as planned' },
  { value: 'cancelled', label: 'Cancelled', icon: '✕', hint: 'Will not be launched, or was cancelled' },
]
const BY_VALUE = Object.fromEntries(OFFER_STATUSES.map((s) => [s.value, s]))
// Sort order follows the offer lifecycle, with "not set" last.
const ORDER: Record<string, number> = { in_progress: 1, scheduled: 2, live: 3, paused: 4, completed: 5, cancelled: 6 }

export function OfferStatusBadge({ status }: { status: string | null }) {
  const s = status ? BY_VALUE[status] : null
  if (!s) return <span className="badge os-unset" title="No status recorded yet — needs review"><span aria-hidden>?</span>Not set</span>
  return <span className={`badge os-${s.value}`} title={s.hint}><span aria-hidden>{s.icon}</span>{s.label}</span>
}

const TECH: Record<string, { text: string; cls: string; icon: string }> = {
  active: { text: 'Active in store', cls: 'good', icon: '●' },
  inactive: { text: 'Inactive in store', cls: 'warning', icon: '○' },
  unavailable: { text: 'Not found in store', cls: 'critical', icon: '✕' },
  unknown: { text: 'Not verified', cls: 'ts-unknown', icon: '–' },
}

export function TechStatusBadge({ ev }: { ev: Pick<Ev, 'technical_status' | 'technical_evidence'> }) {
  const t = TECH[ev.technical_status] ?? TECH.unknown
  return <span className={`badge ${t.cls}`} title={ev.technical_evidence ?? undefined}><span aria-hidden>{t.icon}</span>{t.text}</span>
}

export const statusRank = (s: string | null) => (s ? ORDER[s] ?? 9 : 9)

/** Multi-select filter by offer status, with counts. An empty selection shows everything. */
export function StatusFilter({ rows, value, onChange }: { rows: Ev[]; value: string[]; onChange: (v: string[]) => void }) {
  const count = (s: string) => rows.filter((r) => (r.status ?? 'not_set') === s).length
  const opts = [...OFFER_STATUSES.map((s) => ({ value: s.value as string, label: s.label })), { value: 'not_set', label: 'Not set' }]
  const toggle = (s: string) => onChange(value.includes(s) ? value.filter((x) => x !== s) : [...value, s])
  return (
    <div className="chips" role="group" aria-label="Filter by offer status">
      <button className={value.length === 0 ? 'chip on' : 'chip'} onClick={() => onChange([])}>All <span className="muted">{rows.length}</span></button>
      {opts.map((o) => (
        <button key={o.value} className={value.includes(o.value) ? 'chip on' : 'chip'} aria-pressed={value.includes(o.value)} onClick={() => toggle(o.value)}>
          <OfferStatusBadge status={o.value === 'not_set' ? null : o.value} /> <span className="muted">{count(o.value)}</span>
        </button>
      ))}
    </div>
  )
}

type Row = Ev & { _status: number; _tech: string; _start: string }

/** The one offers table used by Live offers, Offer planning and the Offer database. */
export function OffersTable({ rows, onOpen, showResults = false, empty }: { rows: Ev[]; onOpen: (e: Ev) => void; showResults?: boolean; empty: ReactNode }) {
  const [filter, setFilter] = useState<string[]>([])
  const [reviewOnly, setReviewOnly] = useState(false)
  const { tagChips, tagMatch } = useTagFilter(rows)
  const shown = rows.filter((r) => (filter.length === 0 || filter.includes(r.status ?? 'not_set')) && (!reviewOnly || r.needs_review) && tagMatch(r))
  const { sorted, th } = useSort<Row>(shown.map((r) => ({ ...r, _status: statusRank(r.status), _tech: r.technical_status, _start: r.start_date ?? '' })), '_start')
  const toReview = rows.filter((r) => r.needs_review).length
  return (
    <>
      <div className="row" style={{ marginBottom: 10, justifyContent: 'space-between' }}>
        <StatusFilter rows={rows} value={filter} onChange={setFilter} />
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={reviewOnly} onChange={(e) => setReviewOnly(e.target.checked)} />
          <span className="secondary">Needs review only ({toReview})</span>
        </label>
      </div>
      {tagChips}
      {!sorted.length ? empty : (
        <div className="table-wrap" style={{ maxHeight: 640 }}>
          <table>
            <thead><tr>
              {th('_start', 'Dates', false)}<th>Store</th><th>Offer</th><th>Codes</th>
              {th('_status', 'Offer status', false)}{th('_tech', 'Technical status', false)}
              {showResults && <><th className="num">Offer orders</th><th className="num">Share of orders</th><th className="num">Offer sales</th></>}
            </tr></thead>
            <tbody>{sorted.map((e) => (
              <tr key={e.id} className="clickable" onClick={() => onOpen(e)}>
                <td style={{ whiteSpace: 'nowrap' }}>{e.start_date ? `${date(e.start_date)} → ${date(e.end_date)}` : <span className="muted">no dates</span>}</td>
                <td>{e.is_demo ? <DemoBadge /> : <span className="badge">{e.store_code}</span>}</td>
                <td style={{ minWidth: 220 }}>
                  <strong>{e.name}</strong>
                  {e.data_issues?.length > 0 && <span className="badge warning" title={e.data_issues.join('\n')} style={{ marginLeft: 6 }}>▲ {e.data_issues.length}</span>}
                  <div><small className="secondary">{e.offer_text ?? '—'}</small></div>
                  {e.sync_status_from_store && <div><small className="muted" title="Offer status follows the store's discount">⇄ Follows store</small></div>}
                </td>
                <td className="mono">{e.coupon_codes.join(', ') || '—'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <OfferStatusBadge status={e.status} />
                  {e.conflict_open && <div><span className="badge critical" title={e.status_conflict ?? ''}>▲ Conflict — review</span></div>}
                  {!e.conflict_open && e.review_prompts.length > 0 && <div><small className="warning-ink" title={e.review_prompts.join('\n')}>Review: {e.review_prompts[0]}</small></div>}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}><TechStatusBadge ev={e} /></td>
                {showResults && <>
                  <td className="num">{num(e.offer_orders)}</td>
                  <td className="num">{pct(e.offer_order_share)}</td>
                  <td className="num">{money(e.offer_sales, e.currency)}</td>
                </>}
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

const SOURCE_TEXT: Record<string, string> = { manual: 'Manual', import: 'Workbook import', store_sync: 'Store sync', review: 'Conflict review', demo: 'Demo data' }

/** Status section of the offer drawer: change, review conflicts, technical evidence, history. */
export function StatusPanel({ ev, onChanged }: { ev: Ev; onChanged: (e: Ev) => void }) {
  const qc = useQueryClient()
  const [next, setNext] = useState<string>(ev.status ?? '')
  const [note, setNote] = useState('')
  const done = (e: Ev) => { qc.invalidateQueries({ queryKey: ['events'] }); qc.invalidateQueries({ queryKey: ['live-offers'] }); setNote(''); onChanged(e) }
  const change = useMutation({ mutationFn: () => api.post<Ev>(`/events/${ev.id}/status`, { status: next, note: note || null }), onSuccess: done })
  const review = useMutation({ mutationFn: () => api.post<Ev>(`/events/${ev.id}/status/review`, { note: note || null }), onSuccess: done })
  const recheck = useMutation({ mutationFn: async () => { await api.post(`/events/${ev.id}/verify`); return api.get<Ev>(`/events/${ev.id}`) }, onSuccess: done })
  const follow = useMutation({ mutationFn: (v: boolean) => api.patch<Ev>(`/events/${ev.id}`, { sync_status_from_store: v }), onSuccess: done })
  const overridesStore = ev.sync_status_from_store && ev.technical_verified
  return (
    <div className="status-panel">
      <div className="status-cols">
        <div>
          <div className="k-label">Offer status <small className="muted">(operational stage)</small></div>
          <div className="row" style={{ gap: 6, marginTop: 4 }}>
            <OfferStatusBadge status={ev.status} />
            {ev.status_changed_at && <small className="muted">since {date(ev.status_changed_at)} · {SOURCE_TEXT[ev.status_source ?? ''] ?? label(ev.status_source)}</small>}
          </div>
        </div>
        <div>
          <div className="k-label">Technical status <small className="muted">(connected store)</small></div>
          <div className="row" style={{ gap: 6, marginTop: 4 }}>
            <TechStatusBadge ev={ev} />
            {ev.technical_checked_at && <small className="muted">checked {date(ev.technical_checked_at)}</small>}
          </div>
          <small className="secondary" style={{ display: 'block', marginTop: 4 }}>
            {ev.technical_evidence}
            {!ev.technical_verified && ' The offer status is managed by you and is not confirmed by the store.'}
          </small>
        </div>
      </div>

      {ev.conflict_open && (
        <div className="notice critical mt">
          <strong>Status conflict — review required.</strong> {ev.status_conflict}
          <div><small>Change the offer status below if the store is right, or mark it reviewed if the offer status is right (for example, the store has not been re-synced yet).</small></div>
        </div>
      )}
      {!ev.conflict_open && ev.status_conflict && <div className="notice info mt"><small>Reviewed conflict: {ev.status_conflict}</small></div>}
      {ev.review_prompts.length > 0 && (
        <div className="notice warning mt"><strong>Check the status</strong>
          <ul className="tight">{ev.review_prompts.map((p, i) => <li key={i}>{p}</li>)}</ul>
          <small>Dates alone never change the status: confirm what actually happened.</small>
        </div>
      )}

      <div className="row mt" style={{ alignItems: 'flex-end' }}>
        <label className="field" style={{ minWidth: 160 }}>Set offer status
          <select value={next} onChange={(e) => setNext(e.target.value)}>
            {!ev.status && <option value="">Not set</option>}
            {OFFER_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 180 }}>Note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why the status changes" />
        </label>
        <button className="primary" disabled={!next || next === ev.status || change.isPending} onClick={() => change.mutate()}>Update status</button>
        {ev.conflict_open && <button disabled={review.isPending} onClick={() => review.mutate()}>Mark reviewed</button>}
      </div>
      {overridesStore && next !== (ev.status ?? '') && <small className="warning-ink">This offer follows the store. A manual change stops that, so the next sync will not undo it.</small>}

      <div className="row mt" style={{ gap: 12 }}>
        <button onClick={() => recheck.mutate()} disabled={recheck.isPending}>{recheck.isPending ? 'Checking…' : 'Re-check store'}</button>
        {ev.shopify_discount_id && (
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={ev.sync_status_from_store} disabled={follow.isPending} onChange={(e) => follow.mutate(e.target.checked)} />
            <span className="secondary">Follow the store's status for this offer</span>
          </label>
        )}
      </div>
      <ErrorBox error={change.error ?? review.error ?? recheck.error ?? follow.error} />

      <div className="section-title">Status history</div>
      {!ev.status_history.length ? <small className="muted">No status change recorded.</small> : (
        <ol className="history">
          {[...ev.status_history].reverse().map((h, i) => (
            <li key={i}>
              <span className="mono muted">{new Date(h.at).toLocaleString()}</span>{' '}
              {h.source === 'review' ? <strong>Conflict reviewed</strong> : <>
                <OfferStatusBadge status={h.from} /> → <OfferStatusBadge status={h.to} />
              </>}
              <small className="muted"> · {SOURCE_TEXT[h.source] ?? label(h.source)}</small>
              {h.note && <div><small className="secondary">{h.note}</small></div>}
            </li>))}
        </ol>
      )}
    </div>
  )
}

const DISPLAY_TEXT: Record<string, string> = { full_site: 'Full-Displayed', email_only: 'Email-Only', hidden: 'Hidden' }

export function LevelCell({ e }: { e: Pick<Ev, 'level' | 'level_targets'> }) {
  if (!e.level) return <span className="muted">—</span>
  const what = e.level === 'store' ? 'Store' : e.level === 'collection' ? 'Collection' : 'Product'
  return <span><span className="badge">{what}</span> <small>{e.level === 'store' || !e.level_targets.length ? 'All' : e.level_targets.join(', ')}</small></span>
}

export function TechCell({ e }: { e: Ev }) {
  return (
    <div style={{ display: 'grid', gap: 3, justifyItems: 'start' }}>
      <TechStatusBadge ev={e} />
      {e.code_active_after_end && <span className="badge warning" title="The offer has ended but its code still works in the store: late redemptions are possible and are counted apart in Analysis.">▲ Code active after end</span>}
    </div>
  )
}

type PlanRow = Ev & { _status: number; _start: string; _end: string; _dur: number; _store: string; _name: string }

/** Offers Plan / Offers DB table, with the columns of the marketing calendar. */
/** Common event tags, offered as suggestions; any other text is allowed. */
export const SUGGESTED_TAGS = ['Halloween', 'BFCM', 'Black Friday', 'Cyber Monday', 'Christmas', 'New Year', "Valentine's Day",
  'Easter', "Mother's Day", "Father's Day", 'Summer', 'Back to School', 'Prime Day', 'Birthday', 'Evergreen', 'Clearance']

// Fixed hues for the main seasonal events so they read the same everywhere; other tags get a stable hue from their text.
const TAG_HUES: Record<string, number> = { halloween: 28, bfcm: 0, 'black friday': 0, 'cyber monday': 200, christmas: 140, 'new year': 50,
  "valentine's day": 335, easter: 280, "mother's day": 315, "father's day": 215, summer: 45, 'back to school': 190, 'prime day': 205 }

export function tagHue(t: string) {
  const k = t.toLowerCase()
  if (k in TAG_HUES) return TAG_HUES[k]
  let h = 0
  for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

export function TagChip({ tag, onRemove }: { tag: string; onRemove?: () => void }) {
  return (
    <span className="tag-chip" style={{ ['--h' as string]: tagHue(tag) }}>
      {tag}{onRemove && <button type="button" aria-label={`Remove ${tag}`} onClick={onRemove}>×</button>}
    </span>
  )
}

/** Tag suggestions for an event whose name, description or collections mention a known one (shown for a click, never applied on their own). */
export function suggestTags(name: string, have: string[]) {
  const n = name.toLowerCase()
  const hits = SUGGESTED_TAGS.filter((t) => n.includes(t.toLowerCase().replace("'s day", '')))
  if (/black\s*friday|\bbf\b|cyber/.test(n)) hits.push('BFCM')
  if (/xmas|natale/.test(n)) hits.push('Christmas')
  return [...new Set(hits)].filter((t) => !have.some((h) => h.toLowerCase() === t.toLowerCase()))
}

/** Tag filter chips for an offers table (only shown once some offer has a tag); "No tag" finds offers still to tag. */
function useTagFilter(rows: Ev[]) {
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const allTags = [...new Set(rows.flatMap((r) => r.tags ?? []))].sort((a, b) => a.localeCompare(b))
  const untagged = rows.filter((r) => !r.tags?.length).length
  const tagMatch = (r: Ev) => tagFilter.length === 0 || tagFilter.some((t) => (t === '' ? !r.tags?.length : r.tags?.includes(t)))
  const tagChips = (
    allTags.length > 0 && (
        <div className="chips" style={{ marginBottom: 10 }}>
          <small className="muted" style={{ alignSelf: 'center' }}>Tags</small>
          {allTags.map((t) => (
            <button key={t} type="button" className={`chip tag-filter ${tagFilter.includes(t) ? 'on' : ''}`} style={{ ['--h' as string]: tagHue(t) }}
              onClick={() => setTagFilter(tagFilter.includes(t) ? tagFilter.filter((x) => x !== t) : [...tagFilter, t])}>
              {t} <small className="muted">{rows.filter((r) => r.tags?.includes(t)).length}</small>
            </button>))}
          {untagged > 0 && <button type="button" className={`chip ${tagFilter.includes('') ? 'on' : ''}`}
            onClick={() => setTagFilter(tagFilter.includes('') ? tagFilter.filter((x) => x !== '') : [...tagFilter, ''])}>No tag <small className="muted">{untagged}</small></button>}
          {tagFilter.length > 0 && <button type="button" className="ghost small" onClick={() => setTagFilter([])}>Clear</button>}
        </div>
    )
  )
  return { tagChips, tagMatch }
}

export function PlanTable({ rows, onOpen, showResults = false, empty }: { rows: Ev[]; onOpen: (e: Ev) => void; showResults?: boolean; empty: ReactNode }) {
  const [filter, setFilter] = useState<string[]>([])
  const [reviewOnly, setReviewOnly] = useState(false)
  const { tagChips, tagMatch } = useTagFilter(rows)
  const shown = rows.filter((r) => (filter.length === 0 || filter.includes(r.status ?? 'not_set')) && (!reviewOnly || r.needs_review) && tagMatch(r))
  const { sorted, th } = useSort<PlanRow>(shown.map((r) => ({ ...r, _status: statusRank(r.status), _start: r.start_date ?? '', _end: r.end_date ?? '',
    _dur: r.duration_days ?? -1, _store: r.store_code, _name: r.name })), '_start', !!showResults)
  const toReview = rows.filter((r) => r.needs_review).length
  return (
    <>
      <div className="row" style={{ marginBottom: 10, justifyContent: 'space-between' }}>
        <StatusFilter rows={rows} value={filter} onChange={setFilter} />
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={reviewOnly} onChange={(e) => setReviewOnly(e.target.checked)} />
          <span className="secondary">Needs review only ({toReview})</span>
        </label>
      </div>
      {tagChips}
      {!sorted.length ? empty : (
        <div className="table-wrap plan-table">
          <table>
            <thead><tr>
              {th('_start', 'Start Date', false)}{th('_end', 'End Date', false)}{th('_dur', 'Duration')}{th('_name', 'Name', false, 'name-cell')}
              {th('_store', 'Store', false)}{th('_status', 'Status', false)}<th>Description</th><th>Level</th><th>Type</th><th>Offer</th>
              <th>Discount Code</th><th>Code results</th><th className="num">Threshold</th><th>Technical Status</th><th>Bonus</th><th>Landing Page</th><th>Articles</th><th>Displayed</th>
              {showResults && <><th className="num">Offer orders</th><th className="num">Share of orders</th><th className="num">Offer sales</th></>}
            </tr></thead>
            <tbody>{sorted.map((e) => (
              <tr key={e.id} className="clickable" onClick={() => onOpen(e)}>
                <td style={{ whiteSpace: 'nowrap' }}>{date(e.start_date)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{date(e.end_date)}</td>
                <td className="num" style={{ whiteSpace: 'nowrap' }}>{e.duration_days != null ? `${e.duration_days}d` : '—'}</td>
                <td className="name-cell"><strong>{e.name}</strong>
                  {e.data_issues?.length > 0 && <span className="badge warning" title={e.data_issues.join('\n')} style={{ marginLeft: 6 }}>▲ {e.data_issues.length}</span>}
                  {e.tags?.length > 0 && <div className="tag-row">{e.tags.map((t) => <TagChip key={t} tag={t} />)}</div>}</td>
                <td>{e.is_demo ? <DemoBadge /> : <span className="badge">{e.store_code}</span>}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <OfferStatusBadge status={e.status} />
                  {e.conflict_open && <div><span className="badge critical" title={e.status_conflict ?? ''}>▲ Conflict — review</span></div>}
                  {!e.conflict_open && e.review_prompts.length > 0 && <div><small className="warning-ink" title={e.review_prompts.join('\n')}>Review needed</small></div>}
                </td>
                <td className="clip" title={e.description ?? ''}><small>{e.description ?? '—'}</small></td>
                <td style={{ minWidth: 120 }}><LevelCell e={e} /></td>
                <td><small>{e.offer_type ? label(e.offer_type) : '—'}</small></td>
                <td className="clip" title={e.offer_text ?? ''}><small>{e.offer_text ?? '—'}</small></td>
                <td className="mono">{e.coupon_codes.join(', ') || <span className="muted">{e.activation === 'automatic' ? 'Automatic' : '—'}</span>}
                  {e.linked_discounts?.length > 0 && <div><small className="muted" style={{ fontFamily: 'inherit' }}>+ {e.linked_discounts.join(', ')}</small></div>}</td>
                <td style={{ whiteSpace: 'nowrap' }}><CodeResults e={e} /></td>
                <td className="num">{e.threshold_amount != null ? money(e.threshold_amount, e.currency) : <small className="muted">{e.threshold_text && e.threshold_text !== 'none' ? e.threshold_text : '—'}</small>}</td>
                <td style={{ whiteSpace: 'nowrap' }}><TechCell e={e} /></td>
                <td className="clip" title={e.bonus ?? ''}><small>{e.bonus ?? '—'}</small></td>
                <td>{e.landing_page ? <a href={e.landing_page} target="_blank" rel="noreferrer noopener" onClick={(x) => x.stopPropagation()}><small>{e.landing_page.replace(/^https?:\/\/(www\.)?/, '').slice(0, 40)}</small></a> : <span className="muted">—</span>}</td>
                <td className="clip" title={e.articles ?? ''}><small>{e.articles ?? '—'}</small></td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {e.display ? <small>{DISPLAY_TEXT[e.display] ?? label(e.display)}</small> : <span className="muted">—</span>}
                  {e.has_banner && <div style={{ marginTop: 4 }}><BannerThumb eventId={e.id} name={e.name} /></div>}
                </td>
                {showResults && <>
                  <td className="num">{num(e.offer_orders)}</td>
                  <td className="num">{pct(e.offer_order_share)}</td>
                  <td className="num">{money(e.offer_sales, e.currency)}</td>
                </>}
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

/** What the offer's codes did according to the imported discount report. */
export function CodeResults({ e }: { e: Ev }) {
  const r = e.discount_results
  if (!r) return <span className="muted">—</span>
  if (r.status === 'after_report') return <small className="muted" title={`The discount report ends ${date(r.report_end ?? null)}`}>after report</small>
  if (r.status === 'no_match') return <small className="muted" title="No discount with this name in the imported report">not in report</small>
  const period = r.report_period ? `${date(r.report_period[0])} → ${date(r.report_period[1])}` : ''
  return (
    <div title={`Discount report ${period}${r.by_day ? ', offer dates only' : ', whole report period'}${r.missing?.length ? ` · not in report: ${r.missing.join(', ')}` : ''}`}>
      <div><strong>{num(r.orders ?? 0)}</strong> <small className="muted">orders</small></div>
      <small>{money(r.net_sales ?? 0, e.currency)} net · {money(r.discounts ?? 0, e.currency)} disc.</small>
      {!!r.shared_with && <div><span className="badge warning" title="This code is also used by other offers: without daily data its totals cannot be split between them">shared ×{r.shared_with + 1}</span></div>}
      {r.late && r.late.orders > 0 && <div><span className="badge warning">{num(r.late.orders)} late</span></div>}
      {!r.by_day && !r.shared_with && <div><small className="muted">whole report period</small></div>}
    </div>
  )
}
