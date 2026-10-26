import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { money, num, pct } from '../format'
import { NeedStore } from '../NeedStore'
import { useApp, type StoreRow } from '../state'
import { Link } from 'react-router-dom'
import { Bars, TrendLine } from '../charts'
import type { DiscountAnalysis } from './Overview'
import { Card, ErrorBox, Field, Kpi, Loading, PageHead, Source } from '../ui'

export type Basket = {
  orders: number; aov: number | null; percentiles: Record<string, number | null>
  distribution: { from: number; to: number | null; orders: number }[]
  units_mix: { units: number | string; orders: number; share: number | null }[]
  single_unit_share: number | null; units_per_order: number | null; avg_unit_price: number | null
  margin_pct: number | null; margin_basis: string; free_shipping_threshold: number | null; gift_cost_assumption: number | null
  threshold_candidates: { threshold: number; is_current_free_shipping: boolean; share_above: number | null; share_reachable: number | null
    orders_reachable: number; avg_gap_reachable: number | null; break_even_mover_rate: number | null }[]
  gift_candidates: { candidates: { title: string; variant: string; price: number; unit_cost: number; value_to_cost: number; inventory: number | null; units_sold_in_period: number }[]; criteria: string; available: boolean }
}
type ThresholdSim = {
  threshold: number; orders: number; orders_above: number; orders_reachable: number; share_reachable: number | null; avg_gap_reachable: number | null
  deadweight_cost: number | null; break_even_mover_rate: number | null; break_even_feasible: boolean; margin_basis: string
  scenarios: { mover_rate: number; movers: number; incremental_revenue: number; incremental_gross_profit: number | null; incentive_cost: number; net_effect: number | null }[]
  assumptions: string[]
}

export default function BasketPage() {
  return <NeedStore title="AOV & basket">{(s) => <BasketFor store={s} />}</NeedStore>
}

