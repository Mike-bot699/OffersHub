import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, qs } from '../api'
import { date, money, num, pct } from '../format'
import { isOfferStrategy } from '../offerFocus'
import { RecCard, RecDetail, type Rec } from '../recs'
import { useApp } from '../state'
import { TrendLine } from '../charts'
import { Card, DemoBadge, Empty, ErrorBox, Kpi, Loading, PageHead, Status } from '../ui'

type Kpis = Record<string, number | null> & { contribution_missing: string[]; cost_basis: string }
type Alert = { severity: string; kind: string; title: string; detail: string; store_code: string }
type Ev = { id: number; store_code: string; name: string; offer_text: string | null; start_date: string | null; end_date: string | null
  status: string; coupon_codes: string[]; verification_status: string; is_demo: boolean }
export type DiscountAnalysis = {
  period: { start: string; end: string }
  totals: Record<string, number | null>
  aov_split: { segment: string; orders: number; aov: number | null; units_per_order: number | null }[]
  weekly: { week: string; orders: number; net_sales: number; discounts: number; discount_rate: number | null
    discounted_order_share: number | null; aov: number | null; units_per_order: number | null }[]
  by_code: { code: string; orders: number; order_share: number | null; net_sales: number; discounts: number
    discount_per_order: number | null; aov: number | null; units_per_order: number | null; new_customer_share: number | null }[]
  by_depth: { bucket: string; orders: number; order_share: number | null; aov: number | null; units_per_order: number | null; discounts: number }[]
  note: string
}
type Summary = { kpis: Kpis; previous: { change: Record<string, number | null> } }

export default function Overview() {
  const { store } = useApp()
  return store ? <StoreCockpit /> : <PortfolioCockpit />
}

function OfferList({ events, empty }: { events: Ev[]; empty: string }) {
  if (!events.length) return <small>{empty}</small>
  return (
    <table>
      <tbody>{events.map((e) => (
        <tr key={e.id}>
          <td style={{ whiteSpace: 'nowrap' }}><small>{date(e.start_date)} → {date(e.end_date)}</small></td>
          <td>{e.is_demo ? <DemoBadge /> : <span className="badge">{e.store_code}</span>}</td>
          <td><strong>{e.name}</strong><div><small className="secondary">{e.offer_text ?? '—'}</small></div></td>
          <td className="mono">{e.coupon_codes.join(', ')}</td>
          <td><Status value={e.status} /></td>
        </tr>))}
      </tbody>
    </table>
  )
}

function useOffers(storeId: number | null, includeDemo: boolean) {
  const today = new Date().toISOString().slice(0, 10)
  const live = useQuery({ queryKey: ['events', storeId, 'live', includeDemo], queryFn: () => api.get<Ev[]>(`/events${qs({ store_id: storeId, status: 'live', include_demo: includeDemo })}`) })
  const planned = useQuery({ queryKey: ['events', storeId, 'planned', includeDemo], queryFn: () => api.get<Ev[]>(`/events${qs({ store_id: storeId, status: 'planned', include_demo: includeDemo })}`) })
  const upcoming = (planned.data ?? []).filter((e) => e.start_date && e.start_date >= today).sort((a, b) => (a.start_date! < b.start_date! ? -1 : 1)).slice(0, 6)
  return { live: live.data ?? [], upcoming }
}

function AlertList({ alerts }: { alerts: Alert[] }) {
  if (!alerts.length) return <small>Nothing needs attention.</small>
  return (
    <div className="stack" style={{ gap: 8 }}>
      {alerts.slice(0, 8).map((a, i) => (
        <div key={i} className={`notice ${a.severity}`}>
          <div className="row" style={{ gap: 6 }}><Status value={a.severity} />{a.store_code && <span className="badge">{a.store_code}</span>}<strong>{a.title}</strong></div>
          {a.detail && <small className="secondary">{a.detail}</small>}
        </div>))}
    </div>
  )
}

