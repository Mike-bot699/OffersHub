import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, qs } from '../api'
import { date, dateTime, label, money, signedPct } from '../format'
import { useApp } from '../state'
import { Card, Drawer, Empty, ErrorBox, Field, Loading, PageHead, Status, Tabs } from '../ui'

type Exp = {
  id: number; store_id: number; store_code: string; is_demo: boolean; title: string; hypothesis: string; strategy_type: string
  status: string; design: string; primary_metric: string; target_metrics: string[]; baseline_start: string | null; baseline_end: string | null
  start_date: string | null; end_date: string | null; results: Record<string, Record<string, number>>; evaluation: Record<string, unknown>
  methodology: string | null; interpretation: string | null; evidence_strength: string | null; decision: string | null; follow_up: string | null
  limitations: string | null; costs: number | null; history: { at: string; from: string | null; to: string; note: string | null }[]
  allowed_transitions: string[]; recommendation_id: number | null
}
const FLOW = ['hypothesis', 'simulated', 'approved', 'implementing', 'measuring', 'evaluated', 'closed']

export default function Experiments() {
  const [tab, setTab] = useState('experiments')
  return (
    <>
      <PageHead title="Experiments" desc="Opportunity → Hypothesis → Simulation → Approval → Implementation → Measurement → Evaluation → Learning. Before/after comparisons are never rated above weak evidence." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'experiments', label: 'Experiments' }, { id: 'learning', label: 'Learning' }, { id: 'actions', label: 'Change proposals' }, { id: 'audit', label: 'Audit log' }]} />
      {tab === 'experiments' && <ExperimentList />}
      {tab === 'learning' && <Learning />}
      {tab === 'actions' && <Actions />}
      {tab === 'audit' && <Audit />}
    </>
  )
}

function ExperimentList() {
  const { storeId, includeDemo, stores, meta } = useApp()
  const [open, setOpen] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['experiments', storeId, includeDemo], queryFn: () => api.get<Exp[]>(`/experiments${qs({ store_id: storeId, include_demo: includeDemo })}`) })
  const [f, setF] = useState({ title: '', hypothesis: '', strategy_type: 'free_gift', design: 'ab_test', store_id: '' })
  const create = useMutation({
    mutationFn: () => api.post<Exp>('/experiments', { ...f, store_id: Number(f.store_id || storeId || stores[0]?.id) }),
    onSuccess: (e) => { qc.invalidateQueries({ queryKey: ['experiments'] }); setCreating(false); setOpen(e.id) },
  })
  return (
    <Card title="Experiments" extra={<button className="primary" onClick={() => setCreating(!creating)}>New experiment</button>}>
      {creating && (
        <div className="notice info" style={{ marginBottom: 12 }}>
          <div className="form-grid">
            <Field label="Store"><select value={f.store_id || storeId || ''} onChange={(e) => setF({ ...f, store_id: e.target.value })}>{stores.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Field>
            <Field label="Title"><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
            <Field label="Strategy"><select value={f.strategy_type} onChange={(e) => setF({ ...f, strategy_type: e.target.value })}>{meta?.strategy_types.map((s) => <option key={s} value={s}>{label(s)}</option>)}</select></Field>
            <Field label="Design"><select value={f.design} onChange={(e) => setF({ ...f, design: e.target.value })}>
              <option value="ab_test">A/B test (randomised)</option><option value="holdout">Holdout</option><option value="geo_split">Market / geo split</option><option value="before_after">Before / after (weak)</option></select></Field>
          </div>
          <div className="mt"><Field label="Hypothesis"><textarea value={f.hypothesis} onChange={(e) => setF({ ...f, hypothesis: e.target.value })} placeholder="If we …, then … will …, because …" /></Field></div>
          <div className="row mt"><button className="primary" onClick={() => create.mutate()} disabled={!f.title || !f.hypothesis}>Create</button><ErrorBox error={create.error} /></div>
        </div>
      )}
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? <Empty title="No experiments">Create one, or use "Plan as experiment" on an opportunity.</Empty> : (
        <table>
          <thead><tr><th>Experiment</th><th>Store</th><th>Strategy</th><th>Design</th><th>Stage</th><th>Dates</th><th>Evidence</th><th>Decision</th></tr></thead>
          <tbody>{q.data.map((e) => (
            <tr key={e.id} className="clickable" onClick={() => setOpen(e.id)}>
              <td>{e.title}</td><td>{e.is_demo ? <span className="badge demo">DEMO</span> : <span className="badge">{e.store_code}</span>}</td>
              <td>{label(e.strategy_type)}</td><td>{label(e.design)}</td>
              <td><small>{FLOW.indexOf(e.status) + 1 || '–'}/{FLOW.length}</small> <Status value={e.status} /></td>
              <td><small>{e.start_date ? `${date(e.start_date)} → ${date(e.end_date)}` : '—'}</small></td>
              <td>{e.evidence_strength ? <Status value={e.evidence_strength} /> : '—'}</td><td>{label(e.decision)}</td>
            </tr>))}
          </tbody>
        </table>)}
      <ExperimentDrawer id={open} onClose={() => setOpen(null)} />
    </Card>
  )
}

function ExperimentDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['experiment', id], enabled: id != null, queryFn: () => api.get<Exp>(`/experiments/${id}`) })
  const [f, setF] = useState<Record<string, string>>({})
  const refresh = () => { qc.invalidateQueries({ queryKey: ['experiment', id] }); qc.invalidateQueries({ queryKey: ['experiments'] }); qc.invalidateQueries({ queryKey: ['learning'] }) }
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {}
      const arms: Record<string, Record<string, number>> = JSON.parse(JSON.stringify(q.data?.results ?? {}))
      let armsChanged = false
      for (const [k, v] of Object.entries(f)) {
        if (k.startsWith('arm.')) {
          const [, arm, metric] = k.split('.')
          arms[arm] = { ...(arms[arm] ?? {}) }
          if (v === '') delete arms[arm][metric]
          else arms[arm][metric] = Number(v)
          armsChanged = true
        } else body[k] = v === '' ? null : k === 'costs' ? Number(v) : v
      }
      if (armsChanged) body.results = arms
      return api.patch<Exp>(`/experiments/${id}`, body)
    },
    onSuccess: () => { setF({}); refresh() },
  })
  const evaluate = useMutation({ mutationFn: () => api.post(`/experiments/${id}/evaluate`), onSuccess: refresh })
  const move = useMutation({ mutationFn: (to: string) => api.post(`/experiments/${id}/transition`, { to }), onSuccess: refresh })
  const e = q.data
  const v = (k: string, d: unknown) => (k in f ? f[k] : d == null ? '' : String(d))
  const set = (k: string) => (ev: { target: { value: string } }) => setF({ ...f, [k]: ev.target.value })
  const controlled = e && e.design !== 'before_after'
  const ev = (e?.evaluation ?? {}) as Record<string, unknown>
  return (
    <Drawer open={id != null} onClose={() => { setF({}); onClose() }} title={e ? <div><div className="row" style={{ marginBottom: 6 }}><span className="badge">{e.store_code}</span><Status value={e.status} />{e.evidence_strength && <Status value={e.evidence_strength} text={`Evidence: ${e.evidence_strength}`} />}</div><h2>{e.title}</h2></div> : 'Loading…'}>
      {e && (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            {FLOW.map((s, i) => <span key={s} className={`badge ${s === e.status ? 'info' : ''}`}>{i + 1}. {label(s)}</span>)}
          </div>
          <div className="row" style={{ marginBottom: 12 }}>
            {e.allowed_transitions.map((t) => <button key={t} className={t === 'abandoned' ? 'danger' : FLOW.indexOf(t) > FLOW.indexOf(e.status) ? 'primary' : ''} onClick={() => move.mutate(t)}>
              {FLOW.indexOf(t) > FLOW.indexOf(e.status) ? `Move to ${label(t)} →` : t === 'abandoned' ? 'Abandon' : `← Back to ${label(t)}`}</button>)}
          </div>
          <ErrorBox error={move.error ?? save.error ?? evaluate.error} />
          <div className="section-title">Hypothesis</div><p>{e.hypothesis}</p>
          <div className="form-grid">
            <Field label="Design"><select value={v('design', e.design)} onChange={set('design')}><option value="ab_test">A/B test</option><option value="holdout">Holdout</option><option value="geo_split">Market split</option><option value="before_after">Before / after</option></select></Field>
            <Field label="Baseline start"><input type="date" value={v('baseline_start', e.baseline_start)} onChange={set('baseline_start')} /></Field>
            <Field label="Baseline end"><input type="date" value={v('baseline_end', e.baseline_end)} onChange={set('baseline_end')} /></Field>
            <Field label="Test start"><input type="date" value={v('start_date', e.start_date)} onChange={set('start_date')} /></Field>
            <Field label="Test end"><input type="date" value={v('end_date', e.end_date)} onChange={set('end_date')} /></Field>
            <Field label="Cost of running it"><input type="number" value={v('costs', e.costs)} onChange={set('costs')} /></Field>
          </div>
          <div className="mt"><Field label="Methodology"><textarea value={v('methodology', e.methodology)} onChange={set('methodology')} /></Field></div>
          {controlled && (
            <>
              <div className="section-title">Results per arm (from your testing tool)</div>
              <table><thead><tr><th>Arm</th><th>Visitors</th><th>Orders</th><th>Revenue</th><th>Gross profit</th></tr></thead>
                <tbody>{['control', 'treatment'].map((arm) => (
                  <tr key={arm}><td>{label(arm)}</td>{['visitors', 'orders', 'revenue', 'gross_profit'].map((m) => (
                    <td key={m}><input type="number" style={{ width: 110 }} value={v(`arm.${arm}.${m}`, e.results?.[arm]?.[m])} onChange={set(`arm.${arm}.${m}`)} /></td>))}</tr>))}
                </tbody></table>
            </>
          )}
          <div className="row mt">
            <button onClick={() => save.mutate()} disabled={!Object.keys(f).length}>Save</button>
            <button className="primary" onClick={() => evaluate.mutate()} disabled={evaluate.isPending || Object.keys(f).length > 0}>Evaluate</button>
            {Object.keys(f).length > 0 && <small className="muted">Save changes before evaluating.</small>}
          </div>
          {Object.keys(ev).length > 0 && (
            <div className="notice info mt">
              <div className="row"><Status value={String(ev.evidence_strength)} text={`Evidence: ${ev.evidence_strength}`} /><Status value={String(ev.direction)} /></div>
              <p style={{ marginTop: 6 }}>{String(ev.interpretation ?? '')}</p>
              {ev.conversion ? <small>Conversion lift {signedPct((ev.conversion as Record<string, number>).relative_lift)}, p = {(ev.conversion as Record<string, number>).p_value.toFixed(3)} · sample needed per arm for a 10% lift: {String(ev.sample_needed_per_arm_for_10pct_lift ?? '—')}</small> : null}
              {ev.changes ? <table className="mt"><tbody>{Object.entries(ev.changes as Record<string, number | null>).map(([k, x]) => <tr key={k}><td>{label(k)}</td><td className="num">{signedPct(x)}</td></tr>)}</tbody></table> : null}
              {Array.isArray(ev.confounders) && <><div className="section-title">Confounders</div><ul className="tight">{(ev.confounders as string[]).map((c, i) => <li key={i}>{c}</li>)}</ul></>}
            </div>
          )}
          <div className="section-title">Conclusion</div>
          <div className="form-grid">
            <Field label="Decision"><select value={v('decision', e.decision)} onChange={set('decision')}><option value="">—</option>{['adopt', 'reject', 'iterate', 'extend'].map((d) => <option key={d} value={d}>{label(d)}</option>)}</select></Field>
          </div>
          <div className="mt"><Field label="Interpretation (becomes the learning record)"><textarea value={v('interpretation', e.interpretation)} onChange={set('interpretation')} /></Field></div>
          <div className="mt"><Field label="Limitations"><textarea value={v('limitations', e.limitations)} onChange={set('limitations')} /></Field></div>
          <div className="mt"><Field label="Follow-up actions"><textarea value={v('follow_up', e.follow_up)} onChange={set('follow_up')} /></Field></div>
          <div className="section-title">History</div>
          <ul className="tight">{e.history.map((h, i) => <li key={i}><small>{dateTime(h.at)}</small> {h.from ? `${label(h.from)} → ` : ''}{label(h.to)}{h.note ? ` — ${h.note}` : ''}</li>)}</ul>
        </>
      )}
    </Drawer>
  )
}

