import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, qs } from '../api'
import { date, money, num, pct } from '../format'
import { useApp } from '../state'
import { Card, DemoBadge, Empty, ErrorBox, Kpi, Loading, PageHead, Source } from '../ui'

type StoreOverview = {
  store_id: number; store_code: string; store_name: string; currency: string; is_demo: boolean; logo: string | null
  period: { start: string; end: string }
  live_offers: { id: number; name: string; offer_text: string | null; coupon_codes: string[]; start_date: string | null; end_date: string | null; technical_status: string }[]
  offers_in_period: number
  offers_in_period_by_status: Record<string, number>
  kpis: null | {
    orders: number; aov_with_discount: number | null; aov_without_discount: number | null; orders_with_offer_share: number | null
    orders_with_code_share: number | null; orders_with_live_offer_code: number | null; sales_share_with_offer: number | null
    discount_rate: number | null; discounts: number
  }
  top_codes: { code: string; orders: number; order_share: number | null; net_sales: number; discounts: number; aov: number | null }[]
  orders_missing: string | null
}

export default function Overview() {
  const { storeId, days, includeDemo, store } = useApp()
  const q = useQuery({ queryKey: ['overview', storeId, days, includeDemo],
    queryFn: () => api.get<StoreOverview | { stores: StoreOverview[] }>(`/overview${qs({ store_id: storeId, days, include_demo: includeDemo })}`) })
  return (
    <>
      <PageHead title={store ? `${store.code} — offers & discounts` : 'Offers & discounts'} desc={`Last ${days} days. Live offers, how many offers ran, and what discounts did to orders and order value.`} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : 'stores' in q.data! ? <Portfolio rows={q.data.stores} /> : <StoreView o={q.data as StoreOverview} />}
    </>
  )
}

function StoreView({ o }: { o: StoreOverview }) {
  const k = o.kpis
  const cur = o.currency
  return (
    <>
      <div className="kpis">
        <Kpi label="Live offers now" value={o.live_offers.length} sub="status Live" />
        <Kpi label="Offers in the period" value={o.offers_in_period} sub={Object.entries(o.offers_in_period_by_status).map(([s, n]) => `${n} ${s.replace('_', ' ')}`).join(' · ') || '—'} />
        <Kpi label="AOV with / without discount" value={k ? <span style={{ fontSize: 20 }}>{money(k.aov_with_discount, cur)} / {money(k.aov_without_discount, cur)}</span> : '—'}
          title="Average order value of orders that used a discount vs orders at full price" />
        <Kpi label="Orders with a code or offer" value={k ? pct(k.orders_with_offer_share) : '—'} sub={k ? `${pct(k.orders_with_code_share)} with a code` : undefined} />
        <Kpi label="Sales from orders with an offer" value={k ? pct(k.sales_share_with_offer) : '—'} sub="share of net sales" />
        <Kpi label="Discount rate" value={k ? pct(k.discount_rate) : '—'} sub={k ? `${money(k.discounts, cur)} of discounts on gross sales` : undefined} />
      </div>
      {o.orders_missing && <div className="notice warning mt"><strong>Order KPIs not available.</strong> {o.orders_missing}</div>}
      <div className="grid g2 mt">
        <Card title="Live offers" extra={<Link to="/offers-plan">Offers Plan →</Link>}>
          {!o.live_offers.length ? <small>No offer is marked Live. Set the status in the Offers Plan.</small> : (
            <table>
              <tbody>{o.live_offers.map((e) => (
                <tr key={e.id}>
                  <td style={{ minWidth: 140 }}><strong>{e.name}</strong><div><small className="secondary">{e.offer_text ?? '—'}</small></div>
                    <small className="muted">{date(e.start_date)} → {date(e.end_date)}</small></td>
                  <td className="mono">{e.coupon_codes.join(', ') || <span className="muted">automatic</span>}</td>
                </tr>))}
              </tbody>
            </table>)}
        </Card>
        <Card title="Top 10 codes used" extra={<small className="muted">{date(o.period.start)} → {date(o.period.end)}</small>}>
          {!o.top_codes.length ? <small>{o.orders_missing ? 'Needs order data.' : 'No codes used in this period.'}</small> : (
            <table>
              <thead><tr><th>Code</th><th className="num">Orders</th><th className="num">Share of orders</th><th className="num">AOV</th><th className="num">Discounts</th></tr></thead>
              <tbody>{o.top_codes.map((c) => (
                <tr key={c.code}><td className="mono">{c.code}</td><td className="num">{num(c.orders)}</td><td className="num">{pct(c.order_share)}</td>
                  <td className="num">{money(c.aov, cur)}</td><td className="num">{money(c.discounts, cur)}</td></tr>))}
              </tbody>
            </table>)}
          <Source>Orders that used a code are not all caused by it.</Source>
        </Card>
      </div>
    </>
  )
}

function Portfolio({ rows }: { rows: StoreOverview[] }) {
  if (!rows.length) return <Empty title="No stores yet">Add a store in Settings → Stores.</Empty>
  return (
    <Card title="By store" extra={<small className="muted">Each store in its own currency — amounts are never added across stores</small>}>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Store</th><th className="num">Live offers</th><th className="num">Offers in period</th><th className="num">AOV with discount</th><th className="num">AOV without</th>
            <th className="num">Orders with code/offer</th><th className="num">Sales from offer orders</th><th className="num">Discount rate</th><th>Top code</th></tr></thead>
          <tbody>{rows.map((o) => (
            <tr key={o.store_id}>
              <td><div className="row" style={{ gap: 6 }}>{o.logo && <img src={o.logo} alt="" width={20} height={20} style={{ borderRadius: 4 }} />}<strong>{o.store_code}</strong>{o.is_demo && <DemoBadge />}</div><small className="muted">{o.store_name}</small></td>
              <td className="num">{o.live_offers.length}</td>
              <td className="num">{o.offers_in_period}</td>
              {o.kpis ? <>
                <td className="num">{money(o.kpis.aov_with_discount, o.currency)}</td>
                <td className="num">{money(o.kpis.aov_without_discount, o.currency)}</td>
                <td className="num">{pct(o.kpis.orders_with_offer_share)}</td>
                <td className="num">{pct(o.kpis.sales_share_with_offer)}</td>
                <td className="num">{pct(o.kpis.discount_rate)}</td>
              </> : <td colSpan={5}><small className="muted" title={o.orders_missing ?? ''}>Needs order data</small></td>}
              <td className="mono">{o.top_codes[0]?.code ?? '—'}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
