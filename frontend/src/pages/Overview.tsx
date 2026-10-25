import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, qs } from '../api'
import { ago, money, num, pct } from '../format'
import { RecCard, RecDetail, type Rec } from '../recs'
import { useApp } from '../state'
import { TrendLine } from '../charts'
import { Card, DemoBadge, Empty, ErrorBox, Kpi, Loading, PageHead, Status } from '../ui'

type Kpis = Record<string, number | null> & { contribution_missing: string[]; cost_basis: string }
type Alert = { severity: string; kind: string; title: string; detail: string; store_code: string; event_id?: number }
type Portfolio = {
  period: { start: string; end: string }
  stores: { store: { id: number; code: string; name: string; currency: string; is_demo: boolean }; kpis: Kpis
    previous: { net_sales: number; orders: number; aov: number | null }
    freshness: Record<string, { status: string; last_success_at: string | null }>; integration_status: string }[]
  totals_by_currency: Record<string, { net_sales: number; orders: number; discounts: number; stores: number }>
  top_recommendations: Rec[]
  alerts: Alert[]
}

export default function Overview() {
  const { store } = useApp()
  return store ? <StoreOverview /> : <PortfolioOverview />
}

function growth(c: number | null | undefined, p: number | null | undefined) {
  if (c == null || !p) return null
  return (c - p) / Math.abs(p)
}