function StoreCockpit() {
  const { store, days, includeDemo } = useApp()
  const [open, setOpen] = useState<number | null>(null)
  const qc = useQueryClient()
  const cur = store!.currency
  const s = useQuery({ queryKey: ['summary', store!.id, days], queryFn: () => api.get<Summary>(`/stores/${store!.id}/summary?days=${days}`) })
  const da = useQuery({ queryKey: ['discount-analysis', store!.id, days], queryFn: () => api.get<DiscountAnalysis>(`/stores/${store!.id}/discount-analysis?days=${days}`) })
  const recs = useQuery({ queryKey: ['recs', store!.id], queryFn: () => api.get<Rec[]>(`/recommendations?store_id=${store!.id}`) })
  const alerts = useQuery({ queryKey: ['alerts', store!.id], queryFn: () => api.get<Alert[]>(`/alerts?store_id=${store!.id}`) })
  const offers = useOffers(store!.id, includeDemo)
  const run = useMutation({ mutationFn: () => api.post('/recommendations/run', { store_id: store!.id, days }), onSuccess: () => qc.invalidateQueries() })
  if (s.isLoading || da.isLoading) return <Loading what="Loading" />
  if (s.error || da.error) return <ErrorBox error={s.error ?? da.error} />
  const k = s.data!.kpis
  const ch = s.data!.previous.change
  const d = da.data!
  const split = Object.fromEntries(d.aov_split.map((x) => [x.segment, x]))
  const offerRecs = (recs.data ?? []).filter((r) => isOfferStrategy(r.strategy_type))
  return (
    <>
      <PageHead title={`${store!.code} — offers & discounts`} desc={`Last ${days} days compared with the previous ${days} days.`}
        actions={<button className="primary" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? 'Analysing…' : 'Refresh analysis'}</button>} />
      <ErrorBox error={run.error} />
      <div className="kpis">
        <Kpi label="Net sales" value={money(k.net_sales, cur)} delta={ch.net_sales} sub="vs prev." />
        <Kpi label="AOV" value={money(k.aov, cur, 2)} delta={ch.aov} />
        <Kpi label="Units / order" value={num(k.units_per_order, 2)} delta={ch.units_per_order} />
        <Kpi label="Discounts given" value={money(k.discounts, cur)} delta={ch.discounts} invert />
        <Kpi label="Discount rate" value={pct(k.discount_rate)} delta={ch.discount_rate} invert title="Discounts ÷ gross sales" />
        <Kpi label="Orders with a discount" value={pct(k.discounted_order_share)} delta={ch.discounted_order_share} invert />
        <Kpi label="AOV with / without discount" value={<span style={{ fontSize: 18 }}>{money(split['With a discount']?.aov, cur)} / {money(split['Full price']?.aov, cur)}</span>} />
        <Kpi label="Gross margin" value={k.gross_margin_pct == null ? '—' : pct(k.gross_margin_pct)} sub={k.gross_margin_pct == null ? 'Unit costs missing' : undefined} />
      </div>
      <div className="grid g2 mt">
        <Card title="AOV by week" extra={<Link to="/basket">AOV & basket →</Link>}>
          <TrendLine data={d.weekly} x="week" y="aov" name="AOV" currency={cur} />
        </Card>
        <Card title="Discount rate by week" extra={<Link to="/discounts">Discounts →</Link>}>
          <TrendLine data={d.weekly.map((w) => ({ ...w, rate_pct: w.discount_rate == null ? null : +(w.discount_rate * 100).toFixed(2) }))} x="week" y="rate_pct" name="% of gross sales" />
        </Card>
      </div>
      <div className="grid g2 mt">
        <Card title="Live offers" extra={<Link to="/calendar">Calendar →</Link>}>
          <OfferList events={offers.live} empty="No offer is live." />
          <div className="section-title">Coming up</div>
          <OfferList events={offers.upcoming} empty="Nothing planned. Add the next event in the calendar." />
        </Card>
        <Card title="Needs attention">
          <AlertList alerts={alerts.data ?? []} />
        </Card>
      </div>
      <Card title="Most used discount codes" className="mt" extra={<Link to="/discounts">All codes →</Link>}>
        {d.by_code.length === 0 ? <small>No discounted orders in the period.</small> : (
          <table>
            <thead><tr><th>Code</th><th className="num">Orders</th><th className="num">Share of orders</th><th className="num">Discount / order</th><th className="num">AOV</th><th className="num">Discounts given</th></tr></thead>
            <tbody>{d.by_code.slice(0, 6).map((c) => (
              <tr key={c.code}><td className="mono">{c.code}</td><td className="num">{num(c.orders)}</td><td className="num">{pct(c.order_share)}</td>
                <td className="num">{money(c.discount_per_order, cur, 2)}</td><td className="num">{money(c.aov, cur, 2)}</td><td className="num">{money(c.discounts, cur)}</td></tr>))}
            </tbody>
          </table>)}
      </Card>
      <Card title="Offer & discount opportunities" className="mt" extra={<Link to="/opportunities">All →</Link>}>
        {offerRecs.length ? offerRecs.slice(0, 5).map((r) => <RecCard key={r.id} rec={r} onOpen={() => setOpen(r.id)} />)
          : <Empty title="No open opportunities">Refresh the analysis. Nothing is shown unless the data supports it.</Empty>}
      </Card>
      <RecDetail id={open} onClose={() => setOpen(null)} />
    </>
  )
}

