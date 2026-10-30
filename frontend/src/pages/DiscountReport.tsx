import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { date, money, num, pct } from '../format'
import { NeedStore } from '../NeedStore'
import type { StoreRow } from '../state'
import { Card, Drawer, Empty, ErrorBox, Field, Kpi, Loading, Source, useSort } from '../ui'

export type ReportLine = {
  name: string; method: string | null; discount_class: string | null; orders: number; discounts: number; gross_sales: number
  net_sales: number; aov: number | null; discount_rate: number | null; codes_in_group: number; last_day: string | null; offers: string[]
  store?: StoreDiscount | null
  placements?: string[]
}
export type StoreDiscount = { title: string; status: string | null; value: number | null; value_type: string | null; min_subtotal: number | null
  min_quantity: number | null; starts_at: string | null; ends_at: string | null; usage_count: number | null; source: string; checked_at: string | null }
type Unused = { name: string; store: StoreDiscount; offers: string[]; placements?: string[] }
type Totals = { orders: number; discounts: number; gross_sales: number; net_sales: number; aov: number | null; discount_rate: number | null }
export type DiscountSummary = {
  report: null | { id: number; period_start: string; period_end: string; source: string; filename: string | null; by_day: boolean }
  totals: { all: Totals; codes: Totals; automatic: Totals }
  lines: ReportLine[]; excluded_tests: string[]; grouped: Record<string, number>; note: string; unused: Unused[]
}

const STATUS_CLS: Record<string, string> = { active: 'good', scheduled: 'info', expired: '', deleted: 'critical' }
export function StoreStatus({ s }: { s: StoreDiscount | null | undefined }) {
  if (!s) return <small className="muted" title="Not in the imported Shopify discounts export">—</small>
  const st = s.status ?? 'unknown'
  return <span className={`badge ${STATUS_CLS[st] ?? ''}`} title={s.checked_at ? `Shopify discounts export imported ${date(s.checked_at)}` : undefined}>{st[0].toUpperCase() + st.slice(1)}</span>
}
function valueText(s: StoreDiscount | null | undefined, cur: string) {
  if (!s) return '—'
  const v = s.value_type === 'percent' || s.value_type === 'bxgy' ? `${s.value}%` : s.value_type === 'fixed' ? money(s.value, cur) : s.value_type === 'free_shipping' ? 'Free shipping' : 'App'
  const min = s.min_subtotal ? ` over ${money(s.min_subtotal, cur)}` : s.min_quantity ? ` on ${s.min_quantity}+ items` : ''
  return v + min
}
function validity(s: StoreDiscount | null | undefined) {
  if (!s) return '—'
  return `${s.starts_at ? date(s.starts_at) : '…'} → ${s.ends_at ? date(s.ends_at) : 'no end'}`
}
type ImportResult = { dry_run: boolean; rows: number; period_start: string; period_end: string; by_day: boolean; codes: number
  automatic: number; test_rows: string[]; replaced_previous: boolean }

export function DiscountCodes() {
  return <NeedStore title="Discount codes">{(s) => <ReportFor store={s} />}</NeedStore>
}

