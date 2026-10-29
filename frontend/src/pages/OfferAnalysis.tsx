import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { date, label, money, num, pct, signedPct } from '../format'
import { NeedStore } from '../NeedStore'
import type { StoreRow } from '../state'
import { Card, Empty, ErrorBox, Loading, PageHead, Source } from '../ui'
import { OfferStatusBadge } from '../offerStatus'

type Window = { orders_per_day: number | null; net_sales_per_day: number | null; aov: number | null; units_per_order: number | null; discount_rate: number | null; discounts: number }
type Row = {
  event_id: number; name: string; offer_text: string | null; offer_type: string | null; start: string; end: string; days: number; status: string | null
  coupon_codes: string[]; verification_status: string; data_issues: string[]; store_code: string; duration_days: number | null
  late: null | { orders: number; discounts: number; net_sales: number; last_order: string | null; code_still_active: boolean }
  documented: { offer_orders: number | null; total_orders: number | null; offer_sales: number | null; offer_order_share: number | null; source: string | null }
  measured: null | { during: Window; before: Window | null; change: Record<string, number | null> | null; offer_orders: number | null
    offer_order_share: number | null; offer_discounts: number | null; offer_aov: number | null; discount_per_offer_order: number | null; evidence: string }
}
type Perf = { events: Row[]; by_type: { offer_type: string; events: number; median_net_sales_per_day_change: number | null; discounts: number }[]; note: string }

function Delta({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="muted">—</span>
  return <span className={v > 0.02 ? 'delta-up' : v < -0.02 ? 'delta-down' : ''}>{signedPct(v)}</span>
}

function Late({ l, cur }: { l: Row['late']; cur: string }) {
  if (!l) return <span className="muted">—</span>
  return (
    <span title={l.last_order ? `Last order with the code: ${l.last_order}` : undefined}>
      {num(l.orders)}{l.orders > 0 && <div><small className="muted">{money(l.discounts, cur)} disc.</small></div>}
      {l.code_still_active && <div><span className="badge warning">code active</span></div>}
    </span>
  )
}

export default function OfferAnalysis() {
  return <NeedStore title="Analysis">{(s) => <AnalysisFor store={s} />}</NeedStore>
}

function AnalysisFor({ store }: { store: StoreRow }) {
  const cur = store.currency
  const q = useQuery({ queryKey: ['offer-performance', store.id], queryFn: () => api.get<Perf>(`/stores/${store.id}/offer-performance?days=730`) })
  if (q.isLoading) return <Loading what="Measuring offers" />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  const measured = d.events.filter((e) => e.measured)
  const documented = d.events.filter((e) => !e.measured)
  return (
    <>
      <PageHead title="Analysis" desc="What happened during each offer compared with the period just before it: sales per day, AOV, units per order and the discount it cost." />
      <div className="notice warning" style={{ marginBottom: 14 }}>{d.note}</div>
      {d.by_type.length > 0 && (
        <Card title="By offer type">
          <table>
            <thead><tr><th>Offer type</th><th className="num">Offers measured</th><th className="num">Median change in sales / day</th><th className="num">Discounts given</th></tr></thead>
            <tbody>{d.by_type.map((t) => (
              <tr key={t.offer_type}><td>{label(t.offer_type)}</td><td className="num">{t.events}</td><td className="num"><Delta v={t.median_net_sales_per_day_change} /></td><td className="num">{money(t.discounts, cur)}</td></tr>))}
            </tbody>
          </table>
        </Card>
      )}
      <Card title="Offers measured from store orders" className="mt">
        {!measured.length ? <Empty title="No measured offers">Offers are measured once their dates overlap synced orders.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Offer</th><th>Store</th><th>Dates</th><th className="num">Duration</th><th className="num">Sales / day</th><th className="num">Orders / day</th><th className="num">AOV</th>
                <th className="num">Units / order</th><th className="num">Code orders</th><th className="num">Discount / code order</th><th className="num">Discounts given</th><th className="num">Late redemptions</th></tr></thead>
              <tbody>{measured.map((e) => {
                const m = e.measured!
                return (
                  <tr key={e.event_id}>
                    <td><strong>{e.name}</strong><div><small className="secondary">{e.offer_text}</small></div><OfferStatusBadge status={e.status} /></td>
                    <td><span className="badge">{e.store_code}</span></td>
                    <td style={{ whiteSpace: 'nowrap' }}><small>{date(e.start)} → {date(e.end)}</small></td>
                    <td className="num">{e.duration_days ?? e.days} d</td>
                    <td className="num">{money(m.during.net_sales_per_day, cur)}<div><Delta v={m.change?.net_sales_per_day} /></div></td>
                    <td className="num">{num(m.during.orders_per_day, 1)}<div><Delta v={m.change?.orders_per_day} /></div></td>
                    <td className="num">{money(m.during.aov, cur, 2)}<div><Delta v={m.change?.aov} /></div></td>
                    <td className="num">{num(m.during.units_per_order, 2)}<div><Delta v={m.change?.units_per_order} /></div></td>
                    <td className="num">{m.offer_orders == null ? '—' : `${num(m.offer_orders)} (${pct(m.offer_order_share, 0)})`}</td>
                    <td className="num">{money(m.discount_per_offer_order, cur, 2)}</td>
                    <td className="num">{money(m.offer_discounts ?? m.during.discounts, cur)}</td>
                    <td className="num"><Late l={e.late} cur={cur} /></td>
                  </tr>)
              })}</tbody>
            </table>
          </div>)}
        <Source>Change = during the offer vs the same number of days just before it (max 28). Evidence: weak (before/after). Late redemptions = orders that used the code after the offer ended (e.g. late email opens); they are not counted in the offer's results.</Source>
      </Card>
      <Card title="Offers with documented results only" className="mt">
        {!documented.length ? <small>None.</small> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Offer</th><th>Store</th><th>Dates</th><th className="num">Duration</th><th className="num">Offer orders</th><th className="num">Share of orders</th><th className="num">Offer sales</th><th>Data issues</th></tr></thead>
              <tbody>{documented.map((e) => (
                <tr key={e.event_id}><td><strong>{e.name}</strong><div><small className="secondary">{e.offer_text}</small></div></td>
                  <td><span className="badge">{e.store_code}</span></td>
                  <td style={{ whiteSpace: 'nowrap' }}><small>{date(e.start)} → {date(e.end)}</small></td>
                  <td className="num">{e.duration_days ?? e.days} d</td>
                  <td className="num">{num(e.documented.offer_orders)}</td><td className="num">{pct(e.documented.offer_order_share)}</td>
                  <td className="num">{money(e.documented.offer_sales, cur)}</td>
                  <td>{e.data_issues?.length ? <span className="badge warning" title={e.data_issues.join('\n')}>▲ {e.data_issues.length}</span> : '—'}</td></tr>))}
              </tbody>
            </table>
          </div>)}
        <Source>As recorded in the offers workbook or by hand; not verified against store orders.</Source>
      </Card>
    </>
  )
}
