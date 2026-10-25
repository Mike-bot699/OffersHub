import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, qs } from '../api'
import { label } from '../format'
import { RecCard, RecDetail, type Rec } from '../recs'
import { useApp } from '../state'
import { Card, Empty, ErrorBox, Loading, PageHead } from '../ui'

export default function Opportunities() {
  const { storeId, includeDemo, days, meta } = useApp()
  const [status, setStatus] = useState('open')
  const [strategy, setStrategy] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['recs', storeId, status, strategy, includeDemo],
    queryFn: () => api.get<Rec[]>(`/recommendations${qs({ store_id: storeId, status, strategy_type: strategy, include_demo: includeDemo })}`),
  })
  const run = useMutation({
    mutationFn: () => api.post('/recommendations/run', storeId ? { store_id: storeId, days } : { include_demo: includeDemo, days }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recs'] }),
  })
  return (
    <>
      <PageHead title="Opportunities"
        desc="Ranked by expected value, evidence quality, feasibility, risk and fit with each store's objectives. Every item shows its evidence, assumptions and what is missing."
        actions={<button className="primary" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? 'Analysing…' : `Analyse last ${days} days`}</button>} />
      <ErrorBox error={run.error} />
      {run.data ? <div className="notice info" style={{ marginBottom: 12 }}>
        {(run.data as { store: string; recommendations: number; detector_errors: unknown[] }[]).map((r) =>
          `${r.store}: ${r.recommendations} recommendation(s)${r.detector_errors.length ? `, ${r.detector_errors.length} detector error(s)` : ''}`).join(' · ')}
      </div> : null}
      <div className="row" style={{ marginBottom: 12 }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {['open', 'accepted', 'in_experiment', 'dismissed', 'stale', ''].map((s) => <option key={s} value={s}>{s ? label(s) : 'Any status'}</option>)}
        </select>
        <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          <option value="">All strategy types</option>
          {meta?.strategy_types.map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
      </div>
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? (
        <Card><Empty title="Nothing here">Detectors only produce a recommendation when the evidence clears an explicit threshold. Run the analysis, widen the period, or add missing data (costs, traffic).</Empty></Card>
      ) : q.data.map((r) => <RecCard key={r.id} rec={r} onOpen={() => setOpen(r.id)} />)}
      <RecDetail id={open} onClose={() => setOpen(null)} />
    </>
  )
}
