import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { dateTime, label, money, num, pct, signedPct } from '../format'
import { NeedStore } from '../NeedStore'
import { useApp, type StoreRow } from '../state'
import { TrendLine } from '../charts'
import { Card, ErrorBox, Field, Loading, PageHead, Source, Tabs } from '../ui'

type K = Record<string, number | null>
type Summary = {
  period: { start: string; end: string }
  kpis: K & { contribution_missing: string[]; cost_basis: string }
  previous: { period: { start: string; end: string }; kpis: K; change: K }
  year_ago: { period: { start: string; end: string }; kpis: K; change: K; available: boolean }
  customers: { available: boolean; reason?: string; new_customer_orders?: number; returning_customer_orders?: number; returning_order_share?: number
    new_customer_aov?: number; returning_customer_aov?: number; repeat_90d?: { cohort_customers: number; repeat_customers: number; rate: number | null; definition: string } }
  discounts: { discounted_orders: number; full_price_orders: number; discounted_net_sales_share: number | null; aov_discounted: number | null
    aov_full_price: number | null; avg_discount_depth: number | null; top_codes: { code: string; orders: number; net_sales: number; discounts: number }[]; note: string }
  monthly: { month: string; orders: number; net_sales: number }[]
  seasonality: { available: boolean; reason?: string; monthly_index?: Record<string, number>; peak_month?: number; note?: string }
  data_quality: Record<string, number | string | null>
}

const ROWS: [string, string, 'money' | 'pct' | 'num' | 'money2'][] = [
  ['orders', 'Orders', 'num'], ['gross_sales', 'Gross sales', 'money'], ['discounts', 'Discounts', 'money'], ['net_sales', 'Net sales', 'money'],
  ['returns', 'Returns', 'money'], ['aov', 'AOV', 'money2'], ['units_per_order', 'Units / order', 'num'], ['discount_rate', 'Discount rate', 'pct'],
  ['discounted_order_share', 'Orders with a discount', 'pct'], ['return_rate', 'Return rate', 'pct'], ['gross_margin_pct', 'Gross margin', 'pct'],
  ['contribution', 'Contribution', 'money'], ['sessions', 'Sessions', 'num'], ['conversion_rate', 'Conversion rate', 'pct'],
]

export default function Analytics() {
  return <NeedStore title="Analytics">{(s) => <AnalyticsFor store={s} />}</NeedStore>
}

function AnalyticsFor({ store }: { store: StoreRow }) {
  const { days } = useApp()
  const [tab, setTab] = useState('performance')
  const q = useQuery({ queryKey: ['summary', store.id, days], queryFn: () => api.get<Summary>(`/stores/${store.id}/summary?days=${days}`) })
  const cur = store.currency
  const fmt = (v: number | null | undefined, kind: string) =>
    kind === 'money' ? money(v, cur) : kind === 'money2' ? money(v, cur, 2) : kind === 'pct' ? pct(v, 2) : num(v, v != null && v % 1 ? 2 : 0)
  return (
    <>
      <PageHead title="Analytics" desc="Comparisons, customers, discounting, seasonality and scenario simulations. Formulas are documented in docs/METRICS.md." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'performance', label: 'Performance' }, { id: 'customers', label: 'Customers & discounts' }, { id: 'simulate', label: 'Simulations' }]} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : tab === 'performance' ? (
        <div className="stack">
          <Card title="Period comparison">
            <div className="table-wrap">
              <table>
                <thead><tr><th>Metric</th><th className="num">{q.data!.period.start} → {q.data!.period.end}</th><th className="num">Previous period</th><th className="num">Change</th><th className="num">Same period last year</th><th className="num">YoY change</th></tr></thead>
                <tbody>{ROWS.map(([k, l, kind]) => (
                  <tr key={k}><td>{l}</td><td className="num"><strong>{fmt(q.data!.kpis[k] as number | null, kind)}</strong></td><td className="num">{fmt(q.data!.previous.kpis[k], kind)}</td>
                    <td className="num">{signedPct(q.data!.previous.change[k])}</td>
                    <td className="num">{q.data!.year_ago.available ? fmt(q.data!.year_ago.kpis[k], kind) : '—'}</td>
                    <td className="num">{q.data!.year_ago.available ? signedPct(q.data!.year_ago.change[k]) : '—'}</td></tr>))}
                </tbody>
              </table>
            </div>
            <Source>
              Net sales = gross sales − discounts (before returns, excl. shipping and tax). Cost basis: {label(q.data!.kpis.cost_basis)}.
              {q.data!.kpis.contribution_missing.length > 0 && <> Contribution excludes: {q.data!.kpis.contribution_missing.join(', ')} — set them in Stores → settings.</>}
              {' '}Observed changes are not causal effects.
            </Source>
          </Card>
          <div className="grid g2">
            <Card title="Net sales by month">
              <TrendLine data={q.data!.monthly} x="month" y="net_sales" name="net sales" currency={cur} />
            </Card>
            <Card title="Seasonality">
              {!q.data!.seasonality.available ? <small>{q.data!.seasonality.reason}</small> : (
                <>
                  <table><thead><tr>{Object.keys(q.data!.seasonality.monthly_index!).map((m) => <th key={m} className="num">{new Date(2000, Number(m) - 1).toLocaleString(undefined, { month: 'short' })}</th>)}</tr></thead>
                    <tbody><tr>{Object.entries(q.data!.seasonality.monthly_index!).map(([m, v]) => <td key={m} className={`num ${v >= 1.15 ? 'delta-up' : v <= 0.85 ? 'delta-down' : ''}`}>{v.toFixed(2)}</td>)}</tr></tbody></table>
                  <Source>{q.data!.seasonality.note}</Source>
                </>)}
            </Card>
          </div>
          <Card title="Data quality for this period">
            <table><tbody>{Object.entries(q.data!.data_quality).map(([k, v]) => (
              <tr key={k}><td>{label(k)}</td><td className="num">{typeof v === 'number' && /coverage|share/.test(k) ? pct(v) : typeof v === 'number' ? num(v) : String(v ?? '—')}</td></tr>))}</tbody></table>
          </Card>
        </div>
      ) : tab === 'customers' ? <Customers s={q.data!} cur={cur} /> : <Simulations store={store} s={q.data!} />}
    </>
  )
}

