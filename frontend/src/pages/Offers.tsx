import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, qs } from '../api'
import { date, label, money, num, pct } from '../format'
import { useApp } from '../state'
import { Card, Drawer, Empty, ErrorBox, Field, Loading, PageHead, Source, Status, Tabs } from '../ui'
import { OFFER_STATUSES, OfferStatusBadge, PlanTable, StatusPanel, TechStatusBadge, type Ev } from '../offerStatus'
import { BannerView, useBanner } from '../banner'
import { DiscountCodes, DiscountImport } from './DiscountReport'

type Alert = { severity: string; kind: string; title: string; detail: string; store_code: string }

export function OffersPlan() {
  const { storeId, includeDemo } = useApp()
  const [tab, setTab] = useState('plan')
  const [edit, setEdit] = useState<Ev | 'new' | null>(null)
  const q = useQuery({ queryKey: ['events', storeId, 'plan', includeDemo], queryFn: () => api.get<Ev[]>(`/events${qs({ store_id: storeId, include_demo: includeDemo, when: 'plan' })}`) })
  const rows = q.data ?? []
  const n = (s: string) => rows.filter((r) => r.status === s).length
  return (
    <>
      <PageHead title="Offers Plan" desc="Offers running today or planned for the future — from the marketing calendar and the custom offers you add. Past offers move to the Offers DB."
        actions={<button className="primary" onClick={() => setEdit('new')}>New offer</button>} />
      <div className="kpis" style={{ marginBottom: 14 }}>
        <div className="kpi"><div className="k-label">Live</div><div className="k-value">{n('live')}</div></div>
        <div className="kpi"><div className="k-label">Scheduled</div><div className="k-value">{n('scheduled')}</div></div>
        <div className="kpi"><div className="k-label">In progress</div><div className="k-value">{n('in_progress')}</div></div>
        <div className="kpi"><div className="k-label">Paused</div><div className="k-value">{n('paused')}</div></div>
        <div className="kpi"><div className="k-label">Need review</div><div className={`k-value ${rows.some((r) => r.conflict_open) ? 'critical-ink' : ''}`}>{rows.filter((r) => r.needs_review).length}</div><div className="k-sub">status or dates to confirm</div></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'plan', label: 'Plan' }, { id: 'alerts', label: 'Monitoring' }]} />
      {tab === 'plan' && (
        <Card>
          {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
            <PlanTable rows={rows} onOpen={setEdit} empty={<Empty title="Nothing planned">Import the marketing calendar (Offers DB → Import) or create an offer.</Empty>} />
          )}
          <Source>Offers with future dates are Scheduled automatically; Live, Paused, Completed and Cancelled are set by you (dates alone never make an offer Live). Technical Status is what the store shows for the offer's code — including codes still active after the offer ended.</Source>
        </Card>
      )}
      {tab === 'alerts' && <Alerts />}
      <EventEditor ev={edit} onClose={() => setEdit(null)} />
    </>
  )
}

export function OffersDB() {
  const [tab, setTab] = useState('expired')
  return (
    <>
      <PageHead title="Offers DB" desc="Every offer that has ended, with its documented results — plus offer templates, coupon codes, what worked and the workbook import." />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'expired', label: 'Expired offers' }, { id: 'discounts', label: 'Discount codes' }, { id: 'history', label: 'What worked' }, { id: 'coupons', label: 'Store coupons' },
        { id: 'library', label: 'Offer templates' }, { id: 'import', label: 'Import' },
      ]} />
      {tab === 'expired' && <ExpiredOffers />}
      {tab === 'discounts' && <DiscountCodes />}
      {tab === 'library' && <Library />}
      {tab === 'coupons' && <Coupons />}
      {tab === 'history' && <History />}
      {tab === 'import' && <div className="stack"><ImportWorkbook /><ImportDiscounts /></div>}
    </>
  )
}