function Learning() {
  const { storeId } = useApp()
  const q = useQuery({ queryKey: ['learning', storeId], queryFn: () => api.get<{ id: number; strategy_type: string; finding: string; direction: string; evidence_strength: string; active: boolean; created_at: string }[]>(`/learning${qs({ store_id: storeId })}`) })
  return (
    <Card title="What we have learned" extra={<small>Only strong and moderate findings influence future recommendations</small>}>
      {q.isLoading ? <Loading /> : !q.data?.length ? <Empty title="No learning yet">Close an evaluated experiment to record what it showed.</Empty> : (
        <table><thead><tr><th>Strategy</th><th>Finding</th><th>Direction</th><th>Evidence</th><th>Used in scoring</th><th>Recorded</th></tr></thead>
          <tbody>{q.data.map((l) => (
            <tr key={l.id} style={{ opacity: l.active ? 1 : 0.5 }}><td>{label(l.strategy_type)}</td><td>{l.finding}</td><td><Status value={l.direction} /></td>
              <td><Status value={l.evidence_strength} /></td><td>{l.active && ['strong', 'moderate'].includes(l.evidence_strength) ? 'Yes' : 'No — context only'}</td><td><small>{date(l.created_at)}</small></td></tr>))}
          </tbody></table>)}
    </Card>
  )
}

