import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { money, num, pct, signedPct } from '../format'
import { NeedStore } from '../NeedStore'
import { useApp, type StoreRow } from '../state'
import { TrendLine } from '../charts'
import { Card, Drawer, ErrorBox, Kpi, Loading, PageHead, Share, Source, Tabs, useSort } from '../ui'

type P = {
  product_id: number; title: string; status: string | null; product_type: string | null; units: number; orders: number
  net_sales: number; share_of_net: number; cumulative_share: number; rank: number; growth: number | null
  prev_net_sales: number; avg_selling_price: number | null; discount_rate: number | null; discounted_unit_share: number | null
  return_rate_units: number | null; gross_margin_pct: number | null; cost_coverage: number | null
  aov_of_orders_with_product: number | null; solo_order_share: number | null; price_min: number | null; price_max: number | null
  variants_total: number; variants_available: number; inventory_units: number | null; days_of_cover: number | null
  price_changes_in_period: number; buyers: number
}
type Perf = {
  products: P[]; unsold_active_products: { product_id: number; title: string; inventory_units: number }[]
  concentration: Record<string, number | null | string>
  variants: { product_id: number; title: string; units: number; uniform_price: boolean; expected_share_if_even: number
    variants: { variant_id: number; title: string; sku: string; price: number; units: number; unit_share: number;
      net_sales: number; return_rate_units: number | null; inventory: number | null; available: boolean }[] }[]
  data_quality: Record<string, number | null>
  period: { start: string; end: string }
}
type Pair = { a_title: string; b_title: string; orders_together: number; confidence_a_to_b: number; confidence_b_to_a: number; lift: number }

export default function Products() {
  return <NeedStore title="Products">{(s) => <ProductsFor store={s} />}</NeedStore>
}

