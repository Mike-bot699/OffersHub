// Live docs (Offers Plan → Docs): saved selections shared by a secret link that always shows the
// current plan, plus the stores' extra activities the docs list.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, qs } from '../api'
import { date, dateTime } from '../format'
import { useApp } from '../state'
import { Card, Drawer, Empty, ErrorBox, Field, Loading, Source } from '../ui'

type Doc = {
  id: number; token: string; name: string; tags: string[]; store_ids: number[]
  range_mode: 'fixed' | 'next_days' | 'this_month' | 'next_month'; start_date: string | null; end_date: string | null; days: number | null
  statuses: string[]; sections: Record<string, boolean>; focus: { perf_tags?: string[]; manual_tags?: string[]; limit?: number }
  custom_top: string | null; custom_bottom: string | null; range: [string, string]; updated_at: string; changes_since: string
}
type Activity = { id: number; store_id: number; name: string; description: string | null; price: string | null; code: string | null
  start_date: string | null; end_date: string | null; active: boolean }

const RANGES = [['fixed', 'Fixed dates'], ['next_days', 'Next N days'], ['this_month', 'This month'], ['next_month', 'Next month']] as const
const STATUSES = [['live', 'Live'], ['scheduled', 'Scheduled'], ['in_progress', 'In Progress'], ['paused', 'Paused'], ['completed', 'Completed'], ['cancelled', 'Cancelled'], ['not_set', 'Not set']] as const
const SECTIONS = [['focus', 'Products to focus'], ['events', 'Events'], ['product_offers', 'Product offers'], ['extra_activities', 'Extra activities'],
  ['bonus', 'Bonus'], ['forecast', 'Forecast'], ['extra_details', 'Extra details'], ['changes', 'Recent changes box']] as const
const PERF = [['best_seller', 'Best Seller'], ['top_seller', 'Top Seller'], ['popular', 'Popular'], ['free', 'Free']] as const

export const docUrl = (token: string) => `${window.location.origin}/d/${token}`