function Customers({ s, cur }: { s: Summary; cur: string }) {
  const c = s.customers
  const d = s.discounts
  return (
    <div className="grid g2">
      <Card title="New vs returning customers">
        {!c.available ? <small>{c.reason}</small> : (
          <table><tbody>
            <tr><td>Orders from new customers</td><td className="num">{num(c.new_customer_orders)}</td></tr>
            <tr><td>Orders from returning customers</td><td className="num">{num(c.returning_customer_orders)} ({pct(c.returning_order_share)})</td></tr>
            <tr><td>AOV new / returning</td><td className="num">{money(c.new_customer_aov, cur, 2)} / {money(c.returning_customer_aov, cur, 2)}</td></tr>
            <tr><td>90-day repeat rate</td><td className="num"><strong>{pct(c.repeat_90d?.rate)}</strong> ({c.repeat_90d?.repeat_customers} of {c.repeat_90d?.cohort_customers})</td></tr>
          </tbody></table>)}
        {c.repeat_90d && <Source>{c.repeat_90d.definition}</Source>}
      </Card>
      <Card title="Discount dependency">
        <table><tbody>
          <tr><td>Orders with a discount</td><td className="num">{num(d.discounted_orders)} of {num(d.discounted_orders + d.full_price_orders)}</td></tr>
          <tr><td>Share of net sales from discounted orders</td><td className="num">{pct(d.discounted_net_sales_share)}</td></tr>
          <tr><td>Average discount depth (discounted orders)</td><td className="num">{pct(d.avg_discount_depth)}</td></tr>
          <tr><td>AOV discounted / full price</td><td className="num">{money(d.aov_discounted, cur, 2)} / {money(d.aov_full_price, cur, 2)}</td></tr>
        </tbody></table>
        <div className="section-title">Most used codes</div>
        <table><thead><tr><th>Code</th><th className="num">Orders</th><th className="num">Net sales</th><th className="num">Discount</th></tr></thead>
          <tbody>{d.top_codes.map((t) => <tr key={t.code}><td className="mono">{t.code}</td><td className="num">{t.orders}</td><td className="num">{money(t.net_sales, cur)}</td><td className="num">{money(t.discounts, cur)}</td></tr>)}</tbody></table>
        <Source>{d.note}</Source>
      </Card>
    </div>
  )
}

type SimDef = { kind: string; title: string; fields: [string, string, string?][]; explain: string }
const SIMS: SimDef[] = [
  { kind: 'price_change', title: 'Price change', explain: 'Revenue and gross profit at several volume responses, plus the break-even volume change.',
    fields: [['current_price', 'Current price'], ['new_price', 'New price'], ['unit_cost', 'Unit cost (blank = unknown)'], ['units', 'Units in period']] },
  { kind: 'bundle', title: 'Bundle A + B', explain: 'Movers from A-only orders vs. discount given to orders that already buy both.',
    fields: [['price_a', 'Price A'], ['price_b', 'Price B'], ['cost_a', 'Cost A'], ['cost_b', 'Cost B'], ['orders_with_a', 'Orders with A'], ['orders_with_both', 'Orders with A and B'], ['bundle_discount_pct', 'Bundle discount (0–1)', '0.1']] },
  { kind: 'quantity', title: 'Buy N, save X%', explain: 'Uses this store\'s observed units per order for the selected period.',
    fields: [['min_units', 'Minimum units', '2'], ['discount_pct', 'Discount (0–1)', '0.1'], ['margin_pct', 'Margin (0–1, blank = observed)']] },
  { kind: 'discount_depth', title: 'Discount depth', explain: 'Redemptions needed for a different discount to earn the same gross profit.',
    fields: [['avg_order_value', 'Average order value'], ['margin_pct', 'Margin (0–1)'], ['current_pct', 'Current discount (0–1)'], ['proposed_pct', 'Proposed discount (0–1)'], ['current_redemptions', 'Current redemptions']] },
  { kind: 'gift_vs_discount', title: 'Gift vs discount', explain: 'Cost per qualifying order of a percentage discount vs a free gift.',
    fields: [['qualifying_aov', 'AOV of qualifying orders'], ['discount_pct', 'Discount (0–1)'], ['gift_unit_cost', 'Gift unit cost'], ['gift_perceived_value', 'Gift price / perceived value']] },
]