type Action = { id: number; store_code: string; kind: string; title: string; status: string; current_values: Record<string, unknown>; proposed_values: Record<string, unknown>
  affected: Record<string, unknown>; required_permissions: string[]; risks: string[]; result: Record<string, unknown>; error: string | null; created_at: string; approved_at: string | null }

function Actions() {
  const { storeId, store, meta } = useApp()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['actions', storeId], queryFn: () => api.get<Action[]>(`/actions${qs({ store_id: storeId })}`) })
  const variants = useQuery({ queryKey: ['variants', storeId], enabled: !!store && !store.is_demo,
    queryFn: () => api.get<{ id: number; product_title: string; title: string; sku: string; price: number }[]>(`/stores/${storeId}/variants`) })
  const [vid, setVid] = useState('')
  const [price, setPrice] = useState('')
  const [confirmCode, setConfirmCode] = useState<Record<number, string>>({})
  const refresh = () => qc.invalidateQueries({ queryKey: ['actions'] })
  const create = useMutation({ mutationFn: () => {
    const v = variants.data!.find((x) => x.id === Number(vid))!
    return api.post('/actions', { store_id: storeId, kind: 'update_variant_price', payload: { variant_id: v.id, new_price: Number(price) }, title: `Price ${v.product_title} ${v.title ?? ''}: ${v.price} → ${price}` })
  }, onSuccess: refresh })
  const act = useMutation({ mutationFn: ({ id, op, body }: { id: number; op: string; body?: unknown }) => api.post(`/actions/${id}/${op}`, body ?? {}), onSuccess: refresh })
  return (
    <div className="stack">
      <div className="notice warning">
        <strong>Automated execution is not enabled.</strong> Proposals capture the exact change, current values, risks and required permissions, and need typed approval.
        Apply an approved change in Shopify admin, mark it as applied, and verify it after the next sync. {meta && !meta.write_actions_enabled && 'Write scopes are not requested in this phase.'}
      </div>
      {store && !store.is_demo && (
        <Card title={`Propose a price change — ${store.code}`}>
          <div className="row">
            <select value={vid} onChange={(e) => setVid(e.target.value)} style={{ maxWidth: 420 }}>
              <option value="">Choose variant…</option>
              {variants.data?.map((v) => <option key={v.id} value={v.id}>{v.product_title} — {v.title} ({v.sku ?? 'no SKU'}) · {money(v.price, store.currency, 2)}</option>)}
            </select>
            <input type="number" step="0.01" placeholder="New price" value={price} onChange={(e) => setPrice(e.target.value)} style={{ width: 120 }} />
            <button className="primary" disabled={!vid || !price || create.isPending} onClick={() => create.mutate()}>Create proposal</button>
          </div>
          <div className="mt"><ErrorBox error={create.error} /></div>
          {!variants.data?.length && <small className="muted">No synced variants yet.</small>}
        </Card>
      )}
      <Card title="Proposals">
        <ErrorBox error={act.error} />
        {!q.data?.length ? <small>No proposals.</small> : (
          <table><thead><tr><th>Store</th><th>Change</th><th>Current → proposed</th><th>Permissions</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>{q.data.map((a) => (
              <tr key={a.id}><td><span className="badge">{a.store_code}</span></td><td>{a.title}<div><small className="muted">{label(a.kind)} · {dateTime(a.created_at)}</small></div></td>
                <td className="mono" style={{ fontSize: 12 }}>{JSON.stringify(a.current_values)} → {JSON.stringify(a.proposed_values)}</td>
                <td className="mono" style={{ fontSize: 12 }}>{a.required_permissions.join(', ')}</td>
                <td><Status value={a.status} />{a.result?.verification ? <div><small>verification: {String(a.result.verification)}</small></div> : null}</td>
                <td>
                  {a.status === 'pending_approval' && <div className="row" style={{ gap: 4 }}>
                    <input placeholder={`Type ${a.store_code}`} style={{ width: 90 }} value={confirmCode[a.id] ?? ''} onChange={(e) => setConfirmCode({ ...confirmCode, [a.id]: e.target.value })} />
                    <button className="small primary" onClick={() => act.mutate({ id: a.id, op: 'approve', body: { confirm_store_code: confirmCode[a.id] ?? '' } })}>Approve</button>
                    <button className="small" onClick={() => { const r = prompt('Reason for rejecting?'); if (r) act.mutate({ id: a.id, op: 'reject', body: { reason: r } }) }}>Reject</button>
                  </div>}
                  {a.status === 'approved' && <div className="row" style={{ gap: 4 }}>
                    <button className="small" onClick={() => act.mutate({ id: a.id, op: 'execute' })} title="Not enabled in this phase">Execute via API</button>
                    <button className="small primary" onClick={() => act.mutate({ id: a.id, op: 'applied-manually' })}>Mark applied manually</button>
                  </div>}
                  {a.status === 'succeeded' && <button className="small" onClick={() => act.mutate({ id: a.id, op: 'verify' })}>Verify against store</button>}
                </td></tr>))}
            </tbody></table>)}
      </Card>
    </div>
  )
}

function Audit() {
  const { storeId } = useApp()
  const q = useQuery({ queryKey: ['audit', storeId], queryFn: () => api.get<{ id: number; at: string; actor: string; action: string; entity_type: string; entity_id: number | null; details: Record<string, unknown> }[]>(`/audit${qs({ store_id: storeId })}`) })
  return (
    <Card title="Audit log" extra={<small>Consequential actions and changes</small>}>
      {q.isLoading ? <Loading /> : (
        <div className="table-wrap" style={{ maxHeight: 600 }}>
          <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
            <tbody>{q.data?.map((a) => (
              <tr key={a.id}><td><small>{dateTime(a.at)}</small></td><td>{a.actor}</td><td className="mono">{a.action}</td><td>{a.entity_type}{a.entity_id ? ` #${a.entity_id}` : ''}</td>
                <td className="mono" style={{ fontSize: 11.5, maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis' }}>{JSON.stringify(a.details).slice(0, 220)}</td></tr>))}
            </tbody></table>
        </div>)}
    </Card>
  )
}
