import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from './api'
import { dateTime, label } from './format'
import { Confidence, Drawer, ErrorBox, ImpactView, Markdown, Status, type Impact } from './ui'

export type Rec = {
  id: number
  store_id: number
  store_code: string
  currency: string
  title: string
  strategy_type: string
  scope_type: string
  scope: { labels?: string[]; product_ids?: number[] }
  problem: string
  proposed_action: string
  economic_rationale: string
  evidence: { label: string; value: unknown; source: string; basis?: string | null }[]
  estimated_impact: Impact | null
  assumptions: string[]
  missing_data: string[]
  confidence: string
  confidence_reason: string
  risks: string[]
  complexity: string
  recommended_test: string | null
  success_metrics: string[]
  next_action: string
  priority_score: number
  score_breakdown: Record<string, unknown>
  status: string
  is_demo: boolean
  prior_evidence: { finding: string; direction: string; evidence_strength: string }[]
  analysis_start: string
  analysis_end: string
  generated_at: string
  objective: string | null
  ai_commentary: string | null
  ai_model: string | null
  ai_generated_at: string | null
}

export function RecCard({ rec, onOpen }: { rec: Rec; onOpen: () => void }) {
  return (
    <div className="rec">
      <div className="rec-head">
        <div className="score" title="Priority score (0–100+): impact, evidence, feasibility, risk, objective alignment">
          {Math.round(rec.priority_score)}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3><a href="#" onClick={(e) => { e.preventDefault(); onOpen() }}>{rec.title}</a></h3>
          <div className="meta">
            {rec.is_demo ? <span className="badge demo">DEMO</span> : <span className="badge">{rec.store_code}</span>}
            <span className="badge">{label(rec.strategy_type)}</span>
            <Confidence value={rec.confidence} reason={rec.confidence_reason} />
            <span className="badge">Effort: {rec.complexity}</span>
            {rec.missing_data.length > 0 && <span className="badge warning">▲ {rec.missing_data.length} missing input(s)</span>}
            {rec.status !== 'open' && <Status value={rec.status} />}
          </div>
          <p className="secondary" style={{ marginTop: 6 }}>{rec.problem}</p>
          {rec.estimated_impact && (
            <div style={{ marginTop: 4 }}><ImpactView impact={rec.estimated_impact} currency={rec.currency} /></div>
          )}
        </div>
      </div>
    </div>
  )
}