function Simulations({ store, s }: { store: StoreRow; s: Summary }) {
  const { days } = useApp()
  const [kind, setKind] = useState('price_change')
  const def = SIMS.find((x) => x.kind === kind)!
  const [vals, setVals] = useState<Record<string, string>>({})
  const saved = useQuery({ queryKey: ['sims', store.id], queryFn: () => api.get<{ id: number; kind: string; name: string; created_at: string }[]>(`/stores/${store.id}/simulations`) })
  const run = useMutation({
    mutationFn: (save: boolean) => {
      const inputs: Record<string, number | null> = {}
      for (const [k, , dflt] of def.fields) {
        const raw = vals[`${kind}.${k}`] ?? dflt ?? ''
        inputs[k] = raw === '' ? null : Number(raw)
      }
      const end = s.period.end
      return api.post<Record<string, unknown>>(`/stores/${store.id}/simulations`, { kind, inputs, save, name: def.title, start: s.period.start, end })
    },
    onSuccess: () => saved.refetch(),
  })
  return (
    <div className="grid g2">
      <Card title="Scenario simulator">
        <div className="row" style={{ marginBottom: 10 }}>
          <select value={kind} onChange={(e) => { setKind(e.target.value); run.reset() }}>{SIMS.map((x) => <option key={x.kind} value={x.kind}>{x.title}</option>)}</select>
        </div>
        <p className="secondary">{def.explain}</p>
        <div className="form-grid">
          {def.fields.map(([k, l, dflt]) => (
            <Field key={k} label={l}><input type="number" step="any" value={vals[`${kind}.${k}`] ?? dflt ?? ''} onChange={(e) => setVals({ ...vals, [`${kind}.${k}`]: e.target.value })} /></Field>
          ))}
        </div>
        <div className="row mt">
          <button className="primary" onClick={() => run.mutate(false)} disabled={run.isPending}>Run</button>
          <button onClick={() => run.mutate(true)} disabled={run.isPending}>Run &amp; save</button>
        </div>
        <div className="mt"><ErrorBox error={run.error} /></div>
        {run.data && <SimResult r={run.data} cur={store.currency} />}
        <small className="muted">Period for data-driven simulations: last {days} days.</small>
      </Card>
      <Card title="Saved simulations">
        {!saved.data?.length ? <small>None yet.</small> : (
          <table><tbody>{saved.data.map((x) => <tr key={x.id}><td>#{x.id}</td><td>{x.name}</td><td>{label(x.kind)}</td><td className="muted">{dateTime(x.created_at)}</td></tr>)}</tbody></table>)}
      </Card>
    </div>
  )
}

function SimResult({ r, cur }: { r: Record<string, unknown>; cur: string }) {
  const scen = (r.scenarios as Record<string, number | null>[] | undefined) ?? []
  const scalars = Object.entries(r).filter(([k, v]) => typeof v !== 'object' && !['simulation_id'].includes(k))
  const isMoney = (k: string) => /revenue|profit|price|cost|effect|discount_|gp_|bundle_price|cannibal/.test(k) && !/pct|rate|ratio|uplift|change/.test(k)
  const isRate = (k: string) => /pct|rate|ratio|uplift|change|share/.test(k)
  const f = (k: string, v: unknown) => typeof v !== 'number' ? String(v) : isRate(k) ? pct(v, 1) : isMoney(k) ? money(v, cur, 2) : num(v, 2)
  return (
    <div className="mt">
      <table><tbody>{scalars.map(([k, v]) => <tr key={k}><td>{label(k)}</td><td className="num"><strong>{f(k, v)}</strong></td></tr>)}</tbody></table>
      {scen.length > 0 && (
        <table className="mt"><thead><tr>{Object.keys(scen[0]).map((k) => <th key={k} className="num">{label(k)}</th>)}</tr></thead>
          <tbody>{scen.map((row, i) => <tr key={i}>{Object.entries(row).map(([k, v]) => <td key={k} className="num">{f(k, v)}</td>)}</tr>)}</tbody></table>)}
      {Array.isArray(r.assumptions) && <><div className="section-title">Assumptions</div><ul className="tight">{(r.assumptions as string[]).map((a, i) => <li key={i}>{a}</li>)}</ul></>}
    </div>
  )
}
