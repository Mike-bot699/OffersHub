import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { ago, label } from '../format'
import { useApp, type StoreRow } from '../state'
import { Card, DemoBadge, ErrorBox, Field, PageHead, Status } from '../ui'

const SETTINGS: [string, string, 'money' | 'pct'][] = [
  ['free_shipping_threshold', 'Free-shipping threshold', 'money'],
  ['avg_shipping_cost_per_order', 'Shipping cost to you per order', 'money'],
  ['fulfillment_cost_per_order', 'Pick & pack cost per order', 'money'],
  ['payment_fee_pct', 'Payment fee (%)', 'pct'],
  ['payment_fee_fixed', 'Payment fee fixed per order', 'money'],
  ['default_gift_cost', 'Default free-gift cost', 'money'],
  ['min_margin_pct', 'Minimum acceptable margin (%)', 'pct'],
  ['default_cogs_pct', 'Fallback COGS % (assumption, only where no unit cost)', 'pct'],
  ['new_customer_discount_pct', 'Standing new-customer discount (%)', 'pct'],
  ['returning_customer_discount_pct', 'Standing returning-customer discount (%)', 'pct'],
]

export default function Stores() {
  const { stores, set } = useApp()
  const [adding, setAdding] = useState(false)
  return (
    <>
      <PageHead title="Stores" desc="Each store keeps its own data, settings, objectives and recommendations."
        actions={<button className="primary" onClick={() => setAdding(!adding)}>Add store</button>} />
      {adding && <AddStore onDone={() => setAdding(false)} />}
      <div className="stack">
        {stores.map((s) => <StoreCard key={s.id} s={s} onFocus={() => set({ storeId: s.id })} />)}
      </div>
    </>
  )
}

function AddStore({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ code: '', name: '', currency: 'USD', timezone: 'UTC' })
  const m = useMutation({ mutationFn: () => api.post('/stores', f), onSuccess: () => { qc.invalidateQueries({ queryKey: ['stores'] }); onDone() } })
  return (
    <Card title="New store" className="mt">
      <div className="form-grid">
        <Field label="Code (e.g. WB)"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
        <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Currency"><input value={f.currency} maxLength={3} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Timezone (IANA)"><input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
      </div>
      <small className="muted">Currency and timezone are replaced by the platform's values once Shopify is connected.</small>
      <div className="row mt"><button className="primary" onClick={() => m.mutate()} disabled={!f.code || !f.name}>Create</button><ErrorBox error={m.error} /></div>
    </Card>
  )
}

function StoreCard({ s, onFocus }: { s: StoreRow; onFocus: () => void }) {
  const { meta } = useApp()
  const qc = useQueryClient()
  const [edit, setEdit] = useState(false)
  const [cs, setCs] = useState<Record<string, string>>({})
  const [objs, setObjs] = useState<string[]>(s.objectives.map((o) => o.objective))
  const [info, setInfo] = useState({ name: s.name, status: s.status, notes: s.notes ?? '' })
  const save = useMutation({
    mutationFn: async () => {
      const settings: Record<string, number | null> = {}
      for (const [k, v] of Object.entries(cs)) {
        const kind = SETTINGS.find((x) => x[0] === k)?.[2]
        settings[k] = v === '' ? null : kind === 'pct' ? Number(v) / 100 : Number(v)
      }
      await api.patch(`/stores/${s.id}`, { ...info, commercial_settings: settings })
      await api.put(`/stores/${s.id}/objectives`, objs.map((o) => ({ objective: o })))
    },
    onSuccess: () => { qc.invalidateQueries(); setEdit(false); setCs({}) },
  })
  const val = (k: string, kind: string) => {
    if (k in cs) return cs[k]
    const v = s.commercial_settings?.[k]
    return v == null ? '' : kind === 'pct' ? String(+(v * 100).toFixed(2)) : String(v)
  }
  return (
    <Card title={<span className="row" style={{ gap: 8 }}>{s.code} <span className="secondary" style={{ fontWeight: 400 }}>{s.name}</span>{s.is_demo && <DemoBadge />}{s.status === 'inactive' && <span className="badge">inactive</span>}</span>}
      extra={<div className="row"><button onClick={onFocus}>Focus</button><button onClick={() => setEdit(!edit)}>{edit ? 'Close' : 'Edit settings & objectives'}</button></div>}>
      <div className="row" style={{ gap: 16 }}>
        <span><small className="muted">Platform</small> {label(s.platform)}</span>
        <span><small className="muted">Currency</small> {s.currency}</span>
        <span><small className="muted">Timezone</small> {s.timezone}</span>
        <span><small className="muted">Integration</small> <Status value={s.integration.status} /></span>
        {Object.entries(s.freshness).map(([r, f]) => <span key={r}><small className="muted">{label(r)}</small> <Status value={f.status} text={`${label(f.status)}${f.last_success_at ? ` · ${ago(f.last_success_at)}` : ''}`} /></span>)}
      </div>
      <div className="row mt" style={{ gap: 6 }}>
        <small className="muted">Objectives:</small>
        {s.objectives.length ? s.objectives.map((o) => <span key={o.id} className="badge info">{o.priority}. {label(o.objective)}</span>) : <small>none set — recommendations are ranked without objective alignment</small>}
      </div>
      {edit && (
        <div className="mt">
          <div className="section-title">Store</div>
          <div className="form-grid">
            <Field label="Name"><input value={info.name} onChange={(e) => setInfo({ ...info, name: e.target.value })} /></Field>
            <Field label="Status"><select value={info.status} onChange={(e) => setInfo({ ...info, status: e.target.value })}><option value="active">Active</option><option value="inactive">Inactive</option></select></Field>
            <Field label="Notes"><input value={info.notes} onChange={(e) => setInfo({ ...info, notes: e.target.value })} /></Field>
          </div>
          <div className="section-title">Objectives (in priority order)</div>
          <div className="row">
            {objs.map((o, i) => <span key={o} className="badge info">{i + 1}. {label(o)} <button className="small ghost" onClick={() => setObjs(objs.filter((x) => x !== o))}>✕</button></span>)}
            <select value="" onChange={(e) => e.target.value && setObjs([...objs, e.target.value])}>
              <option value="">Add objective…</option>
              {meta?.objectives.filter((o) => !objs.includes(o)).map((o) => <option key={o} value={o}>{label(o)}</option>)}
            </select>
          </div>
          <div className="section-title">Commercial settings &amp; cost assumptions</div>
          <div className="form-grid">
            {SETTINGS.map(([k, l, kind]) => (
              <Field key={k} label={kind === 'money' ? `${l} (${s.currency})` : l}>
                <input type="number" step="any" value={val(k, kind)} onChange={(e) => setCs({ ...cs, [k]: e.target.value })} placeholder="not set" />
              </Field>))}
          </div>
          <small className="muted">Blank means unknown: analyses that need the value say so instead of assuming it. The fallback COGS % is labelled as an assumption wherever it is used.</small>
          <div className="row mt"><button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button><ErrorBox error={save.error} /></div>
        </div>
      )}
    </Card>
  )
}
