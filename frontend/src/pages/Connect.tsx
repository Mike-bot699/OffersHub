import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { ago } from '../format'
import { useApp, type StoreRow } from '../state'
import { Card, Empty, ErrorBox, Field, PageHead, Status } from '../ui'
import { ConnectForm } from './Integrations'

const SCOPES: [string, string, string][] = [
  ['read_products', 'Required', 'Products, variants, prices'],
  ['read_orders', 'Required', 'Orders, line items, discounts used, refunds'],
  ['read_all_orders', 'Recommended', 'Order history older than 60 days'],
  ['read_discounts', 'Recommended', 'Discount codes and their status — powers the technical status of offers'],
  ['read_inventory', 'Recommended', 'Unit cost (cost per item) for margin'],
  ['read_customers', 'Optional', 'Customer ID only, for new vs returning'],
]

export default function Connect() {
  const { stores, set, meta } = useApp()
  const nav = useNavigate()
  const real = stores.filter((s) => !s.is_demo)
  const [open, setOpen] = useState<number | 'new' | null>(real.length ? null : 'new')
  const manage = (s: StoreRow) => { set({ storeId: s.id }); nav('/integrations') }
  return (
    <>
      <PageHead title="Store connection" desc="Connect each Shopify store with its API credentials. Access is read-only: OffersHub never changes prices, discounts or settings in your store." />
      {meta && !meta.encryption_configured && (
        <div className="notice critical" style={{ marginBottom: 12 }}>
          The server has no encryption key (EGI_SECRET_KEY), so credentials cannot be saved yet. Generate one with
          <span className="mono"> python -m app.cli generate-key</span> and add it to <span className="mono">backend/.env</span> (see README → Configuration).
        </div>
      )}
      <div className="grid g2" style={{ alignItems: 'start' }}>
        <div className="stack">
          <Card title="Your stores" extra={<button className="primary" onClick={() => setOpen('new')}>Add store</button>}>
            {!real.length ? <Empty title="No store yet">Add your first store, then paste its API credentials.</Empty> : (
              <table>
                <thead><tr><th>Store</th><th>Shop domain</th><th>Connection</th><th>Last data</th><th /></tr></thead>
                <tbody>{real.map((s) => {
                  const st = s.integration?.status ?? 'not_configured'
                  const last = Object.values(s.freshness ?? {}).map((f) => f.last_success_at).filter(Boolean).sort().pop() ?? null
                  return (
                    <tr key={s.id}>
                      <td><strong>{s.code}</strong><div><small className="secondary">{s.name}</small></div></td>
                      <td className="mono"><small>{s.integration?.shop_domain ?? '—'}</small></td>
                      <td><Status value={st === 'not_configured' ? 'never_synced' : st} text={st === 'not_configured' ? 'Not connected' : undefined} /></td>
                      <td><small>{last ? ago(last) : '—'}</small></td>
                      <td style={{ textAlign: 'right' }}>
                        {st === 'not_configured'
                          ? <button className="primary" onClick={() => setOpen(s.id)}>Connect</button>
                          : <button onClick={() => manage(s)}>Manage & sync</button>}
                      </td>
                    </tr>)
                })}</tbody>
              </table>)}
          </Card>
          {open === 'new' && <AddStore onCreated={(id) => setOpen(id)} onCancel={real.length ? () => setOpen(null) : undefined} />}
          {open === 'new' && (
            <Card title="Step 2 — paste the API credentials" className="muted-card">
              <small className="secondary">Available after step 1. You will enter:</small>
              <div className="form-grid mt">
                <Field label="Shop domain"><input disabled placeholder="your-store.myshopify.com" /></Field>
                <Field label="App type"><select disabled><option>Dev Dashboard app (client ID + secret)</option></select></Field>
                <Field label="Client ID"><input disabled /></Field>
                <Field label="Client secret"><input disabled type="password" /></Field>
              </div>
              <small className="muted" style={{ display: 'block', marginTop: 6 }}>Or, for an existing custom app, the Admin API access token (shpat_…).</small>
            </Card>)}
          {typeof open === 'number' && (() => {
            const s = real.find((x) => x.id === open)
            if (!s) return null
            return (
              <Card title={`Connect ${s.code} — enter the API credentials`} extra={<button className="ghost" onClick={() => setOpen(null)}>Close</button>}>
                <ConnectForm store={s} onDone={() => { setOpen(null) }} />
              </Card>)
          })()}
        </div>
        <div className="stack">
          <Card title="Where to find the API credentials">
            <h3 style={{ fontSize: 14 }}>Option A — Dev Dashboard app <span className="badge info">recommended</span></h3>
            <ol className="tight">
              <li>Open the Shopify <strong>Dev Dashboard</strong> and create an app in the same organisation as the store.</li>
              <li>Under <em>Admin API access scopes</em>, select the read scopes below, then install the app on the store.</li>
              <li>Copy the <strong>Client ID</strong> and <strong>Client secret</strong> from the app settings.</li>
              <li>Here: <em>Connect</em> → <em>Dev Dashboard app</em> → paste the <span className="mono">your-store.myshopify.com</span> domain, Client ID and secret.</li>
            </ol>
            <h3 style={{ fontSize: 14, marginTop: 12 }}>Option B — existing custom app</h3>
            <ol className="tight">
              <li>Shopify admin → <em>Settings → Apps and sales channels → Develop apps</em> → your app → <em>API credentials</em>.</li>
              <li>Copy the <strong>Admin API access token</strong> (starts with <span className="mono">shpat_</span>; it is shown only once).</li>
              <li>Here: <em>Connect</em> → <em>Legacy custom app</em> → paste the domain and the token.</li>
            </ol>
            <small className="muted">Stores created after 1 January 2026 can no longer create new custom apps in the admin: use option A.</small>
          </Card>
          <Card title="Permissions to grant (read-only)">
            <table>
              <tbody>{SCOPES.map(([s, lvl, why]) => (
                <tr key={s}><td className="mono"><small>{s}</small></td><td><span className={`badge ${lvl === 'Required' ? 'info' : ''}`}>{lvl}</span></td><td><small>{why}</small></td></tr>))}
              </tbody>
            </table>
            <small className="muted">No <span className="mono">write_*</span> scope is ever requested.</small>
          </Card>
          <Card title="How your credentials are handled">
            <ul className="tight">
              <li>Encrypted on the server before being saved; never sent back to the browser or written to logs.</li>
              <li>The connection is tested against the live Shopify API before the store is marked connected.</li>
              <li>You can disconnect at any time (Manage & sync → Disconnect), which deletes the stored credentials. To revoke access at the source, uninstall the app in Shopify.</li>
            </ul>
          </Card>
        </div>
      </div>
    </>
  )
}

function AddStore({ onCreated, onCancel }: { onCreated: (id: number) => void; onCancel?: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState({ code: '', name: '', currency: 'EUR', timezone: 'Europe/Rome' })
  const m = useMutation({ mutationFn: () => api.post<{ id: number }>('/stores', { ...f, code: f.code.toUpperCase() }),
    onSuccess: async (s) => { await qc.invalidateQueries({ queryKey: ['stores'] }); onCreated(s.id) } })
  return (
    <Card title="Step 1 — add the store" extra={onCancel && <button className="ghost" onClick={onCancel}>Close</button>}>
      <div className="form-grid">
        <Field label="Short code (e.g. AAA)"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Store name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Currency"><input value={f.currency} maxLength={3} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Time zone"><input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
      </div>
      <small className="muted" style={{ display: 'block', marginTop: 6 }}>Next step: paste the API credentials for this store.</small>
      <div className="row mt"><button className="primary" disabled={!f.code || !f.name || m.isPending} onClick={() => m.mutate()}>Add store & continue</button></div>
      <ErrorBox error={m.error} />
    </Card>
  )
}