function ReportFor({ store }: { store: StoreRow }) {
  const q = useQuery({ queryKey: ['discount-report', store.id], queryFn: () => api.get<DiscountSummary>(`/stores/${store.id}/discount-report`) })
  const [method, setMethod] = useState<'all' | 'code' | 'automatic'>('all')
  const [status, setStatus] = useState('all')
  const [open, setOpen] = useState<{ code: string; automatic: boolean } | null>(null)
  const lines = (q.data?.lines ?? []).filter((l) => (method === 'all' || (method === 'automatic' ? l.method === 'automatic' : l.method !== 'automatic'))
    && (status === 'all' || (status === 'not_in_export' ? !l.store : l.store?.status === status)))
  const { sorted, th } = useSort(lines, 'orders')
  const cur = store.currency
  const d = q.data
  return (
    <div className="stack">
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !d?.report ? (
        <Card><Empty title="No sales-by-discount report yet">Import the Shopify "Sales by discount code" export below to see orders and sales per code.</Empty></Card>
      ) : (
        <>
          <div className="kpis">
            <Kpi label="Orders with a discount" value={num(d.totals.all.orders)} sub={`${num(d.totals.codes.orders)} with a code · ${num(d.totals.automatic.orders)} automatic`} />
            <Kpi label="Discounts given" value={money(d.totals.all.discounts, cur)} sub={`${pct(d.totals.all.discount_rate)} of their gross sales`} />
            <Kpi label="AOV with a code" value={money(d.totals.codes.aov, cur)} sub="net sales ÷ orders" />
            <Kpi label="AOV with an automatic discount" value={money(d.totals.automatic.aov, cur)} />
            <Kpi label="Net sales with a discount" value={money(d.totals.all.net_sales, cur)} />
          </div>
          <Card title={`Discounts ${date(d.report.period_start)} → ${date(d.report.period_end)}`}
            extra={<div className="row">
              <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status in Shopify">
                <option value="all">Any status</option><option value="active">Active</option><option value="scheduled">Scheduled</option>
                <option value="expired">Expired</option><option value="not_in_export">Not in discounts export</option>
              </select>
              <div className="seg">{(['all', 'code', 'automatic'] as const).map((m) => <button key={m} className={method === m ? 'on' : ''} onClick={() => setMethod(m)}>{m === 'all' ? 'All' : m === 'code' ? 'Codes' : 'Automatic'}</button>)}</div>
            </div>}>
            <div className="table-wrap" style={{ maxHeight: 620 }}>
              <table>
                <thead><tr><th>Discount</th><th>Status</th><th>Value</th><th>Valid</th><th>Shown in</th>{th('orders', 'Orders')}{th('discounts', 'Discounts')}{th('gross_sales', 'Gross sales')}{th('net_sales', 'Net sales')}{th('aov', 'AOV')}{th('discount_rate', 'Discount %')}</tr></thead>
                <tbody>{sorted.map((l) => (
                  <tr key={l.name} className={l.codes_in_group > 1 ? '' : 'clickable'} onClick={() => l.codes_in_group <= 1 && setOpen({ code: l.name, automatic: l.method === 'automatic' })}>
                    <td className={l.method === 'automatic' ? '' : 'mono'} style={{ maxWidth: 320 }}>{l.name}{l.codes_in_group > 1 && <small className="muted"> · {l.codes_in_group} codes</small>}</td>
                    <td><StoreStatus s={l.store} /></td>
                    <td><small>{valueText(l.store, cur)}</small><div><small className="muted">{l.method === 'automatic' ? 'Automatic' : 'Code'}{l.discount_class ? ` · ${l.discount_class}` : ''}</small></div></td>
                    <td style={{ whiteSpace: 'nowrap' }}><small>{validity(l.store)}</small></td>
                    <td style={{ minWidth: 140 }}><ShownIn offers={l.offers} placements={l.placements} /></td>
                    <td className="num">{num(l.orders)}</td><td className="num">{money(l.discounts, cur)}</td><td className="num">{money(l.gross_sales, cur)}</td>
                    <td className="num">{money(l.net_sales, cur)}</td><td className="num">{money(l.aov, cur, 2)}</td><td className="num">{pct(l.discount_rate)}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
            <Source>
              {d.report.source === 'elizeta' ? 'Sales by discount from Eli Z (daily, synced every night)' : `Shopify "Sales by discount code" (${d.report.filename})`}. {d.note}
              {Object.keys(d.grouped).length > 0 && ` Grouped: ${Object.entries(d.grouped).map(([g, n]) => `${g} (${n})`).join(', ')}.`}
              {d.excluded_tests.length > 0 && ` Test codes excluded: ${d.excluded_tests.join(', ')}.`}
            </Source>
          </Card>
        </>
      )}
      {d && d.unused?.length > 0 && (
        <Card title={d.report ? 'Not used in the report period' : 'Store discounts'} extra={<small className="muted">From the Shopify discounts export</small>}>
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table>
              <thead><tr><th>Discount</th><th>Status</th><th>Value</th><th>Valid</th><th>Shown in</th><th className="num">Times used (all time)</th></tr></thead>
              <tbody>{d.unused.filter((u) => status === 'all' || u.store.status === status).map((u) => (
                <tr key={u.name} className="clickable" onClick={() => setOpen({ code: u.name, automatic: u.name.includes(' ') })}><td className="mono">{u.name}</td><td><StoreStatus s={u.store} /></td><td><small>{valueText(u.store, cur)}</small></td>
                  <td style={{ whiteSpace: 'nowrap' }}><small>{validity(u.store)}</small></td><td style={{ minWidth: 140 }}><ShownIn offers={u.offers} placements={u.placements} /></td>
                  <td className="num">{num(u.store.usage_count)}</td></tr>))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      <DiscountImport store={store} />
      <DiscountListImport store={store} />
      {open && <PlacementDrawer store={store} code={open.code} automatic={open.automatic} onClose={() => setOpen(null)} />}
    </div>
  )
}

export function DiscountImport({ store }: { store: StoreRow }) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [period, setPeriod] = useState({ start: '', end: '' })
  const run = useMutation({
    mutationFn: (dry: boolean) => {
      const fd = new FormData()
      fd.append('file', file!)
      fd.append('dry_run', String(dry))
      if (period.start) fd.append('start', period.start)
      if (period.end) fd.append('end', period.end)
      return api.upload<ImportResult>(`/stores/${store.id}/discount-report/import`, fd)
    },
    onSuccess: (r) => { if (!r.dry_run) { qc.invalidateQueries({ queryKey: ['discount-report'] }); qc.invalidateQueries({ queryKey: ['overview'] }) } },
  })
  const r = run.data
  return (
    <Card title={`Import discount report — ${store.code}`}>
      <p className="secondary" style={{ marginTop: 0 }}>Shopify admin → Analytics → Reports → <strong>Sales by discount code</strong> → Export (.jsonl or .csv). The period is read from the file name; set it here if it is missing.</p>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <input type="file" onChange={(e) => { setFile(e.target.files?.[0] ?? null); run.reset() }} />
        <Field label="From (optional)"><input type="date" value={period.start} onChange={(e) => setPeriod({ ...period, start: e.target.value })} /></Field>
        <Field label="To (optional)"><input type="date" value={period.end} onChange={(e) => setPeriod({ ...period, end: e.target.value })} /></Field>
        <button onClick={() => run.mutate(true)} disabled={!file || run.isPending}>Preview</button>
        <button className="primary" onClick={() => run.mutate(false)} disabled={!file || run.isPending || !r?.dry_run}>Import</button>
      </div>
      <ErrorBox error={run.error} />
      {r && (
        <div className="notice info mt"><small>
          {r.dry_run ? 'Preview: ' : 'Imported: '}{r.rows} rows · {date(r.period_start)} → {date(r.period_end)} · {r.codes} codes, {r.automatic} automatic
          {r.by_day ? ' · split by day' : ''}{r.test_rows.length ? ` · test codes excluded from totals: ${r.test_rows.join(', ')}` : ''}
          {r.replaced_previous ? ' · replaces the report already imported for this period' : ''}
        </small></div>
      )}
    </Card>
  )
}

type ListResult = { dry_run: boolean; rows: number; active: number; expired: number; scheduled: number; automatic: number
  created: number; updated: number; possibly_partial: boolean; warnings: string[] }

/** Shopify admin → Discounts → Export: every discount with its status. */
export function DiscountListImport({ store }: { store: StoreRow }) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const run = useMutation({
    mutationFn: (dry: boolean) => {
      const fd = new FormData()
      fd.append('file', file!)
      fd.append('dry_run', String(dry))
      return api.upload<ListResult>(`/stores/${store.id}/discount-list/import`, fd)
    },
    onSuccess: (r) => { if (!r.dry_run) qc.invalidateQueries() },
  })
  const r = run.data
  return (
    <Card title={`Import discounts list with status — ${store.code}`}>
      <p className="secondary" style={{ marginTop: 0 }}>Shopify admin → Discounts → <strong>Export</strong> → choose <strong>All discounts</strong> (CSV). Gives each code its status (Active, Scheduled, Expired) and becomes the evidence for the offers' Technical Status.</p>
      <div className="row">
        <input type="file" onChange={(e) => { setFile(e.target.files?.[0] ?? null); run.reset() }} />
        <button onClick={() => run.mutate(true)} disabled={!file || run.isPending}>Preview</button>
        <button className="primary" onClick={() => run.mutate(false)} disabled={!file || run.isPending || !r?.dry_run}>Import</button>
      </div>
      <ErrorBox error={run.error} />
      {r && <div className="notice info mt"><small>
        {r.dry_run ? 'Preview: ' : 'Imported: '}{r.rows} discounts · {r.active} active · {r.scheduled} scheduled · {r.expired} expired · {r.automatic} automatic/app
        {!r.dry_run && ` · ${r.created} new, ${r.updated} updated`}
      </small></div>}
      {r?.warnings.map((w, i) => <div key={i} className="notice warning mt"><small>{w}</small></div>)}
    </Card>
  )
}

type Placement = { code: string; offers: { id: number; name: string; status: string | null; start_date: string | null; end_date: string | null }[]
  placements: string[]; notes: string | null }
const SUGGESTED = ['Welcome popup', 'Exit-intent popup', 'Abandoned cart email', 'Welcome email flow', 'Newsletter', 'SMS',
  'Influencer / affiliate', 'Customer service', 'Thank-you page', 'Paid social ad']

/** "Shown in": the offers that use the code plus other placements (popups, emails…). */
export function ShownIn({ offers, placements }: { offers: string[]; placements?: string[] }) {
  if (!offers.length && !placements?.length) return <span className="muted">— <small>add</small></span>
  return (
    <span>
      {offers.map((o) => <div key={o}><small>{o}</small></div>)}
      {placements?.map((p) => <span key={p} className="badge tag-manual" style={{ marginRight: 4 }}>{p}</span>)}
    </span>
  )
}

export function PlacementDrawer({ store, code, automatic, onClose }: { store: StoreRow; code: string; automatic: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['placement', store.id, code], queryFn: () => api.get<Placement>(`/stores/${store.id}/discount-placements?code=${encodeURIComponent(code)}`) })
  const events = useQuery({ queryKey: ['events', store.id, 'all-for-link'], queryFn: () => api.get<{ id: number; name: string; start_date: string | null; end_date: string | null; status: string | null }[]>(`/events?store_id=${store.id}`) })
  const [pick, setPick] = useState('')
  const [text, setText] = useState('')
  const [draft, setDraft] = useState<{ placements: string[]; notes: string } | null>(null)
  const cur = draft ?? { placements: q.data?.placements ?? [], notes: q.data?.notes ?? '' }
  const refresh = () => { qc.invalidateQueries({ queryKey: ['discount-report'] }); qc.invalidateQueries({ queryKey: ['events'] }); qc.invalidateQueries({ queryKey: ['placement', store.id, code] }) }
  const link = useMutation({ mutationFn: (b: { event_id: number; unlink?: boolean }) => api.post(`/stores/${store.id}/discount-placements/link`, { code, automatic, ...b }),
    onSuccess: () => { setPick(''); refresh() } })
  const save = useMutation({ mutationFn: () => api.put(`/stores/${store.id}/discount-placements`, { code, placements: cur.placements, notes: cur.notes }),
    onSuccess: () => { setDraft(null); refresh() } })
  const add = (t: string) => { const v = t.trim(); if (v && !cur.placements.some((p) => p.toLowerCase() === v.toLowerCase())) setDraft({ ...cur, placements: [...cur.placements, v] }); setText('') }
  const linked = new Set(q.data?.offers.map((o) => o.id))
  const options = (events.data ?? []).filter((e) => !linked.has(e.id))
  return (
    <Drawer open onClose={onClose} title={<div><h2 className={automatic ? '' : 'mono'}>{code}</h2><small className="muted">Where this discount was shown</small></div>}>
      {q.isLoading ? <Loading /> : (
        <>
          <div className="section-title" style={{ marginTop: 0 }}>Offers</div>
          {!q.data?.offers.length ? <small className="muted">Not linked to any offer.</small> : (
            <table><tbody>{q.data.offers.map((o) => (
              <tr key={o.id}><td><strong>{o.name}</strong><div><small className="muted">{o.start_date ? `${date(o.start_date)} → ${date(o.end_date)}` : 'no dates'} · {o.status ?? 'status not set'}</small></div></td>
                <td style={{ textAlign: 'right' }}><button onClick={() => confirm(`Remove ${code} from "${o.name}"?`) && link.mutate({ event_id: o.id, unlink: true })}>Remove</button></td></tr>))}
            </tbody></table>
          )}
          <div className="row mt">
            <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
              <option value="">Add to an offer from the Offers Plan or DB…</option>
              {options.map((e) => <option key={e.id} value={e.id}>{e.start_date ? `${date(e.start_date)} · ` : ''}{e.name}</option>)}
            </select>
            <button className="primary" disabled={!pick || link.isPending} onClick={() => link.mutate({ event_id: Number(pick) })}>Add</button>
          </div>
          <small className="muted" style={{ display: 'block', marginTop: 4 }}>Adds the {automatic ? 'discount' : 'code'} to that offer: its code results and technical status follow.</small>

          <div className="section-title">Other placements</div>
          <div className="row" style={{ gap: 6 }}>
            {cur.placements.length ? cur.placements.map((p) => (
              <span key={p} className="badge tag-manual">{p}<button className="tag-x" aria-label={`Remove ${p}`} onClick={() => setDraft({ ...cur, placements: cur.placements.filter((x) => x !== p) })}>×</button></span>
            )) : <small className="muted">None — e.g. a popup, an email flow, SMS.</small>}
          </div>
          <form className="row mt" onSubmit={(e) => { e.preventDefault(); add(text) }}>
            <input list="placement-suggest" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Welcome popup" style={{ flex: 1 }} />
            <datalist id="placement-suggest">{SUGGESTED.map((s) => <option key={s} value={s} />)}</datalist>
            <button type="submit" disabled={!text.trim()}>Add</button>
          </form>
          <div className="mt"><Field label="Notes"><textarea value={cur.notes} onChange={(e) => setDraft({ ...cur, notes: e.target.value })} placeholder="e.g. replaced LITTLE10 in the welcome flow from March" /></Field></div>
          <div className="row mt">
            <button className="primary" disabled={!draft || save.isPending} onClick={() => save.mutate()}>Save placements</button>
            {draft && <small className="muted">Unsaved changes</small>}
          </div>
          <ErrorBox error={link.error ?? save.error} />
        </>
      )}
    </Drawer>
  )
}