function ExpiredOffers() {
  const { storeId, includeDemo } = useApp()
  const [edit, setEdit] = useState<Ev | 'new' | null>(null)
  const q = useQuery({ queryKey: ['events', storeId, 'expired', includeDemo], queryFn: () => api.get<Ev[]>(`/events${qs({ store_id: storeId, include_demo: includeDemo, when: 'expired' })}`) })
  return (
    <Card>
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
        <PlanTable rows={q.data ?? []} onOpen={setEdit} showResults empty={<Empty title="No expired offers">Offers appear here once their end date has passed.</Empty>} />
      )}
      <Source>Recorded results are as documented (spreadsheet or manual). "Share of orders" counts orders that used the offer — not orders caused by it. Imported history gets a status only where the workbook proves it; the rest is "Not set" for you to confirm.</Source>
      <EventEditor ev={edit} onClose={() => setEdit(null)} />
    </Card>
  )
}

function EventEditor({ ev, onClose }: { ev: Ev | 'new' | null; onClose: () => void }) {
  const { stores, storeId, meta } = useApp()
  const qc = useQueryClient()
  const isNew = ev === 'new'
  // Keep the drawer in step with status changes made inside it.
  const [fresh, setFresh] = useState<Ev | null>(null)
  const base = isNew || ev === null ? null : fresh && fresh.id === ev.id ? fresh : ev
  const [f, setF] = useState<Record<string, string>>({})
  const v = (k: string, fallback: unknown) => (k in f ? f[k] : fallback == null ? '' : String(fallback))
  const results = useQuery({ queryKey: ['event-results', base?.id], enabled: !!base,
    queryFn: () => api.get<{ measured: Record<string, number | string | boolean> | null }>(`/events/${base!.id}/results`) })
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {}
      const num = (s: string) => (s === '' ? null : Number(s))
      for (const [k, val] of Object.entries(f)) {
        if (['threshold_amount', 'offer_orders', 'total_orders', 'offer_sales', 'est_incentive_cost'].includes(k)) body[k] = num(val)
        else if (k === 'coupon_codes' || k === 'channels' || k === 'level_targets') body[k] = val.split(',').map((x) => x.trim()).filter(Boolean)
        else if (k === 'target_orders') body.targets = { ...(base?.targets ?? {}), offer_orders: num(val) }
        else body[k] = val === '' ? null : val
      }
      if (isNew) return api.post('/events', { store_id: Number(f.store_id ?? storeId ?? stores[0]?.id), name: f.name ?? '', ...body })
      return api.patch(`/events/${base!.id}`, body)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['events'] }); setF({}); setFresh(null); onClose() },
  })
  const verify = useMutation({ mutationFn: () => api.post<{ status: string; findings: string[] }>(`/events/${base!.id}/verify`),
    onSuccess: async () => { qc.invalidateQueries({ queryKey: ['events'] }); qc.invalidateQueries({ queryKey: ['live-offers'] }); setFresh(await api.get<Ev>(`/events/${base!.id}`)) } })
  const del = useMutation({ mutationFn: () => api.del(`/events/${base!.id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['events'] }); onClose() } })
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  if (ev === null) return null
  const m = results.data?.measured
  return (
    <Drawer open onClose={() => { setF({}); setFresh(null); onClose() }} title={<h2>{isNew ? 'New offer' : base!.name}</h2>}>
      {base && (
        <div className="row" style={{ marginBottom: 10 }}>
          <span className="badge">{base.store_code}</span><OfferStatusBadge status={base.status} /><TechStatusBadge ev={base} />
          <small className="muted">Source: {label(base.source)}{base.source_ref ? ` (${base.source_ref})` : ''}</small>
        </div>
      )}
      {base && <StatusPanel key={`${base.id}-${base.status}`} ev={base} onChanged={setFresh} />}
      {base && <div className="section-title">Offer details</div>}
      {base?.data_issues?.length ? <div className="notice warning" style={{ marginBottom: 10 }}><strong>Data issues found on import</strong><ul className="tight">{base.data_issues.map((d, i) => <li key={i}>{d}</li>)}</ul></div> : null}
      <div className="form-grid">
        {isNew && <Field label="Store"><select value={f.store_id ?? storeId ?? stores[0]?.id ?? ''} onChange={set('store_id')}>{stores.filter((s) => !s.is_demo).map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Field>}
        <Field label="Name"><input value={v('name', base?.name)} onChange={set('name')} /></Field>
        <Field label="Offer type"><select value={v('offer_type', base?.offer_type)} onChange={set('offer_type')}><option value="">—</option>{meta?.offer_types.map((t) => <option key={t} value={t}>{label(t)}</option>)}</select></Field>
        {isNew && <Field label="Offer status"><select value={f.status ?? ''} onChange={set('status')}><option value="">Automatic (Scheduled if it starts later)</option>{OFFER_STATUSES.map((s) => <option key={s.value} value={s.value} title={s.hint}>{s.label}</option>)}</select></Field>}
        <Field label="Start"><input type="date" value={v('start_date', base?.start_date)} onChange={set('start_date')} /></Field>
        <Field label="End"><input type="date" value={v('end_date', base?.end_date)} onChange={set('end_date')} /></Field>
        <Field label="Coupon codes (comma separated)"><input value={v('coupon_codes', base?.coupon_codes.join(', '))} onChange={set('coupon_codes')} /></Field>
        <Field label="Threshold amount"><input type="number" value={v('threshold_amount', base?.threshold_amount)} onChange={set('threshold_amount')} /></Field>
        <Field label="Activation"><select value={v('activation', base?.activation)} onChange={set('activation')}><option value="">—</option><option value="manual_code">Manual code</option><option value="automatic">Automatic</option></select></Field>
        <Field label="Channels (comma separated)"><input value={v('channels', base?.channels.join(', '))} onChange={set('channels')} /></Field>
        <Field label="Target offer orders"><input type="number" value={v('target_orders', base?.targets?.offer_orders)} onChange={set('target_orders')} /></Field>
        <Field label="Level"><select value={v('level', base?.level)} onChange={set('level')}><option value="">—</option><option value="store">Store (all)</option><option value="collection">Collection</option><option value="product">Product</option></select></Field>
        <Field label="Collections / products (comma separated, empty = All)"><input value={v('level_targets', base?.level_targets?.join(', '))} onChange={set('level_targets')} /></Field>
        <Field label="Displayed"><select value={v('display', base?.display)} onChange={set('display')}><option value="">—</option><option value="full_site">Full-Displayed</option><option value="email_only">Email-Only</option><option value="hidden">Hidden</option></select></Field>
        <Field label="Landing page"><input value={v('landing_page', base?.landing_page)} onChange={set('landing_page')} placeholder="https://…" /></Field>
      </div>
      <div className="mt"><Field label="Description"><textarea value={v('description', base?.description)} onChange={set('description')} /></Field></div>
      <div className="mt"><Field label="Offer"><textarea value={v('offer_text', base?.offer_text)} onChange={set('offer_text')} /></Field></div>
      <div className="mt"><Field label="Bonus / gift"><input value={v('bonus', base?.bonus)} onChange={set('bonus')} /></Field></div>
      <div className="mt"><Field label="Articles"><input value={v('articles', base?.articles)} onChange={set('articles')} /></Field></div>
      <div className="mt"><Field label="Banner message"><textarea value={v('banner_message', base?.banner_message)} onChange={set('banner_message')} /></Field></div>
      {base && <BannerEditor ev={base} />}
      <div className="section-title">Recorded results (documented)</div>
      <div className="form-grid">
        <Field label="Offer orders"><input type="number" value={v('offer_orders', base?.offer_orders)} onChange={set('offer_orders')} /></Field>
        <Field label="Total orders in period"><input type="number" value={v('total_orders', base?.total_orders)} onChange={set('total_orders')} /></Field>
        <Field label="Offer sales"><input type="number" value={v('offer_sales', base?.offer_sales)} onChange={set('offer_sales')} /></Field>
      </div>
      <div className="mt"><Field label="Analysis & conclusions"><textarea value={v('conclusions', base?.conclusions)} onChange={set('conclusions')} /></Field></div>
      {base && (
        <>
          <div className="section-title">Measured from store orders</div>
          {!m ? <small>Needs dates, at least one coupon code and synced orders.</small> : m.available === false ? <small>{String(m.reason)}</small> : (
            <div className="kpis">
              <div className="kpi"><div className="k-label">Orders using the code</div><div className="k-value">{num(Number(m.offer_orders))}</div><div className="k-sub">of {num(Number(m.total_orders))} ({pct(Number(m.offer_order_share))})</div></div>
              <div className="kpi"><div className="k-label">Net sales of those orders</div><div className="k-value">{money(Number(m.offer_net_sales), base.currency)}</div></div>
              <div className="kpi"><div className="k-label">Discount given</div><div className="k-value">{money(Number(m.discount_cost), base.currency)}</div></div>
              <div className="kpi"><div className="k-label">AOV offer vs other</div><div className="k-value" style={{ fontSize: 16 }}>{money(Number(m.aov_offer_orders), base.currency)} / {money(Number(m.aov_other_orders), base.currency)}</div></div>
            </div>
          )}
          {m && m.note && <small className="muted">{String(m.note)}</small>}
          <div className="section-title">Configuration check against the store</div>
          <small className="secondary" style={{ display: 'block', marginBottom: 6 }}>Compares codes, threshold and dates with the store's synced discounts. It also refreshes the technical status; it never changes the offer status.</small>
          <button onClick={() => verify.mutate()} disabled={verify.isPending}>Check configuration</button>
          {verify.data && <div className="notice info mt"><Status value={verify.data.status} /><ul className="tight">{verify.data.findings.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
        </>
      )}
      <div className="row mt">
        <button className="primary" onClick={() => save.mutate()} disabled={save.isPending || (!isNew && Object.keys(f).length === 0)}>Save</button>
        {base && <button className="danger" onClick={() => confirm(`Delete event "${base.name}"?`) && del.mutate()}>Delete</button>}
      </div>
      {isNew && <div className="notice info mt"><small>Technical status starts as <strong>Not verified</strong>: it is checked against the connected store's synced discounts by the offer's coupon codes.</small></div>}
      <div className="mt"><ErrorBox error={save.error ?? verify.error ?? del.error} /></div>
    </Drawer>
  )
}

function BannerEditor({ ev }: { ev: Ev }) {
  const qc = useQueryClient()
  const banner = useBanner(ev.id, ev.has_banner)
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const isHtml = /\.html?$/i.test(file.name) || file.type === 'text/html'
      const content = isHtml ? await file.text() : await new Promise<string>((ok, ko) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = ko; r.readAsDataURL(file) })
      return api.put(`/events/${ev.id}/banner`, { kind: isHtml ? 'html' : 'image', content, filename: file.name })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['banner', ev.id] }); qc.invalidateQueries({ queryKey: ['events'] }) },
  })
  const remove = useMutation({ mutationFn: () => api.del(`/events/${ev.id}/banner`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['banner', ev.id] }); qc.invalidateQueries({ queryKey: ['events'] }) } })
  return (
    <>
      <div className="section-title">Banner design</div>
      {banner.data?.content && <div style={{ marginBottom: 8 }}><BannerView banner={banner.data} width={360} /><small className="muted">{banner.data.filename}</small></div>}
      <div className="row">
        <input type="file" accept=".html,.htm,image/png,image/jpeg,image/webp" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
        {ev.has_banner && <button onClick={() => remove.mutate()}>Remove</button>}
      </div>
      <small className="muted">HTML design file (the element with id "banner-desktop" is used for the preview) or an image, up to 3 MB. Shown as a preview in the Offers Plan and Offers DB.</small>
      <ErrorBox error={upload.error ?? remove.error} />
    </>
  )
}

function Alerts() {
  const { storeId, includeDemo } = useApp()
  const q = useQuery({ queryKey: ['alerts-all', storeId, includeDemo], queryFn: () => api.get<Alert[]>(`/alerts${qs({ store_id: storeId, include_demo: includeDemo })}`) })
  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  return (
    <Card title="Monitoring" extra={<small>Upcoming & live events, configuration, overlaps, code leakage, discount pressure</small>}>
      {!q.data?.length ? <small>Nothing needs attention.</small> : (
        <div className="stack" style={{ gap: 8 }}>
          {q.data.map((a, i) => (
            <div key={i} className={`notice ${a.severity}`}>
              <div className="row" style={{ gap: 6 }}><Status value={a.severity} /><span className="badge">{a.store_code}</span><span className="badge">{label(a.kind)}</span><strong>{a.title}</strong></div>
              {a.detail && <small className="secondary">{a.detail}</small>}
            </div>))}
        </div>)}
      <Source>Configuration errors are only reported when the store's discounts have been synced; otherwise events are marked unverifiable.</Source>
    </Card>
  )
}

function Library() {
  const { storeId } = useApp()
  const q = useQuery({ queryKey: ['offers', storeId], queryFn: () => api.get<{ id: number; name: string; offer_type: string; mechanic: Record<string, unknown>; description: string | null; status: string; source: string; event_count: number }[]>(`/offers${qs({ store_id: storeId })}`) })
  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  return (
    <Card title="Offer templates" extra={<small>Reusable mechanics; offers reference them. Templates have no status — each offer that uses one has its own.</small>}>
      {!q.data?.length ? <Empty title="No offers yet">They are created from imported events or added manually.</Empty> : (
        <table>
          <thead><tr><th>Offer</th><th>Type</th><th>Mechanic</th><th className="num">Used in offers</th><th>Source</th></tr></thead>
          <tbody>{q.data.map((o) => (
            <tr key={o.id}><td>{o.name}{o.description && <div><small className="muted">{o.description}</small></div>}</td><td>{label(o.offer_type)}</td>
              <td className="mono" style={{ fontSize: 11.5 }}>{Object.entries(o.mechanic).filter(([k]) => k !== 'inferred').map(([k, val]) => `${k}: ${JSON.stringify(val)}`).join(' · ')}{o.mechanic.inferred ? <span className="badge" style={{ marginLeft: 4 }}>inferred</span> : null}</td>
              <td className="num">{o.event_count}</td><td>{label(o.source)}</td></tr>))}
          </tbody>
        </table>)}
    </Card>
  )
}

function Coupons() {
  const { storeId } = useApp()
  const q = useQuery({ queryKey: ['coupons', storeId], queryFn: () => api.get<{ id: number; store_code: string; code: string; source: string; title: string | null; value_type: string | null; value: number | null; min_subtotal: number | null; min_quantity: number | null; starts_at: string | null; ends_at: string | null; status: string | null; usage_count: number | null; channel: string | null }[]>(`/coupons${qs({ store_id: storeId })}`) })
  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  return (
    <Card title="Coupons" extra={<small>"Shopify" = synced from the store; "spreadsheet import" = documented only</small>}>
      {!q.data?.length ? <small>No coupons.</small> : (
        <div className="table-wrap" style={{ maxHeight: 600 }}>
          <table>
            <thead><tr><th>Store</th><th>Code</th><th>Source</th><th>Value</th><th className="num">Minimum</th><th>Valid</th><th className="num">Uses</th><th>Status in store</th></tr></thead>
            <tbody>{q.data.map((c) => (
              <tr key={c.id}><td><span className="badge">{c.store_code}</span></td><td className="mono">{c.code}</td><td>{label(c.source)}</td>
                <td>{c.value_type === 'percent' ? `${c.value}%` : c.value_type === 'fixed' ? `${c.value} off` : label(c.value_type)}</td>
                <td className="num">{c.min_subtotal ?? (c.min_quantity ? `${c.min_quantity} items` : '—')}</td>
                <td><small>{date(c.starts_at)} → {c.ends_at ? date(c.ends_at) : <strong>no end</strong>}</small></td>
                <td className="num">{num(c.usage_count)}</td><td><Status value={c.status} /></td></tr>))}
            </tbody>
          </table>
        </div>)}
    </Card>
  )
}

function History() {
  const { storeId, includeDemo } = useApp()
  const q = useQuery({ queryKey: ['offer-history', storeId, includeDemo], queryFn: () => api.get<{ events_used: number; note: string; by: Record<string, { key: string; events: number; median_offer_order_share: number; p25: number; p75: number }[]> }>(`/offer-history${qs({ store_id: storeId, include_demo: includeDemo })}`) })
  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  return (
    <div className="grid g2">
      {(['type', 'display'] as const).map((dim) => (
        <Card key={dim} title={dim === 'type' ? 'By offer type' : 'By display / channel'} extra={<small>{d.events_used} ended events with results</small>}>
          {!d.by[dim]?.length ? <small>No ended events with recorded results.</small> : (
            <table><thead><tr><th>{dim === 'type' ? 'Offer type' : 'Display'}</th><th className="num">Events</th><th className="num">Median share of orders</th><th className="num">Middle 50%</th></tr></thead>
              <tbody>{d.by[dim].map((r) => (
                <tr key={r.key}><td>{label(r.key)}</td><td className="num">{r.events}</td><td className="num">{pct(r.median_offer_order_share)}</td><td className="num">{pct(r.p25)} – {pct(r.p75)}</td></tr>))}
              </tbody></table>)}
          <Source>{d.note}</Source>
        </Card>
      ))}
    </div>
  )
}

type ImportReport = {
  filename: string; dry_run: boolean; sheets_found: string[]; sheets_ignored: string[]; events_parsed: number; events_created: number
  events_updated: number; offers_created: number; coupons_upserted: number; rows_skipped: number; stores_existing: string[]
  stores_created: string[]; store_settings_updated: string[]; issues: { sheet: string; row: number; store: string | null; event: string | null; issue: string }[]
  issue_counts: Record<string, number>
}

function ImportDiscounts() {
  const { store } = useApp()
  if (!store || store.is_demo) return <Card title="Import discount report (.jsonl / .csv)"><small>Select a store in the top bar to import its Shopify "Sales by discount code" export.</small></Card>
  return <DiscountImport store={store} />
}

function ImportWorkbook() {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [force, setForce] = useState(false)
  const run = useMutation({
    mutationFn: (dry: boolean) => {
      const fd = new FormData()
      fd.append('file', file!)
      fd.append('dry_run', String(dry))
      fd.append('force', String(force))
      return api.upload<ImportReport>('/imports/offers-workbook', fd)
    },
    onSuccess: (r) => { if (!r.dry_run) qc.invalidateQueries() },
  })
  const r = run.data
  return (
    <Card title="Import the offers-monitoring workbook">
      <p className="secondary">Upload the workbook as <code>.xlsx</code> (sheets <em>Events</em>, <em>Fix Codes</em>, <em>Store Discounts</em> are read) or the Events sheet as <code>.csv</code>.
        Always preview first: nothing is written until you confirm. Re-importing an edited workbook updates existing events instead of duplicating them.</p>
      <div className="row">
        <input type="file" accept=".xlsx,.xlsm,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> allow re-importing an identical file</label>
        <button onClick={() => run.mutate(true)} disabled={!file || run.isPending}>Preview</button>
        <button className="primary" onClick={() => run.mutate(false)} disabled={!file || run.isPending || !r?.dry_run}>Import</button>
      </div>
      <div className="mt"><ErrorBox error={run.error} /></div>
      {r && (
        <div className="mt">
          <div className={`notice ${r.dry_run ? 'info' : 'good'}`}>
            {r.dry_run ? <strong>Preview — nothing written yet.</strong> : <strong>Imported.</strong>}{' '}
            {r.events_parsed} events parsed{!r.dry_run && ` (${r.events_created} new, ${r.events_updated} updated)`}, {r.rows_skipped} skipped, {r.coupons_upserted} coupons
            {r.stores_created.length > 0 && <>; new stores: <strong>{r.stores_created.join(', ')}</strong> (created unconnected)</>}
            {r.stores_existing.length > 0 && <>; existing stores: {r.stores_existing.join(', ')}</>}.
            {r.sheets_ignored.length > 0 && <div><small>Sheets not imported: {r.sheets_ignored.join(', ')}</small></div>}
          </div>
          {Object.keys(r.issue_counts).length > 0 && (
            <>
              <div className="section-title">Data issues ({r.issues.length})</div>
              <table><tbody>{Object.entries(r.issue_counts).map(([k, n]) => <tr key={k}><td>{k}</td><td className="num">{n}</td></tr>)}</tbody></table>
              <details className="mt"><summary>Row-level detail</summary>
                <table><thead><tr><th>Row</th><th>Store</th><th>Event</th><th>Issue</th></tr></thead>
                  <tbody>{r.issues.map((i, k) => <tr key={k}><td className="num">{i.sheet}!{i.row}</td><td>{i.store}</td><td>{i.event}</td><td>{i.issue}</td></tr>)}</tbody></table>
              </details>
            </>
          )}
        </div>
      )}
    </Card>
  )
}
