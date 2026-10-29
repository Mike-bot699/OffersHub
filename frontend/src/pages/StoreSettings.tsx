import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { dateTime } from '../format'
import { useApp } from '../state'
import { Card, DemoBadge, ErrorBox, Field, Loading, Status } from '../ui'

type EliZ = { status: string; shop_id: string | null; has_key: boolean; key_hint: string | null; last_verified_at: string | null
  last_error: string | null; provides: string[]; not_available: Record<string, string> }
type StoreSettings = { id: number; name: string; code: string; logo: string | null; product_host: string | null; currency: string
  is_demo: boolean; tag_min_ranked_products: number | null; elizeta: EliZ }

export function StoresSettings() {
  const { meta } = useApp()
  const q = useQuery({ queryKey: ['store-settings'], queryFn: () => api.get<StoreSettings[]>('/settings/stores') })
  const [adding, setAdding] = useState(false)
  if (q.isLoading) return <Loading />
  if (q.error) return <ErrorBox error={q.error} />
  const real = (q.data ?? []).filter((s) => !s.is_demo)
  return (
    <div className="stack">
      {meta && !meta.encryption_configured && (
        <div className="notice critical">The server has no encryption key (EGI_SECRET_KEY), so API keys cannot be saved. Generate one with
          <span className="mono"> python -m app.cli generate-key</span> and add it to <span className="mono">backend/.env</span>.</div>
      )}
      <div className="notice info"><small>Data comes from the <strong>Eli Z</strong> platform: each store is identified by its numeric shop id and read with an API key.
        The key is encrypted on the server and never shown again — only its last four characters. Leave the key field blank to keep the saved one.</small></div>
      {real.map((s) => <StoreCard key={s.id} s={s} />)}
      {import.meta.env.VITE_PREVIEW === '1' && !real.length && (
        <>
          <small className="muted">Preview only — how a store's card looks once added (saving is disabled in the preview):</small>
          <StoreCard s={{ id: 0, name: 'Example store', code: 'EX', logo: null, product_host: null, currency: 'USD', is_demo: false, tag_min_ranked_products: null,
            elizeta: { status: 'not_configured', shop_id: null, has_key: false, key_hint: null, last_verified_at: null, last_error: null, provides: ['products', 'sales_reports'],
              not_available: { orders: 'Eli Z has no confirmed orders endpoint yet.', discounts: 'Eli Z has no confirmed discount-codes endpoint yet.' } } }} />
        </>
      )}
      {adding ? <AddStore onDone={() => setAdding(false)} /> : <div><button className="primary" onClick={() => setAdding(true)}>Add store</button></div>}
      {(q.data ?? []).filter((s) => s.is_demo).map((s) => (
        <Card key={s.id}><div className="row"><DemoBadge /><strong>{s.name}</strong><small className="muted">Synthetic store: it cannot be connected to a platform.</small></div></Card>))}
    </div>
  )
}

