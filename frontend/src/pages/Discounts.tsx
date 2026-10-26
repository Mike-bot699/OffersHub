import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { money, num, pct } from '../format'
import { NeedStore } from '../NeedStore'
import { useApp, type StoreRow } from '../state'
import { Bars, TrendLine } from '../charts'
import { Card, ErrorBox, Kpi, Loading, PageHead, Share, Source, useSort } from '../ui'
import type { DiscountAnalysis } from './Overview'

type Dependency = { discounts: { aov_discounted: number | null; aov_full_price: number | null; avg_discount_depth: number | null; discounted_net_sales_share: number | null } }

export default function Discounts() {
  return <NeedStore title="Discounts">{(s) => <DiscountsFor store={s} />}</NeedStore>
}

function DiscountsFor({ store }: { store: StoreRow }) {
  const { days } = useApp()
  const cur = store.currency
  const q = useQuery({ queryKey: ['discount-analysis', store.id, days], queryFn: () => api.get<DiscountAnalysis>(`/stores/${store.id}/discount-analysis?days=${days}`) })
  const s = useQuery({ queryKey: ['summary', store.id, days], queryFn: () => api.get<Dependency>(`/stores/${store.id}/summary?days=${days}`) })
  const { sorted, th } = useSort(q.data?.by_code ?? [], 'orders')
  if (q.isLoading) return <Loading what="Analysing discounts" />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  const t = d.totals
  return (
    <>
      <PageHead title="Discounts" desc={`How much you give away, through which codes, and how discounted orders differ from full-price ones. ${d.period.start} to ${d.period.end}.`} />
      <div className="kpis">
        <Kpi label="Discounts given" value={money(t.discounts, cur)} />
        <Kpi label="Discount rate" value={pct(t.discount_rate)} sub="of gross sales" />
        <Kpi label="Orders with a discount" value={pct(t.discounted_order_share)} />
        <Kpi label="Discount per discounted order" value={money(t.discount_per_discounted_order, cur, 2)} />
        <Kpi label="Sales from discounted orders" value={pct(s.data?.discounts.discounted_net_sales_share)} />
        <Kpi label="Average depth when discounted" value={pct(s.data?.discounts.avg_discount_depth)} />
      </div>
      <div className="grid g2 mt">
        <Card title="Discounts given by week">
          <TrendLine data={d.weekly} x="week" y="discounts" name="discounts" currency={cur} />
        </Card>
        <Card title="Orders with a discount by week (%)">
          <TrendLine data={d.weekly.map((w) => ({ ...w, share: w.discounted_order_share == null ? null : +(w.discounted_order_share * 100).toFixed(1) }))} x="week" y="share" name="% of orders" />
        </Card>
      </div>
      <div className="grid g2 mt">
        <Card title="Orders by discount depth">
          <Bars data={d.by_depth} x="bucket" y="orders" name="orders" />
          <table className="mt">
            <thead><tr><th>Depth</th><th className="num">Orders</th><th className="num">Share</th><th className="num">AOV</th><th className="num">Units / order</th><th className="num">Discounts</th></tr></thead>
            <tbody>{d.by_depth.map((b) => (
              <tr key={b.bucket}><td>{b.bucket}</td><td className="num">{num(b.orders)}</td><td className="num">{pct(b.order_share)}</td>
                <td className="num">{money(b.aov, cur, 2)}</td><td className="num">{num(b.units_per_order, 2)}</td><td className="num">{money(b.discounts, cur)}</td></tr>))}
            </tbody>
          </table>
        </Card>
        <Card title="AOV by segment">
          <table>
            <thead><tr><th>Segment</th><th className="num">Orders</th><th className="num">AOV</th><th className="num">Units / order</th></tr></thead>
            <tbody>{d.aov_split.map((x) => (
              <tr key={x.segment}><td>{x.segment}</td><td className="num">{num(x.orders)}</td><td className="num">{money(x.aov, cur, 2)}</td><td className="num">{num(x.units_per_order, 2)}</td></tr>))}
            </tbody>
          </table>
          <Source>{d.note}</Source>
        </Card>
      </div>
      <Card title="Discount codes used in orders" className="mt">
        {!sorted.length ? <small>No discounted orders in the period.</small> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Code</th>{th('orders', 'Orders')}{th('order_share', 'Share of orders')}{th('discounts', 'Discounts given')}
                {th('discount_per_order', 'Discount / order')}{th('aov', 'AOV')}{th('units_per_order', 'Units / order')}{th('new_customer_share', 'New customers')}</tr></thead>
              <tbody>{sorted.map((c) => (
                <tr key={c.code}><td className="mono">{c.code}</td><td className="num">{num(c.orders)}</td><td><Share value={c.order_share} /></td>
                  <td className="num">{money(c.discounts, cur)}</td><td className="num">{money(c.discount_per_order, cur, 2)}</td>
                  <td className="num">{money(c.aov, cur, 2)}</td><td className="num">{num(c.units_per_order, 2)}</td><td className="num">{pct(c.new_customer_share, 0)}</td></tr>))}
              </tbody>
            </table>
          </div>)}
        <Source>Codes as they appear on orders. The coupon list (configured and documented codes) is in the Offer database.</Source>
      </Card>
    </>
  )
}