type Portfolio = {
  period: { start: string; end: string }
  stores: { store: { id: number; code: string; name: string; currency: string; is_demo: boolean }; kpis: Kpis
    previous: { net_sales: number; orders: number; aov: number | null }; integration_status: string }[]
  totals_by_currency: Record<string, { net_sales: number; orders: number; discounts: number; stores: number }>
  top_recommendations: Rec[]
  alerts: Alert[]
}

function PortfolioCockpit() {
  const { days, includeDemo, set, stores } = useApp()
  const [open, setOpen] = useState<number | null>(null)
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['portfolio', days, includeDemo], queryFn: () => api.get<Portfolio>(`/portfolio${qs({ days, include_demo: includeDemo })}`) })
  const offers = useOffers(null, includeDemo)
  const demo = useMutation({ mutationFn: () => api.post('/demo/reset'), onSuccess: () => qc.invalidateQueries() })
  const run = useMutation({ mutationFn: () => api.post('/recommendations/run', { include_demo: includeDemo, days }), onSuccess: () => qc.invalidateQueries() })
  if (q.isLoading) return <Loading what="Loading" />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  if (!stores.length) return (
    <>
      <PageHead title="Offers & discounts" />
      <Card><Empty title="No stores yet">
        <p>Import your offers workbook, connect a store, or load the demo store.</p>
        <div className="row" style={{ justifyContent: 'center' }}>
          <Link to="/offer-database" className="btn">Import offers workbook</Link>
          <Link to="/integrations" className="btn">Connect a store</Link>
          <button onClick={() => demo.mutate()} disabled={demo.isPending}>{demo.isPending ? 'Building…' : 'Load demo store'}</button>
        </div>
      </Empty></Card>
    </>
  )
  return (
    <>
      <PageHead title="Offers & discounts — all stores" desc={`${d.period.start} to ${d.period.end}. Currencies are never summed together; demo data is never in the totals.`}
        actions={<button className="primary" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? 'Analysing…' : 'Refresh analysis'}</button>} />
      <div className="kpis">
        {Object.keys(d.totals_by_currency).length === 0 && <Kpi label="Real stores" value="—" sub="Only demo data so far" />}
        {Object.entries(d.totals_by_currency).map(([cur, t]) => (
          <Kpi key={cur} label={`${cur} · ${t.stores} store${t.stores > 1 ? 's' : ''}`} value={money(t.net_sales, cur)}
            sub={`${money(t.discounts, cur)} discounts · AOV ${money(t.orders ? t.net_sales / t.orders : null, cur, 2)}`} />
        ))}
      </div>
      <Card title="Stores" className="mt">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Store</th><th className="num">Net sales</th><th className="num">AOV</th><th className="num">Units / order</th>
              <th className="num">Discounts</th><th className="num">Discount rate</th><th className="num">Orders with discount</th><th>Data</th></tr></thead>
            <tbody>{d.stores.map((s) => (
              <tr key={s.store.id} className="clickable" onClick={() => set({ storeId: s.store.id })}>
                <td><strong>{s.store.code}</strong> <span className="secondary">{s.store.name}</span> {s.store.is_demo && <DemoBadge />}</td>
                <td className="num">{money(s.kpis.net_sales, s.store.currency)}</td><td className="num">{money(s.kpis.aov, s.store.currency, 2)}</td>
                <td className="num">{num(s.kpis.units_per_order, 2)}</td><td className="num">{money(s.kpis.discounts, s.store.currency)}</td>
                <td className="num">{pct(s.kpis.discount_rate)}</td><td className="num">{pct(s.kpis.discounted_order_share)}</td>
                <td><Status value={s.integration_status} /></td>
              </tr>))}
            </tbody>
          </table>
        </div>
        <small className="muted">Click a store to open its cockpit.</small>
      </Card>
      <div className="grid g2 mt">
        <Card title="Live and upcoming offers" extra={<Link to="/calendar">Calendar →</Link>}>
          <OfferList events={offers.live} empty="No offer is live." />
          <div className="section-title">Coming up</div>
          <OfferList events={offers.upcoming} empty="Nothing planned." />
        </Card>
        <Card title="Needs attention"><AlertList alerts={d.alerts} /></Card>
      </div>
      <Card title="Offer & discount opportunities" className="mt" extra={<Link to="/opportunities">All →</Link>}>
        {d.top_recommendations.filter((r) => isOfferStrategy(r.strategy_type)).slice(0, 5).map((r) => <RecCard key={r.id} rec={r} onOpen={() => setOpen(r.id)} />)}
        {!d.top_recommendations.some((r) => isOfferStrategy(r.strategy_type)) && <small>No open opportunities.</small>}
      </Card>
      <RecDetail id={open} onClose={() => setOpen(null)} />
    </>
  )
}