function StoreCard({ s }: { s: StoreSettings }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ name: s.name, code: s.code, product_host: s.product_host ?? '', elizeta_shop_id: s.elizeta.shop_id ?? '',
    elizeta_api_key: '', tag_min_ranked_products: s.tag_min_ranked_products == null ? '' : String(s.tag_min_ranked_products) })
  const done = () => { qc.invalidateQueries({ queryKey: ['store-settings'] }); qc.invalidateQueries({ queryKey: ['stores'] }) }
  const save = useMutation({
    mutationFn: (extra: Record<string, unknown> = {}) => api.put(`/stores/${s.id}/settings`, {
      name: f.name, code: f.code, product_host: f.product_host, elizeta_shop_id: f.elizeta_shop_id,
      ...(f.elizeta_api_key ? { elizeta_api_key: f.elizeta_api_key } : {}),
      tag_min_ranked_products: f.tag_min_ranked_products === '' ? null : Number(f.tag_min_ranked_products), ...extra }),
    onSuccess: () => { setF({ ...f, elizeta_api_key: '' }); done() },
  })
  const icon = useMutation({ mutationFn: (logo: string) => api.put(`/stores/${s.id}/settings`, { logo }), onSuccess: done })
  const verify = useMutation({ mutationFn: () => api.post(`/stores/${s.id}/elizeta/verify`), onSettled: done })
  const sync = useMutation({ mutationFn: () => api.post<{ finished?: boolean; status?: string; stats?: Record<string, Record<string, unknown>> }>(`/stores/${s.id}/elizeta/sync`), onSuccess: () => setTimeout(() => { done(); qc.invalidateQueries({ queryKey: ['catalog'] }) }, 1500) })
  const disconnect = useMutation({ mutationFn: () => api.post(`/stores/${s.id}/elizeta/disconnect`), onSuccess: done })
  const ez = s.elizeta
  const pickIcon = (file: File) => { const r = new FileReader(); r.onload = () => icon.mutate(String(r.result)); r.readAsDataURL(file) }
  return (
    <Card title={<span className="row" style={{ gap: 8 }}>{s.logo ? <img src={s.logo} alt="" width={28} height={28} style={{ borderRadius: 6 }} /> : <span className="store-letter">{s.code[0]}</span>}{s.name} <span className="mono muted">· {s.code}</span></span>}
      extra={<Status value={ez.status === 'not_configured' ? 'never_synced' : ez.status} text={ez.status === 'not_configured' ? 'Eli Z not set' : `Eli Z ${ez.status}`} />}>
      <div className="form-grid">
        <Field label="Store name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Code (campaign ID prefix)"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
      </div>
      <div className="mt">
        <span className="field">Store icon <small className="muted">(shown in the store list; the letter is used if empty)</small></span>
        <div className="row" style={{ marginTop: 4 }}>
          {s.logo ? <img src={s.logo} alt="" width={40} height={40} style={{ borderRadius: 8, border: '1px solid var(--border)' }} /> : <span className="store-letter big">{s.code[0]}</span>}
          <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={(e) => e.target.files?.[0] && pickIcon(e.target.files[0])} />
          {s.logo && <button onClick={() => icon.mutate('')}>Remove</button>}
        </div>
      </div>
      <div className="mt"><Field label="Product host (storefront domain — product links are built from it)"><input value={f.product_host} placeholder="thelittlelearnerstoys.com" onChange={(e) => setF({ ...f, product_host: e.target.value })} /></Field></div>
      <div className="form-grid mt">
        <Field label="Eli Z shop id"><input inputMode="numeric" value={f.elizeta_shop_id} placeholder="26" onChange={(e) => setF({ ...f, elizeta_shop_id: e.target.value })} /></Field>
        <Field label={`Eli Z API key${ez.has_key ? ` (set: ${ez.key_hint} — blank keeps it)` : ''}`}>
          <input type="password" autoComplete="new-password" value={f.elizeta_api_key} placeholder={ez.key_hint ?? 'Paste the API key'} onChange={(e) => setF({ ...f, elizeta_api_key: e.target.value })} /></Field>
        <Field label="Min. products with sales for performance tags"><input type="number" min={0} value={f.tag_min_ranked_products} placeholder="10" onChange={(e) => setF({ ...f, tag_min_ranked_products: e.target.value })} /></Field>
      </div>
      <div className="row mt">
        <button className="primary" onClick={() => save.mutate({})} disabled={save.isPending}>Save</button>
        <button onClick={() => verify.mutate()} disabled={!ez.has_key || verify.isPending}>{verify.isPending ? 'Testing…' : 'Test connection'}</button>
        <button onClick={() => sync.mutate()} disabled={!ez.has_key || sync.isPending}>Sync products & sales</button>
        {ez.has_key && <button className="danger" onClick={() => confirm(`Delete the saved Eli Z API key of ${s.code}?`) && disconnect.mutate()}>Disconnect</button>}
      </div>
      <ErrorBox error={save.error ?? icon.error ?? verify.error ?? sync.error ?? disconnect.error} />
      {sync.data && (sync.data.finished ? (
        <div className={`notice ${sync.data.status === 'success' ? 'info' : 'critical'} mt`}><small>
          Sync {sync.data.status}. {Object.entries(sync.data.stats ?? {}).map(([k, v]) => `${k}: ${v.error ? `error — ${String(v.error)}` : Object.entries(v).map(([a, b]) => `${a} ${typeof b === 'object' ? JSON.stringify(b) : b}`).join(', ')}`).join(' · ')}
        </small></div>
      ) : <div className="notice info mt"><small>Sync started. Products and tags update in a minute.</small></div>)}
      {ez.last_error && <div className="error-box mt">{ez.last_error}</div>}
      <small className="muted" style={{ display: 'block', marginTop: 8 }}>
        Last verified: {ez.last_verified_at ? dateTime(ez.last_verified_at) : 'never'}. Eli Z provides: catalog and product sales.
        Not available yet: {Object.keys(ez.not_available).join(' and ')} — {Object.values(ez.not_available)[0]}
      </small>
    </Card>
  )
}

function AddStore({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ code: '', name: '', currency: 'USD', timezone: 'UTC' })
  const m = useMutation({ mutationFn: () => api.post('/stores', { ...f, code: f.code.toUpperCase() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['store-settings'] }); qc.invalidateQueries({ queryKey: ['stores'] }); onDone() } })
  return (
    <Card title="New store" extra={<button className="ghost" onClick={onDone}>Close</button>}>
      <div className="form-grid">
        <Field label="Code (e.g. MAU)"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
        <Field label="Store name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Currency"><input value={f.currency} maxLength={3} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Time zone"><input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
      </div>
      <small className="muted" style={{ display: 'block', marginTop: 6 }}>Then set its Eli Z shop id and API key in its card.</small>
      <div className="row mt"><button className="primary" disabled={!f.code || !f.name || m.isPending} onClick={() => m.mutate()}>Add store</button></div>
      <ErrorBox error={m.error} />
    </Card>
  )
}