function BasketFor({ store }: { store: StoreRow }) {
  const { days } = useApp()
  const q = useQuery({ queryKey: ['basket', store.id, days], queryFn: () => api.get<Basket>(`/stores/${store.id}/basket?days=${days}`) })
  const da = useQuery({ queryKey: ['discount-analysis', store.id, days], queryFn: () => api.get<DiscountAnalysis>(`/stores/${store.id}/discount-analysis?days=${days}`) })
  if (q.isLoading) return <Loading what="Analysing baskets" />
  if (q.error) return <ErrorBox error={q.error} />
  const b = q.data!
  const cur = store.currency
  return (
    <>
      <PageHead title="AOV & basket" desc="Where order value sits, how it moves week by week, and how close orders are to potential thresholds. Higher AOV is only good if contribution rises too."
        actions={<Link to="/simulator" className="btn">Simulate a threshold offer →</Link>} />
      <div className="kpis">
        <Kpi label="AOV" value={money(b.aov, cur, 2)} />
        <Kpi label="Median order" value={money(b.percentiles.p50, cur, 2)} sub={`p75 ${money(b.percentiles.p75, cur)} · p90 ${money(b.percentiles.p90, cur)}`} />
        <Kpi label="Units / order" value={num(b.units_per_order, 2)} />
        <Kpi label="Single-unit orders" value={pct(b.single_unit_share)} />
        <Kpi label="Merchandise margin" value={b.margin_pct == null ? '—' : pct(b.margin_pct)} sub={b.margin_basis} />
        <Kpi label="Free-shipping threshold" value={b.free_shipping_threshold ? money(b.free_shipping_threshold, cur) : 'not set'} sub="Settings → store" />
      </div>
      {da.data && (
        <div className="grid g2 mt">
          <Card title="AOV by week"><TrendLine data={da.data.weekly} x="week" y="aov" name="AOV" currency={cur} /></Card>
          <Card title="Units per order by week"><TrendLine data={da.data.weekly} x="week" y="units_per_order" name="units per order" /></Card>
        </div>
      )}
      <div className="grid g2 mt">
        <Card title="Order value distribution (net, per order)">
          <Bars data={b.distribution} x="from" y="orders" name="orders" fmtX={(v) => money(Number(v), cur)}
            highlight={(r) => !!b.free_shipping_threshold && Number(r.from) <= b.free_shipping_threshold && (r.to == null || Number(r.to) > b.free_shipping_threshold)} />
          <small className="muted">Bucket containing the current free-shipping threshold is highlighted. Last bucket collects the top 2%.</small>
        </Card>
        <Card title="Units per order">
          <Bars data={b.units_mix} x="units" y="orders" name="orders" />
        </Card>
      </div>
      <Card title="Candidate thresholds" className="mt" extra={<small>Derived from the 50th–80th percentiles, rounded to price points</small>}>
        <table>
          <thead><tr><th className="num">Threshold</th><th className="num">Orders already above</th><th className="num">Orders within 25% below</th><th className="num">Avg gap</th><th className="num">Break-even top-up rate</th><th></th></tr></thead>
          <tbody>{b.threshold_candidates.map((t) => (
            <tr key={t.threshold}>
              <td className="num">{money(t.threshold, cur)}</td><td className="num">{pct(t.share_above)}</td>
              <td className="num">{pct(t.share_reachable)} <small className="muted">({num(t.orders_reachable)})</small></td>
              <td className="num">{money(t.avg_gap_reachable, cur, 2)}</td>
              <td className="num">{t.is_current_free_shipping ? <span className="muted">already offered</span> : t.break_even_mover_rate == null ? <span className="muted">{b.gift_cost_assumption ? 'never breaks even' : 'set gift cost'}</span> : pct(t.break_even_mover_rate)}</td>
              <td>{t.is_current_free_shipping && <span className="badge info">current free shipping</span>}</td>
            </tr>))}
          </tbody>
        </table>
        <Source>Break-even assumes a gift costing {b.gift_cost_assumption ? money(b.gift_cost_assumption, cur, 2) : '(not set — Settings → default gift cost)'} on every qualifying order, including those already above the threshold.</Source>
      </Card>
      <Card title="Free-gift candidates" className="mt">
        {!b.gift_candidates.available ? <small>Needs unit costs. Import costs to find products that are cheap to give and valued by customers.</small>
          : b.gift_candidates.candidates.length === 0 ? <small>No product meets the criteria.</small> : (
            <table><thead><tr><th>Product</th><th className="num">Price</th><th className="num">Unit cost</th><th className="num">Value ÷ cost</th><th className="num">Stock</th><th className="num">Units sold</th></tr></thead>
              <tbody>{b.gift_candidates.candidates.map((g, i) => (
                <tr key={i}><td>{g.title} <small className="muted">{g.variant}</small></td><td className="num">{money(g.price, cur, 2)}</td><td className="num">{money(g.unit_cost, cur, 2)}</td>
                  <td className="num">{g.value_to_cost.toFixed(1)}×</td><td className="num">{g.inventory ?? '—'}</td><td className="num">{g.units_sold_in_period}</td></tr>))}
              </tbody></table>)}
        <Source>{b.gift_candidates.criteria}</Source>
      </Card>
    </>
  )
}