export function RecDetail({ id, onClose }: { id: number | null; onClose: () => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const q = useQuery({
    queryKey: ['rec', id], enabled: id != null,
    queryFn: () => api.get<Rec & { ai_available: boolean }>(`/recommendations/${id}`),
  })
  const [err, setErr] = useState<unknown>(null)
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['recs'] })
    qc.invalidateQueries({ queryKey: ['rec', id] })
    qc.invalidateQueries({ queryKey: ['portfolio'] })
  }
  const status = useMutation({
    mutationFn: (s: string) => api.patch(`/recommendations/${id}`, { status: s }),
    onSuccess: refresh, onError: setErr,
  })
  const toExp = useMutation({
    mutationFn: () => api.post<{ id: number }>(`/recommendations/${id}/experiment`),
    onSuccess: () => { refresh(); qc.invalidateQueries({ queryKey: ['experiments'] }); nav('/experiments') },
    onError: setErr,
  })
  const ai = useMutation({
    mutationFn: () => api.post(`/recommendations/${id}/ai-commentary`),
    onSuccess: refresh, onError: setErr,
  })
  const r = q.data
  return (
    <Drawer open={id != null} onClose={onClose} title={r ? (
      <div>
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          {r.is_demo ? <span className="badge demo">DEMO</span> : <span className="badge">{r.store_code}</span>}
          <span className="badge">{label(r.strategy_type)}</span>
          <Status value={r.status} />
        </div>
        <h2>{r.title}</h2>
      </div>
    ) : 'Loading…'}>
      {q.error ? <ErrorBox error={q.error} /> : null}
      {r && (
        <>
          <ErrorBox error={err} />
          <div className="row" style={{ marginBottom: 12 }}>
            {r.status === 'open' && <button className="primary" onClick={() => toExp.mutate()} disabled={toExp.isPending}>Plan as experiment</button>}
            {r.status === 'open' && <button onClick={() => status.mutate('accepted')}>Accept</button>}
            {r.status === 'open' && <button onClick={() => status.mutate('dismissed')}>Dismiss</button>}
            {r.status !== 'open' && r.status !== 'in_experiment' && <button onClick={() => status.mutate('open')}>Reopen</button>}
          </div>

          <div className="section-title">Problem or opportunity</div>
          <p>{r.problem}</p>
          <div className="section-title">Proposed action</div>
          <p>{r.proposed_action}</p>
          <div className="section-title">Economic rationale</div>
          <p>{r.economic_rationale}</p>

          <div className="section-title">Estimated impact</div>
          <ImpactView impact={r.estimated_impact} currency={r.currency} />

          <div className="section-title">Evidence</div>
          <table>
            <tbody>
              {r.evidence.map((e, i) => (
                <tr key={i}>
                  <td style={{ width: '38%' }}>{e.label}</td>
                  <td>{String(e.value)}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{e.basis === 'sourced' ? `Principle — ${e.source}` : e.source}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="grid g2 mt">
            <div>
              <div className="section-title">Assumptions</div>
              {r.assumptions.length ? <ul className="tight">{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul> : <small>None stated.</small>}
            </div>
            <div>
              <div className="section-title">Missing data</div>
              {r.missing_data.length ? <ul className="tight">{r.missing_data.map((a, i) => <li key={i}>{a}</li>)}</ul> : <small>None.</small>}
            </div>
            <div>
              <div className="section-title">Risks</div>
              {r.risks.length ? <ul className="tight">{r.risks.map((a, i) => <li key={i}>{a}</li>)}</ul> : <small>None identified.</small>}
            </div>
            <div>
              <div className="section-title">Success metrics</div>
              {r.success_metrics.length ? <ul className="tight">{r.success_metrics.map((a, i) => <li key={i}>{a}</li>)}</ul> : <small>—</small>}
            </div>
          </div>

          <dl className="kv mt">
            <dt>Confidence</dt><dd><Confidence value={r.confidence} /> {r.confidence_reason}</dd>
            <dt>Implementation effort</dt><dd>{r.complexity}</dd>
            <dt>Recommended test</dt><dd>{r.recommended_test ?? '—'}</dd>
            <dt>Next action</dt><dd>{r.next_action}</dd>
            <dt>Objective</dt><dd>{label(r.objective)} — {String(r.score_breakdown.alignment_note ?? '')}</dd>
            <dt>Priority score</dt><dd>{r.priority_score} <small>({String(r.score_breakdown.weights ?? '')})</small></dd>
            <dt>Analysis period</dt><dd>{r.analysis_start} → {r.analysis_end} · generated {dateTime(r.generated_at)}</dd>
          </dl>

          {r.prior_evidence.length > 0 && (
            <>
              <div className="section-title">Previous experiments considered</div>
              <ul className="tight">
                {r.prior_evidence.map((p, i) => (
                  <li key={i}><Status value={p.evidence_strength} /> <Status value={p.direction} /> {p.finding}
                    {['weak', 'inconclusive'].includes(p.evidence_strength) && <small> — shown for context, not used to adjust this recommendation</small>}
                  </li>
                ))}
              </ul>
            </>
          )}

          <div className="section-title">AI strategist commentary</div>
          {r.ai_commentary ? (
            <div className="notice info">
              <small>AI hypotheses generated by {r.ai_model} on {dateTime(r.ai_generated_at)} from the evidence above. Not verified facts.</small>
              <Markdown text={r.ai_commentary} />
            </div>
          ) : r.ai_available ? (
            <button onClick={() => ai.mutate()} disabled={ai.isPending}>{ai.isPending ? 'Asking…' : 'Get AI commentary'}</button>
          ) : (
            <small>Off. Set EGI_ANTHROPIC_API_KEY to get an AI critique of this recommendation. The analysis above does not depend on it.</small>
          )}
        </>
      )}
    </Drawer>
  )
}