export function DocsTab() {
  const qc = useQueryClient()
  const { stores } = useApp()
  const [edit, setEdit] = useState<Doc | 'new' | null>(null)
  const [copied, setCopied] = useState<number | null>(null)
  const q = useQuery({ queryKey: ['docs'], queryFn: () => api.get<Doc[]>('/offer-docs') })
  const relink = useMutation({ mutationFn: (id: number) => api.post(`/offer-docs/${id}/new-link`), onSuccess: () => qc.invalidateQueries({ queryKey: ['docs'] }) })
  const copy = async (d: Doc) => {
    try { await navigator.clipboard.writeText(docUrl(d.token)); setCopied(d.id); setTimeout(() => setCopied(null), 2000) } catch { /* clipboard blocked */ }
  }
  const storeCodes = (ids: number[]) => (ids.length ? stores.filter((s) => ids.includes(s.id)).map((s) => s.code).join(', ') : 'All stores')
  return (
    <div className="stack">
      <Card title="Live docs" extra={<button className="primary" onClick={() => setEdit('new')}>New doc</button>}>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? (
          <Empty title="No docs yet">Create a doc to share the offers of a period with a link that always shows the latest plan.</Empty>
        ) : (
          <table>
            <thead><tr><th>Doc</th><th>Stores</th><th>Period</th><th>Link</th><th /></tr></thead>
            <tbody>{q.data.map((d) => (
              <tr key={d.id}>
                <td><strong>{d.name}</strong>{d.tags.length > 0 && <div><small className="muted">{d.tags.join(' · ')}</small></div>}</td>
                <td>{storeCodes(d.store_ids)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{date(d.range[0])} → {date(d.range[1])}
                  {d.range_mode !== 'fixed' && <div><small className="muted">{RANGES.find(([k]) => k === d.range_mode)?.[1]}{d.range_mode === 'next_days' ? ` (${d.days})` : ''}, moves with today</small></div>}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <a href={docUrl(d.token)} target="_blank" rel="noopener noreferrer">Open ↗</a>{' · '}
                  <button className="ghost small" onClick={() => copy(d)}>{copied === d.id ? 'Copied' : 'Copy link'}</button>
                </td>
                <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                  <button className="small" onClick={() => setEdit(d)}>Edit</button>{' '}
                  <button className="small" title="The current link stops working; share the new one"
                    onClick={() => { if (window.confirm(`Replace the link of "${d.name}"? People with the current link will lose access.`)) relink.mutate(d.id) }}>New link</button>
                </td>
              </tr>))}
            </tbody>
          </table>
        )}
        <Source>Anyone with a doc's link can read it, without signing in. Docs are composed again on every visit, so they always show the current Offers Plan. "New link" replaces the link: the old one stops working.</Source>
      </Card>
      <ExtraActivities />
      {edit && <DocEditor doc={edit === 'new' ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  )
}

function toggle<T>(list: T[], v: T) { return list.includes(v) ? list.filter((x) => x !== v) : [...list, v] }

function DocEditor({ doc, onClose }: { doc: Doc | null; onClose: () => void }) {
  const qc = useQueryClient()
  const { stores, storeId } = useApp()
  const real = stores.filter((s) => !s.is_demo)
  const [f, setF] = useState(() => ({
    name: doc?.name ?? '', tags: doc?.tags.join(', ') ?? '', store_ids: doc?.store_ids ?? (storeId ? [storeId] : []),
    range_mode: doc?.range_mode ?? 'fixed', start_date: doc?.start_date ?? '', end_date: doc?.end_date ?? '', days: String(doc?.days ?? 30),
    statuses: doc?.statuses ?? ['live', 'scheduled', 'in_progress'],
    sections: { focus: true, events: true, product_offers: true, extra_activities: true, bonus: true, forecast: true, extra_details: true, changes: true, ...(doc?.sections ?? {}) } as Record<string, boolean>,
    perf_tags: doc?.focus?.perf_tags ?? ['best_seller', 'top_seller'], manual_tags: (doc?.focus?.manual_tags ?? []).join(', '),
    limit: String(doc?.focus?.limit ?? 15), custom_top: doc?.custom_top ?? '', custom_bottom: doc?.custom_bottom ?? '',
  }))
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v })
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name.trim(), tags: f.tags.split(',').map((t) => t.trim()).filter(Boolean), store_ids: f.store_ids,
        range_mode: f.range_mode, start_date: f.range_mode === 'fixed' ? f.start_date || null : null,
        end_date: f.range_mode === 'fixed' ? f.end_date || null : null, days: f.range_mode === 'next_days' ? Number(f.days) || 30 : null,
        statuses: f.statuses, sections: f.sections,
        focus: { perf_tags: f.perf_tags, manual_tags: f.manual_tags.split(',').map((t) => t.trim()).filter(Boolean), limit: Number(f.limit) || 15 },
        custom_top: f.custom_top || null, custom_bottom: f.custom_bottom || null,
      }
      return doc ? api.put(`/offer-docs/${doc.id}`, body) : api.post('/offer-docs', body)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['docs'] }); onClose() },
  })
  const reset = useMutation({ mutationFn: () => api.post(`/offer-docs/${doc!.id}/reset-changes`), onSuccess: () => qc.invalidateQueries({ queryKey: ['docs'] }) })
  const del = useMutation({ mutationFn: () => api.del(`/offer-docs/${doc!.id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['docs'] }); onClose() } })
  const chk = (on: boolean, label: string, onChange: () => void) => (
    <label key={label} className="row" style={{ gap: 6 }}><input type="checkbox" checked={on} onChange={onChange} /> <span>{label}</span></label>
  )
  return (
    <Drawer open onClose={onClose} title={<h2>{doc ? 'Edit doc' : 'New doc'}</h2>}>
      <div className="form-grid">
        <Field label="Doc name"><input value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="e.g. MAU · November offers" /></Field>
        <Field label="Doc tags (comma separated)"><input value={f.tags} onChange={(e) => set('tags', e.target.value)} placeholder="e.g. BFCM, Executors" /></Field>
      </div>
      <div className="section-title">Stores</div>
      <div className="row" style={{ gap: 14, flexWrap: 'wrap' }}>
        {real.map((s) => chk(f.store_ids.includes(s.id), `${s.code} · ${s.name}`, () => set('store_ids', toggle(f.store_ids, s.id))))}
      </div>
      <small className="muted">None selected = every store.</small>
      <div className="section-title">Time range</div>
      <div className="form-grid">
        <Field label="Range"><select value={f.range_mode} onChange={(e) => set('range_mode', e.target.value)}>{RANGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        {f.range_mode === 'fixed' && <>
          <Field label="From"><input type="date" value={f.start_date} onChange={(e) => set('start_date', e.target.value)} /></Field>
          <Field label="To"><input type="date" value={f.end_date} onChange={(e) => set('end_date', e.target.value)} /></Field>
        </>}
        {f.range_mode === 'next_days' && <Field label="Days from today"><input type="number" min={1} max={366} value={f.days} onChange={(e) => set('days', e.target.value)} /></Field>}
      </div>
      <div className="section-title">Offer statuses</div>
      <div className="row" style={{ gap: 14, flexWrap: 'wrap' }}>{STATUSES.map(([k, l]) => chk(f.statuses.includes(k), l, () => set('statuses', toggle(f.statuses, k))))}</div>
      <div className="section-title">Sections</div>
      <div className="row" style={{ gap: 14, flexWrap: 'wrap' }}>{SECTIONS.map(([k, l]) => chk(!!f.sections[k], l, () => set('sections', { ...f.sections, [k]: !f.sections[k] })))}</div>
      {doc && f.sections.changes && (
        <div className="row mt" style={{ gap: 8 }}>
          <small className="muted">Recent changes box: offer edits since {dateTime(doc.changes_since)}.</small>
          <button type="button" className="small" disabled={reset.isPending}
            onClick={() => { if (window.confirm('Start the changes box again from now? Edits made so far will no longer show as updates.')) reset.mutate() }}>
            {reset.isSuccess ? 'Reset done' : 'Reset changes box'}</button>
        </div>
      )}
      {f.sections.focus && <>
        <div className="section-title">Products to focus</div>
        <div className="row" style={{ gap: 14, flexWrap: 'wrap' }}>{PERF.map(([k, l]) => chk(f.perf_tags.includes(k), l, () => set('perf_tags', toggle(f.perf_tags, k))))}</div>
        <div className="form-grid mt">
          <Field label="Manual tags (comma separated)"><input value={f.manual_tags} onChange={(e) => set('manual_tags', e.target.value)} placeholder="e.g. Q4 Focus" /></Field>
          <Field label="Max products"><input type="number" min={1} max={100} value={f.limit} onChange={(e) => set('limit', e.target.value)} /></Field>
        </div>
        <small className="muted">Products with any of these performance tags or manual tags (set in Products), recalculated at every visit.</small>
      </>}
      <div className="section-title">Custom text</div>
      <Field label="Top (text or pasted HTML; empty = hidden)"><textarea rows={4} value={f.custom_top} onChange={(e) => set('custom_top', e.target.value)} /></Field>
      <div className="mt"><Field label="Bottom (text or pasted HTML; empty = hidden)"><textarea rows={4} value={f.custom_bottom} onChange={(e) => set('custom_bottom', e.target.value)} /></Field></div>
      <small className="muted">Pasted HTML keeps formatting, links and images; scripts are removed.</small>
      <div className="row mt" style={{ justifyContent: 'space-between' }}>
        <button className="primary" disabled={!f.name.trim() || save.isPending} onClick={() => save.mutate()}>{doc ? 'Save doc' : 'Create doc'}</button>
        {doc && <button className="danger" onClick={() => { if (window.confirm(`Delete "${doc.name}"? Its link stops working.`)) del.mutate() }}>Delete doc</button>}
      </div>
      <ErrorBox error={save.error ?? del.error} />
    </Drawer>
  )
}

function ExtraActivities() {
  const qc = useQueryClient()
  const { stores, storeId } = useApp()
  const [edit, setEdit] = useState<Partial<Activity> | null>(null)
  const q = useQuery({ queryKey: ['extra-activities', storeId], queryFn: () => api.get<Activity[]>(`/extra-activities${qs({ store_id: storeId })}`) })
  const save = useMutation({
    mutationFn: (a: Partial<Activity>) => {
      const body = { store_id: a.store_id, name: a.name, description: a.description || null, price: a.price || null, code: a.code || null,
        start_date: a.start_date || null, end_date: a.end_date || null, active: a.active ?? true }
      return a.id ? api.put(`/extra-activities/${a.id}`, body) : api.post('/extra-activities', body)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['extra-activities'] }); setEdit(null) },
  })
  const del = useMutation({ mutationFn: (id: number) => api.del(`/extra-activities/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['extra-activities'] }); setEdit(null) } })
  const code = (id: number) => stores.find((s) => s.id === id)?.code ?? ''
  const real = stores.filter((s) => !s.is_demo)
  return (
    <Card title="Extra activities" extra={<button onClick={() => setEdit({ store_id: storeId ?? real[0]?.id, active: true })}>Add activity</button>}>
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? (
        <Empty title="No extra activities">Gift bag, warranty, free-gift popup, golden ticket… Docs list the ones active in their period.</Empty>
      ) : (
        <table><thead><tr><th>Store</th><th>Activity</th><th>What the customer gets</th><th>Price / value</th><th>Active</th></tr></thead>
          <tbody>{q.data.map((a) => (
            <tr key={a.id} className="clickable" onClick={() => setEdit(a)}>
              <td><span className="badge">{code(a.store_id)}</span></td><td><strong>{a.name}</strong></td>
              <td><small>{a.description ?? '—'}{a.code && <> · code <b>{a.code}</b></>}</small></td><td>{a.price ?? '—'}</td>
              <td><small>{!a.active ? 'Off' : a.start_date || a.end_date ? `${a.start_date ? date(a.start_date) : '…'} → ${a.end_date ? date(a.end_date) : '…'}` : 'Always'}</small></td>
            </tr>))}
          </tbody></table>
      )}
      {edit && (
        <Drawer open onClose={() => setEdit(null)} title={<h2>{edit.id ? 'Edit activity' : 'New extra activity'}</h2>}>
          <div className="form-grid">
            <Field label="Store"><select value={edit.store_id ?? ''} onChange={(e) => setEdit({ ...edit, store_id: Number(e.target.value) })}>{real.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Field>
            <Field label="Name"><input value={edit.name ?? ''} onChange={(e) => setEdit({ ...edit, name: e.target.value })} placeholder="e.g. Extended Warranty" /></Field>
            <Field label="Price / value (USD)"><input value={edit.price ?? ''} onChange={(e) => setEdit({ ...edit, price: e.target.value })} placeholder="e.g. $8.95" /></Field>
            <Field label="Code (optional)"><input value={edit.code ?? ''} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
            <Field label="From (empty = always)"><input type="date" value={edit.start_date ?? ''} onChange={(e) => setEdit({ ...edit, start_date: e.target.value })} /></Field>
            <Field label="To (empty = always)"><input type="date" value={edit.end_date ?? ''} onChange={(e) => setEdit({ ...edit, end_date: e.target.value })} /></Field>
          </div>
          <div className="mt"><Field label="What the customer gets"><textarea rows={3} value={edit.description ?? ''} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field></div>
          <label className="row mt" style={{ gap: 6 }}><input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active</label>
          <div className="row mt" style={{ justifyContent: 'space-between' }}>
            <button className="primary" disabled={!edit.name?.trim() || !edit.store_id || save.isPending} onClick={() => save.mutate(edit)}>Save</button>
            {edit.id && <button className="danger" onClick={() => del.mutate(edit.id!)}>Delete</button>}
          </div>
          <ErrorBox error={save.error ?? del.error} />
        </Drawer>
      )}
    </Card>
  )
}