export function ThresholdSimulator({ store, basket }: { store: StoreRow; basket: Basket }) {
  const { days } = useApp()
  const cur = store.currency
  const [f, setF] = useState({
    threshold: String(basket.threshold_candidates[1]?.threshold ?? basket.free_shipping_threshold ?? 100),
    incentive_cost: String(basket.gift_candidates.candidates[0]?.unit_cost ?? basket.gift_cost_assumption ?? ''),
    margin_pct: basket.margin_pct != null ? String(Math.round(basket.margin_pct * 100)) : '',
    reach_window: '25', overshoot_pct: '5', already_offered: false,
  })
  const sim = useMutation({
    mutationFn: (save: boolean) => {
      const end = new Date()
      const start = new Date(end.getTime() - (days - 1) * 86400000)
      return api.post<ThresholdSim & { simulation_id?: number }>(`/stores/${store.id}/simulations`, {
        kind: 'threshold', save, name: `Threshold ${f.threshold}`,
        start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10),
        inputs: {
          threshold: Number(f.threshold), incentive_cost: Number(f.incentive_cost),
          margin_pct: f.margin_pct === '' ? null : Number(f.margin_pct) / 100,
          reach_window: Number(f.reach_window) / 100, overshoot_pct: Number(f.overshoot_pct) / 100, already_offered: f.already_offered,
        },
      })
    },
  })
  const r = sim.data
  return (
    <Card title="Threshold incentive simulator" className="mt" extra={<small>Free gift, free shipping or reward unlocked above a spend level</small>}>
      <div className="form-grid">
        <Field label={`Threshold (${cur})`}><input type="number" value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} /></Field>
        <Field label={`Incentive cost per qualifying order (${cur})`}><input type="number" step="0.01" value={f.incentive_cost} onChange={(e) => setF({ ...f, incentive_cost: e.target.value })} /></Field>
        <Field label="Margin on added merchandise (%) — blank = observed"><input type="number" value={f.margin_pct} onChange={(e) => setF({ ...f, margin_pct: e.target.value })} /></Field>
        <Field label="Reach window below threshold (%)"><input type="number" value={f.reach_window} onChange={(e) => setF({ ...f, reach_window: e.target.value })} /></Field>
        <Field label="Overshoot above threshold (%)"><input type="number" value={f.overshoot_pct} onChange={(e) => setF({ ...f, overshoot_pct: e.target.value })} /></Field>
        <label className="field">Already offered today?<span className="row"><input type="checkbox" checked={f.already_offered} onChange={(e) => setF({ ...f, already_offered: e.target.checked })} /> existing qualifiers cost nothing new</span></label>
      </div>
      <div className="row mt">
        <button className="primary" onClick={() => sim.mutate(false)} disabled={sim.isPending || !f.incentive_cost}>Simulate</button>
        <button onClick={() => sim.mutate(true)} disabled={sim.isPending || !f.incentive_cost}>Simulate &amp; save</button>
        {r?.simulation_id && <small className="muted">Saved as simulation #{r.simulation_id}</small>}
      </div>
      <div className="mt"><ErrorBox error={sim.error} /></div>
      {r && (
        <div className="mt">
          <div className="kpis">
            <Kpi label="Orders already above" value={num(r.orders_above)} sub={`deadweight cost ${money(r.deadweight_cost, cur)}`} />
            <Kpi label="Orders within reach" value={num(r.orders_reachable)} sub={`avg gap ${money(r.avg_gap_reachable, cur, 2)}`} />
            <Kpi label="Break-even top-up rate" value={r.break_even_mover_rate == null ? 'never' : pct(r.break_even_mover_rate)}
              sub={r.break_even_feasible ? 'share of reachable orders that must top up' : 'the incentive cannot pay for itself under these inputs'} />
          </div>
          <table className="mt">
            <thead><tr><th className="num">If this share tops up</th><th className="num">Movers</th><th className="num">Added revenue</th><th className="num">Added gross profit</th><th className="num">Incentive cost</th><th className="num">Net effect</th></tr></thead>
            <tbody>{r.scenarios.map((s) => (
              <tr key={s.mover_rate}><td className="num">{pct(s.mover_rate, 0)}</td><td className="num">{num(s.movers, 1)}</td>
                <td className="num">{money(s.incremental_revenue, cur)}</td><td className="num">{money(s.incremental_gross_profit, cur)}</td>
                <td className="num">{money(s.incentive_cost, cur)}</td>
                <td className={`num ${s.net_effect == null ? '' : s.net_effect >= 0 ? 'delta-up' : 'delta-down'}`}><strong>{money(s.net_effect, cur)}</strong></td></tr>))}
            </tbody>
          </table>
          <div className="section-title">Assumptions</div>
          <ul className="tight">{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
          <small className="muted">Over the selected {days}-day period. These are scenarios, not forecasts — validate with an experiment.</small>
        </div>
      )}
    </Card>
  )
}