function PortfolioOverview() {
  const { days, includeDemo, set, stores } = useApp()
  const [open, setOpen] = useState<number | null>(null)
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['portfolio', days, includeDemo],
    queryFn: () => api.get<Portfolio>(`/portfolio${qs({ days, include_demo: includeDemo })}`),
  })
  const run = useMutation({
    mutationFn: () => api.post('/recommendations/run', { include_demo: includeDemo, days }),
    onSuccess: () => qc.invalidateQueries(),
  })
  const demo = useMutation({ mutationFn: () => api.post('/demo/reset'), onSuccess: () => qc.invalidateQueries() })

  if (q.isLoading) return <Loading what="Loading portfolio" />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  if (!stores.length) {
    return (
      <>
        <PageHead title="Overview" />
        <Card>
          <Empty title="No stores yet">
            <p>Add a store and connect Shopify, import your offers workbook, or load the demo store to explore the application.</p>
            <div className="row" style={{ justifyContent: 'center' }}>
              <Link to="/stores" className="btn">Add a store</Link>
              <Link to="/offers" className="btn">Import offers workbook</Link>
              <button onClick={() => demo.mutate()} disabled={demo.isPending}>{demo.isPending ? 'Building demo…' : 'Load demo store'}</button>
            </div>
          </Empty>
        </Card>
      </>
    )
  }
  return (
    <>
      <PageHead title="Portfolio overview"
        desc={`What matters most across your stores, ${d.period.start} to ${d.period.end}. Currencies are never summed together.`}
        actions={<button className="primary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? 'Analysing…' : 'Refresh analysis'}</button>} />
      <ErrorBox error={run.error} />
      <div className="kpis">
        {Object.keys(d.totals_by_currency).length === 0 && <Kpi label="Real stores" value="—" sub="Only demo data so far" />}
        {Object.entries(d.totals_by_currency).map(([cur, t]) => (
          <Kpi key={cur} label={`Net sales (${cur}, ${t.stores} store${t.stores > 1 ? 's' : ''})`} value={money(t.net_sales, cur)}
            sub={`${num(t.orders)} orders · ${money(t.discounts, cur)} discounts`} />
        ))}
      </div>

      <div className="grid g2 mt">
        <Card title="Top opportunities" extra={<Link to="/opportunities">All opportunities →</Link>}>
          {d.top_recommendations.length === 0
            ? <Empty title="No open recommendations">Run the analysis once stores have order data. Detectors only fire when evidence clears their thresholds.</Empty>
            : d.top_recommendations.slice(0, 5).map((r) => <RecCard key={r.id} rec={r} onOpen={() => setOpen(r.id)} />)}
        </Card>
        <Card title="Issues to check" extra={<Link to="/offers">Offers →</Link>}>
          {d.alerts.length === 0 ? <small>No alerts.</small> : (
            <div className="stack" style={{ gap: 8 }}>
              {d.alerts.slice(0, 10).map((a, i) => (
                <div key={i} className={`notice ${a.severity}`}>
                  <div className="row" style={{ gap: 6 }}><Status value={a.severity} /> {a.store_code && <span className="badge">{a.store_code}</span>} <strong>{a.title}</strong></div>
                  {a.detail && <small className="secondary">{a.detail}</small>}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="Stores" className="mt">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Store</th><th className="num">Net sales</th><th className="num">vs prev.</th><th className="num">Orders</th>
                <th className="num">AOV</th><th className="num">Discount rate</th><th className="num">Gross margin</th>
                <th>Integration</th><th>Orders data</th>
              </tr>
            </thead>
            <tbody>
              {d.stores.map((s) => {
                const g = growth(s.kpis.net_sales, s.previous.net_sales)
                return (
                  <tr key={s.store.id} className="clickable" onClick={() => set({ storeId: s.store.id })}>
                    <td><strong>{s.store.code}</strong> <span className="secondary">{s.store.name}</span> {s.store.is_demo && <DemoBadge />}</td>
                    <td className="num">{money(s.kpis.net_sales, s.store.currency)}</td>
                    <td className={`num ${g == null ? '' : g >= 0 ? 'delta-up' : 'delta-down'}`}>{g == null ? '—' : `${g >= 0 ? '+' : ''}${(g * 100).toFixed(1)}%`}</td>
                    <td className="num">{num(s.kpis.orders)}</td>
                    <td className="num">{money(s.kpis.aov, s.store.currency, 2)}</td>
                    <td className="num">{pct(s.kpis.discount_rate)}</td>
                    <td className="num" title={s.kpis.cost_basis === 'insufficient' ? 'Unit costs missing' : ''}>{s.kpis.gross_margin_pct == null ? <span className="muted">no costs</span> : pct(s.kpis.gross_margin_pct)}</td>
                    <td><Status value={s.integration_status} /></td>
                    <td><Status value={s.freshness.orders?.status} text={`${s.freshness.orders?.status?.replace('_', ' ')}${s.freshness.orders?.last_success_at ? ' · ' + ago(s.freshness.orders.last_success_at) : ''}`} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <small className="muted">Click a store to focus the whole application on it.</small>
      </Card>
      <RecDetail id={open} onClose={() => setOpen(null)} />
    </>
  )
}

type Summary = {
  store: { code: string; currency: string; is_demo: boolean }
  kpis: Kpis
  previous: { change: Record<string, number | null>; kpis: Kpis }
  year_ago: { available: boolean; change: Record<string, number | null> }
  weekly: { week: string; net_sales: number; orders: number }[]
  data_quality: Record<string, number | string | null>
}

function StoreOverview() {
  const { store, days } = useApp()
  const [open, setOpen] = useState<number | null>(null)
  const qc = useQueryClient()
  const s = useQuery({ queryKey: ['summary', store!.id, days], queryFn: () => api.get<Summary>(`/stores/${store!.id}/summary?days=${days}`) })
  const recs = useQuery({ queryKey: ['recs', store!.id], queryFn: () => api.get<Rec[]>(`/recommendations?store_id=${store!.id}`) })
  const alerts = useQuery({ queryKey: ['alerts', store!.id], queryFn: () => api.get<Alert[]>(`/alerts?store_id=${store!.id}`) })
  const run = useMutation({
    mutationFn: () => api.post('/recommendations/run', { store_id: store!.id, days }),
    onSuccess: () => qc.invalidateQueries(),
  })
  if (s.isLoading) return <Loading what="Loading store" />
  if (s.error) return <ErrorBox error={s.error} />
  const d = s.data!
  const k = d.kpis
  const ch = d.previous.change
  const cur = store!.currency
  return (
    <>
      <PageHead title={`${store!.code} — ${store!.name}`} desc={`Last ${days} days vs the previous ${days} days.`}
        actions={<button className="primary" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? 'Analysing…' : 'Refresh analysis'}</button>} />
      <ErrorBox error={run.error} />
      {k.orders === 0 && <div className="notice warning" style={{ marginBottom: 12 }}>No orders in this period. Connect and sync the store in Integrations, or choose a longer period.</div>}
      <div className="kpis">
        <Kpi label="Net sales" value={money(k.net_sales, cur)} delta={ch.net_sales} sub="vs prev." title="Gross sales − discounts, before returns, excl. shipping & tax" />
        <Kpi label="Orders" value={num(k.orders)} delta={ch.orders} />
        <Kpi label="AOV" value={money(k.aov, cur, 2)} delta={ch.aov} />
        <Kpi label="Units / order" value={num(k.units_per_order, 2)} delta={ch.units_per_order} />
        <Kpi label="Discount rate" value={pct(k.discount_rate)} delta={ch.discount_rate} invert title="Discounts ÷ gross sales" />
        <Kpi label="Gross margin" value={k.gross_margin_pct == null ? '—' : pct(k.gross_margin_pct)}
          sub={k.gross_margin_pct == null ? 'Unit costs missing' : undefined} delta={k.gross_margin_pct == null ? null : ch.gross_margin_pct} />
        <Kpi label="Contribution" value={k.contribution == null ? '—' : money(k.contribution, cur)}
          sub={k.contribution == null ? 'Needs unit costs' : k.contribution_missing.length ? `Excludes: ${k.contribution_missing.join(', ')}` : 'All cost inputs present'} />
        <Kpi label="Conversion" value={k.conversion_rate == null ? '—' : pct(k.conversion_rate, 2)}
          sub={k.conversion_rate == null ? 'No traffic data' : `${pct(Number(k.traffic_coverage))} of days with traffic`} delta={ch.conversion_rate} />
      </div>
      <div className="grid g2 mt">
        <Card title="Net sales by week">
          {d.weekly.length ? <TrendLine data={d.weekly} x="week" y="net_sales" name="net sales" currency={cur} /> : <small>No data.</small>}
          <small className="muted">Last week may be partial.</small>
        </Card>
        <Card title="Alerts">
          {alerts.data?.length ? (
            <div className="stack" style={{ gap: 8 }}>
              {alerts.data.slice(0, 7).map((a, i) => (
                <div key={i} className={`notice ${a.severity}`}>
                  <div className="row" style={{ gap: 6 }}><Status value={a.severity} /><strong>{a.title}</strong></div>
                  {a.detail && <small className="secondary">{a.detail}</small>}
                </div>
              ))}
            </div>
          ) : <small>No alerts.</small>}
        </Card>
      </div>
      <Card title="Prioritised opportunities" className="mt" extra={<Link to="/opportunities">All →</Link>}>
        {recs.data?.length ? recs.data.slice(0, 6).map((r) => <RecCard key={r.id} rec={r} onOpen={() => setOpen(r.id)} />)
          : <Empty title="No open recommendations">Refresh the analysis. If nothing appears, the data does not support a confident recommendation yet.</Empty>}
      </Card>
      <RecDetail id={open} onClose={() => setOpen(null)} />
    </>
  )
}