function ProductsFor({ store }: { store: StoreRow }) {
  const { days } = useApp()
  const [tab, setTab] = useState('performance')
  const [focus, setFocus] = useState<P | null>(null)
  const q = useQuery({ queryKey: ['products', store.id, days], queryFn: () => api.get<Perf>(`/stores/${store.id}/products?days=${days}`) })
  const cp = useQuery({ queryKey: ['copurchase', store.id, days], enabled: tab === 'pairs',
    queryFn: () => api.get<{ pairs: Pair[]; multi_product_share: number; min_pair_orders: number; note: string }>(`/stores/${store.id}/copurchase?days=${days}`) })
  const { sorted, th } = useSort<P>(q.data?.products ?? [], 'net_sales')
  if (q.isLoading) return <Loading what="Analysing products" />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  const c = d.concentration
  const cur = store.currency
  const cov = d.data_quality.cost_coverage
  return (
    <>
      <PageHead title="Products" desc={`Product and variant performance for ${store.code}, ${d.period.start} to ${d.period.end}. Growth compares with the previous period of equal length.`} />
      {cov != null && cov < 0.95 && <div className="notice warning" style={{ marginBottom: 12 }}>
        Unit costs cover {pct(cov)} of net sales. Margins are shown only for products with ≥95% cost coverage.</div>}
      <div className="kpis">
        <Kpi label="Products with sales" value={num(Number(c.products_with_sales))} />
        <Kpi label="Top product share" value={pct(Number(c.top1_share))} sub="of net sales" />
        <Kpi label="Top 3 share" value={pct(Number(c.top3_share))} />
        <Kpi label="Products for 80% of sales" value={num(Number(c.products_for_80pct))} />
        <Kpi label="Effective products" value={num(Number(c.effective_products), 1)} title={String(c.note)} sub="1 / HHI" />
      </div>
      <div className="mt">
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'performance', label: 'Performance' }, { id: 'variants', label: 'Variants' },
          { id: 'pairs', label: 'Bought together' }, { id: 'unsold', label: `No sales (${d.unsold_active_products.length})` },
        ]} />
      </div>
      {tab === 'performance' && (
        <Card>
          <div className="table-wrap" style={{ maxHeight: 640 }}>
            <table>
              <thead><tr>
                {th('rank', '#')}<th>Product</th>{th('net_sales', 'Net sales')}{th('share_of_net', 'Share')}{th('growth', 'Growth')}
                {th('units', 'Units')}{th('avg_selling_price', 'Avg price')}{th('discounted_unit_share', 'Disc. units')}
                {th('gross_margin_pct', 'Margin')}{th('return_rate_units', 'Returns')}{th('solo_order_share', 'Solo orders')}
                {th('days_of_cover', 'Days cover')}
              </tr></thead>
              <tbody>
                {sorted.map((p) => (
                  <tr key={p.product_id} className="clickable" onClick={() => setFocus(p)}>
                    <td className="num muted">{p.rank}</td>
                    <td>{p.title}{p.variants_available < p.variants_total && <small className="muted"> · {p.variants_available}/{p.variants_total} variants available</small>}</td>
                    <td className="num">{money(p.net_sales, cur)}</td>
                    <td><Share value={p.share_of_net} /></td>
                    <td className={`num ${p.growth == null ? '' : p.growth >= 0 ? 'delta-up' : 'delta-down'}`}>{p.prev_net_sales ? signedPct(p.growth) : <span className="muted">new</span>}</td>
                    <td className="num">{num(p.units)}</td>
                    <td className="num">{money(p.avg_selling_price, cur, 2)}</td>
                    <td className="num">{pct(p.discounted_unit_share, 0)}</td>
                    <td className="num">{p.gross_margin_pct == null ? <span className="muted" title={`Cost coverage ${pct(p.cost_coverage)}`}>—</span> : pct(p.gross_margin_pct)}</td>
                    <td className="num">{pct(p.return_rate_units)}</td>
                    <td className="num" title="Orders containing only this product">{pct(p.solo_order_share, 0)}</td>
                    <td className={`num ${p.days_of_cover != null && p.days_of_cover < 21 ? 'delta-down' : ''}`}>{p.days_of_cover == null ? '—' : num(p.days_of_cover)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Source>{store.is_demo ? 'demo orders' : 'synced store orders'}; inventory from the last catalogue sync. High concentration is not a problem by itself — see Opportunities for the assessment.</Source>
        </Card>
      )}
      {tab === 'variants' && (
        <div className="stack">
          {d.variants.length === 0 && <Card><small>No multi-variant products with sales.</small></Card>}
          {d.variants.slice(0, 20).map((pv) => (
            <Card key={pv.product_id} title={pv.title} extra={<small>{num(pv.units)} units · even share would be {pct(pv.expected_share_if_even, 0)}{pv.uniform_price ? ' · same price on all variants' : ''}</small>}>
              <table>
                <thead><tr><th>Variant</th><th>SKU</th><th className="num">Price</th><th className="num">Units</th><th>Unit share</th><th className="num">Returns</th><th className="num">Stock</th></tr></thead>
                <tbody>{pv.variants.map((v) => (
                  <tr key={v.variant_id}>
                    <td>{v.title}{!v.available && <span className="badge critical" style={{ marginLeft: 6 }}>✕ unavailable</span>}</td>
                    <td className="mono">{v.sku}</td><td className="num">{money(v.price, cur, 2)}</td><td className="num">{num(v.units)}</td>
                    <td><Share value={v.unit_share} /></td><td className="num">{pct(v.return_rate_units)}</td><td className="num">{v.inventory ?? '—'}</td>
                  </tr>))}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      )}
      {tab === 'pairs' && (
        <Card title="Products bought together" extra={cp.data && <small>{pct(cp.data.multi_product_share)} of orders contain 2+ products · pairs with ≥{cp.data.min_pair_orders} orders</small>}>
          {cp.isLoading ? <Loading /> : cp.error ? <ErrorBox error={cp.error} /> : !cp.data?.pairs.length ? <small>No pairs above the minimum order count.</small> : (
            <table>
              <thead><tr><th>Product A</th><th>Product B</th><th className="num">Orders together</th><th className="num">A → B</th><th className="num">B → A</th><th className="num" title="Times more often than chance">Lift</th></tr></thead>
              <tbody>{cp.data.pairs.map((p, i) => (
                <tr key={i}><td>{p.a_title}</td><td>{p.b_title}</td><td className="num">{p.orders_together}</td>
                  <td className="num">{pct(p.confidence_a_to_b)}</td><td className="num">{pct(p.confidence_b_to_a)}</td><td className="num"><strong>{p.lift.toFixed(2)}×</strong></td></tr>))}
              </tbody>
            </table>
          )}
          {cp.data && <Source>{cp.data.note}</Source>}
        </Card>
      )}
      {tab === 'unsold' && (
        <Card title="Active products without sales in the period">
          {d.unsold_active_products.length === 0 ? <small>Every active product sold at least once.</small> : (
            <table><thead><tr><th>Product</th><th className="num">Tracked stock</th></tr></thead>
              <tbody>{d.unsold_active_products.map((p) => <tr key={p.product_id}><td>{p.title}</td><td className="num">{p.inventory_units}</td></tr>)}</tbody></table>
          )}
        </Card>
      )}
      <ProductDrawer store={store} p={focus} onClose={() => setFocus(null)} />
    </>
  )
}

function ProductDrawer({ store, p, onClose }: { store: StoreRow; p: P | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ['pweekly', store.id, p?.product_id], enabled: !!p,
    queryFn: () => api.get<{ week: string; units: number; net_sales: number }[]>(`/stores/${store.id}/products/${p!.product_id}/weekly?weeks=52`) })
  const cur = store.currency
  return (
    <Drawer open={!!p} onClose={onClose} title={<h2>{p?.title}</h2>}>
      {p && (
        <>
          <div className="kpis">
            <Kpi label="Net sales" value={money(p.net_sales, cur)} delta={p.prev_net_sales ? p.growth : null} />
            <Kpi label="Units" value={num(p.units)} />
            <Kpi label="Buyers" value={num(p.buyers)} />
            <Kpi label="AOV of its orders" value={money(p.aov_of_orders_with_product, cur, 2)} />
            <Kpi label="Price range" value={p.price_min === p.price_max ? money(p.price_min, cur, 2) : `${money(p.price_min, cur)}–${money(p.price_max, cur)}`} sub={`${p.price_changes_in_period} price change(s) in period`} />
          </div>
          <Card title="Units per week (last 52 weeks)" className="mt">
            {q.data?.length ? <TrendLine data={q.data} x="week" y="units" name="units" /> : <Loading />}
          </Card>
        </>
      )}
    </Drawer>
  )
}
