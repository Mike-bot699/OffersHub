import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { date, money, num, pct } from '../format'
import { NeedStore } from '../NeedStore'
import type { StoreRow } from '../state'
import { Card, Empty, ErrorBox, Field, Kpi, Loading, Source, useSort } from '../ui'

export type ReportLine = {
  name: string; method: string | null; discount_class: string | null; orders: number; discounts: number; gross_sales: number
  net_sales: number; aov: number | null; discount_rate: number | null; codes_in_group: number; last_day: string | null; offers: string[]
}
type Totals = { orders: number; discounts: number; gross_sales: number; net_sales: number; aov: number | null; discount_rate: number | null }
export type DiscountSummary = {
  report: null | { id: number; period_start: string; period_end: string; source: string; filename: string | null; by_day: boolean }
  totals: { all: Totals; codes: Totals; automatic: Totals }
  lines: ReportLine[]; excluded_tests: string[]; grouped: Record<string, number>; note: string
}
type ImportResult = { dry_run: boolean; rows: number; period_start: string; period_end: string; by_day: boolean; codes: number
  automatic: number; test_rows: string[]; replaced_previous: boolean }

export function DiscountCodes() {
  return <NeedStore title="Discount codes">{(s) => <ReportFor store={s} />}</NeedStore>
}

function ReportFor({ store }: { store: StoreRow }) {
  const q = useQuery({ queryKey: ['discount-report', store.id], queryFn: () => api.get<DiscountSummary>(`/stores/${store.id}/discount-report`) })
  const [method, setMethod] = useState<'all' | 'code' | 'automatic'>('all')
  const lines = (q.data?.lines ?? []).filter((l) => method === 'all' || (method === 'automatic' ? l.method === 'automatic' : l.method !== 'automatic'))
  const { sorted, th } = useSort(lines, 'orders')
  const cur = store.currency
  const d = q.data
  return (
    <div className="stack">
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !d?.report ? (
        <Card><Empty title="No discount report yet">Import the Shopify "Sales by discount code" export below.</Empty></Card>
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
            extra={<div className="seg">{(['all', 'code', 'automatic'] as const).map((m) => <button key={m} className={method === m ? 'on' : ''} onClick={() => setMethod(m)}>{m === 'all' ? 'All' : m === 'code' ? 'Codes' : 'Automatic'}</button>)}</div>}>
            <div className="table-wrap" style={{ maxHeight: 620 }}>
              <table>
                <thead><tr><th>Discount</th><th>Method</th><th>Offer</th>{th('orders', 'Orders')}{th('discounts', 'Discounts')}{th('gross_sales', 'Gross sales')}{th('net_sales', 'Net sales')}{th('aov', 'AOV')}{th('discount_rate', 'Discount %')}</tr></thead>
                <tbody>{sorted.map((l) => (
                  <tr key={l.name}>
                    <td className={l.method === 'automatic' ? '' : 'mono'} style={{ maxWidth: 320 }}>{l.name}{l.codes_in_group > 1 && <small className="muted"> · {l.codes_in_group} codes</small>}</td>
                    <td><small>{l.method === 'automatic' ? 'Automatic' : 'Code'}{l.discount_class ? ` · ${l.discount_class}` : ''}</small></td>
                    <td><small>{l.offers.length ? l.offers.join(', ') : <span className="muted">—</span>}</small></td>
                    <td className="num">{num(l.orders)}</td><td className="num">{money(l.discounts, cur)}</td><td className="num">{money(l.gross_sales, cur)}</td>
                    <td className="num">{money(l.net_sales, cur)}</td><td className="num">{money(l.aov, cur, 2)}</td><td className="num">{pct(l.discount_rate)}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
            <Source>
              Shopify "Sales by discount code" ({d.report.filename}). {d.note}
              {Object.keys(d.grouped).length > 0 && ` Grouped: ${Object.entries(d.grouped).map(([g, n]) => `${g} (${n})`).join(', ')}.`}
              {d.excluded_tests.length > 0 && ` Test codes excluded: ${d.excluded_tests.join(', ')}.`}
            </Source>
          </Card>
        </>
      )}
      <DiscountImport store={store} />
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
