import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { ago, dateTime, label } from '../format'
import { NeedStore } from '../NeedStore'
import { useApp, type StoreRow } from '../state'
import { Card, ErrorBox, Field, Loading, PageHead, Status } from '../ui'

type Integ = {
  id?: number; status: string; auth_method: string | null; shop_domain: string | null; api_version: string | null; has_credentials: boolean
  granted_scopes: string[]; capabilities: string[]; missing_capabilities: string[]
  scope_plan: { scope: string; required: boolean; why: string; granted: boolean }[]
  shop_info: { name?: string; probe?: Record<string, string> }; last_verified_at: string | null; last_error: string | null
  sync_enabled: boolean; sync_interval_minutes: number; initial_history_days: number
}
type Jobs = { jobs: { id: number; trigger: string; status: string; started_at: string; finished_at: string | null; stats: Record<string, Record<string, unknown>>; warnings: string[]; error: string | null }[]
  freshness: Record<string, { status: string; last_success_at: string | null; records: number }>; running: boolean }

const SCOPES_FALLBACK = [
  ['read_products', true, 'Products, variants, prices and availability.'], ['read_orders', true, 'Orders, line items, discounts, refunds.'],
  ['read_all_orders', false, 'History older than 60 days.'], ['read_discounts', false, 'Verify documented offers and coupon usage.'],
  ['read_inventory', false, 'Unit cost (COGS) and inventory tracking.'], ['read_customers', false, 'Pseudonymous customer IDs for repeat-purchase analysis.'],
] as const

export default function Integrations() {
  return <NeedStore title="Integrations">{(s) => <IntegrationFor store={s} />}</NeedStore>
}

function IntegrationFor({ store }: { store: StoreRow }) {
  const { meta } = useApp()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['integration', store.id], queryFn: () => api.get<Integ>(`/stores/${store.id}/integration`) })
  const jobs = useQuery({ queryKey: ['jobs', store.id], queryFn: () => api.get<Jobs>(`/stores/${store.id}/sync-jobs`),
    refetchInterval: (qq) => (qq.state.data?.running ? 3000 : false) })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['integration', store.id] }); qc.invalidateQueries({ queryKey: ['jobs', store.id] }); qc.invalidateQueries({ queryKey: ['stores'] }) }
  const verify = useMutation({ mutationFn: () => api.post(`/stores/${store.id}/integration/verify`), onSettled: refresh })
  const sync = useMutation({ mutationFn: (full: boolean) => api.post(`/stores/${store.id}/sync`, { full }), onSuccess: () => setTimeout(refresh, 500) })
  const settings = useMutation({ mutationFn: (b: Partial<Integ>) => api.patch(`/stores/${store.id}/integration`, b), onSuccess: refresh })
  const [disc, setDisc] = useState({ open: false, code: '', purge: false })
  const disconnect = useMutation({ mutationFn: () => api.post<{ next_steps: string }>(`/stores/${store.id}/integration/disconnect`, { confirm_store_code: disc.code, purge_data: disc.purge }), onSuccess: refresh })

  if (store.is_demo) return (<><PageHead title="Integrations" /><Card><p>The demo store is synthetic and cannot be connected to a platform.</p></Card></>)
  if (q.isLoading) return <Loading />
  const i = q.data!
  const connected = i.status === 'connected' || i.status === 'error' || i.status === 'configured'
  return (
    <>
      <PageHead title={`Integrations — ${store.code}`} desc="Shopify Admin GraphQL API, read-only. Credentials are encrypted at rest and never sent back to the browser." />
      {!meta?.encryption_configured && <div className="notice critical" style={{ marginBottom: 12 }}>EGI_SECRET_KEY is not configured on the server, so credentials cannot be stored. See README → Configuration.</div>}
      <div className="grid g2">
        <Card title="Shopify connection" extra={<Status value={i.status} />}>
          {i.has_credentials ? (
            <>
              <dl className="kv">
                <dt>Shop</dt><dd>{i.shop_info?.name ?? '—'} <span className="mono">{i.shop_domain}</span></dd>
                <dt>Auth</dt><dd>{label(i.auth_method)}</dd>
                <dt>API version</dt><dd>{i.api_version}</dd>
                <dt>Last verified</dt><dd>{dateTime(i.last_verified_at)}</dd>
                {i.shop_info?.probe && <><dt>Query check</dt><dd>{Object.entries(i.shop_info.probe).map(([k, v]) => <div key={k}><small>{k}: {v}</small></div>)}</dd></>}
              </dl>
              {i.last_error && <div className="error-box mt">{i.last_error}</div>}
              <div className="row mt">
                <button onClick={() => verify.mutate()} disabled={verify.isPending}>{verify.isPending ? 'Checking…' : 'Test connection'}</button>
                <button className="danger" onClick={() => setDisc({ ...disc, open: !disc.open })}>Disconnect…</button>
              </div>
              <ErrorBox error={verify.error} />
              {disc.open && (
                <div className="notice critical mt">
                  <p>Deletes the stored credentials from this application. Synced data is kept unless you also purge it.</p>
                  <div className="row">
                    <input placeholder={`Type ${store.code} to confirm`} value={disc.code} onChange={(e) => setDisc({ ...disc, code: e.target.value })} />
                    <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={disc.purge} onChange={(e) => setDisc({ ...disc, purge: e.target.checked })} /> also delete synced Shopify data</label>
                    <button className="danger" onClick={() => disconnect.mutate()}>Disconnect</button>
                  </div>
                  <ErrorBox error={disconnect.error} />
                </div>
              )}
              {disconnect.data && <div className="notice info mt">{disconnect.data.next_steps}</div>}
            </>
          ) : <ConnectForm store={store} onDone={refresh} />}
        </Card>
        <Card title="Permissions (access scopes)">
          <table>
            <thead><tr><th>Scope</th><th>Why</th><th>Granted</th></tr></thead>
            <tbody>{(i.scope_plan?.length ? i.scope_plan.map((p) => [p.scope, p.required, p.why, p.granted] as const) : SCOPES_FALLBACK.map(([a, b, c]) => [a, b, c, false] as const)).map(([s, req, why, granted]) => (
              <tr key={s}><td className="mono">{s}{req ? '' : <small className="muted"> optional</small>}</td><td><small>{why}</small></td>
                <td>{i.granted_scopes?.length ? <Status value={granted ? 'success' : req ? 'failed' : 'warning'} text={granted ? 'yes' : 'no'} /> : '—'}</td></tr>))}
            </tbody>
          </table>
          <small className="muted">No write scopes are requested. Customer data: only the Shopify customer ID is stored — no names, emails, addresses or phone numbers.</small>
        </Card>
      </div>
      {connected && i.has_credentials && (
        <Card title="Synchronisation" className="mt" extra={
          <div className="row">
            <button className="primary" onClick={() => sync.mutate(false)} disabled={jobs.data?.running || sync.isPending}>{jobs.data?.running ? 'Syncing…' : 'Sync now'}</button>
            <button onClick={() => sync.mutate(true)} disabled={jobs.data?.running} title="Ignore watermarks and re-read the full history window">Full re-sync</button>
          </div>}>
          <ErrorBox error={sync.error} />
          <div className="row" style={{ gap: 18, marginBottom: 12 }}>
            <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={i.sync_enabled} onChange={(e) => settings.mutate({ sync_enabled: e.target.checked })} /> Automatic sync</label>
            <label className="row" style={{ gap: 4 }}>every
              <select value={i.sync_interval_minutes} onChange={(e) => settings.mutate({ sync_interval_minutes: Number(e.target.value) })}>
                {[60, 180, 360, 720, 1440].map((m) => <option key={m} value={m}>{m < 1440 ? `${m / 60} h` : '24 h'}</option>)}
              </select></label>
            <label className="row" style={{ gap: 4 }}>first sync history
              <select value={i.initial_history_days} onChange={(e) => settings.mutate({ initial_history_days: Number(e.target.value) })}>
                {[90, 365, 730, 1095].map((d) => <option key={d} value={d}>{d} days</option>)}
              </select></label>
          </div>
          <div className="row" style={{ gap: 16 }}>
            {jobs.data && Object.entries(jobs.data.freshness).map(([r, f]) => <span key={r}><small className="muted">{label(r)}</small> <Status value={f.status} text={`${label(f.status)} · ${f.records} records · ${ago(f.last_success_at)}`} /></span>)}
          </div>
          <table className="mt">
            <thead><tr><th>Started</th><th>Trigger</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>{jobs.data?.jobs.map((j) => (
              <tr key={j.id}><td><small>{dateTime(j.started_at)}</small></td><td>{j.trigger}</td><td><Status value={j.status} /></td>
                <td><small>{Object.entries(j.stats).map(([r, s]) => `${r}: ${s.error ? `ERROR ${s.error}` : s.skipped ? 'skipped' : `${s.fetched ?? 0} fetched (${s.mode ?? ''})`}`).join(' · ')}</small>
                  {j.error && <div className="error-box">{j.error}</div>}
                  {j.warnings?.map((w, k) => <div key={k}><small className="delta-down">▲ {w}</small></div>)}</td></tr>))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  )
}

export function ConnectForm({ store, onDone }: { store: StoreRow; onDone: () => void }) {
  const [f, setF] = useState({ shop_domain: store.shop_domain ?? '', auth_method: 'client_credentials', access_token: '', client_id: '', client_secret: '' })
  const m = useMutation({ mutationFn: () => api.put(`/stores/${store.id}/integration`, f), onSettled: onDone })
  return (
    <>
      <p className="secondary">Choose how this store's app was created:</p>
      <div className="form-grid">
        <Field label="Shop domain"><input placeholder="your-store.myshopify.com" value={f.shop_domain} onChange={(e) => setF({ ...f, shop_domain: e.target.value })} /></Field>
        <Field label="App type"><select value={f.auth_method} onChange={(e) => setF({ ...f, auth_method: e.target.value })}>
          <option value="client_credentials">Dev Dashboard app (client ID + secret)</option>
          <option value="admin_token">Legacy custom app (Admin API token)</option></select></Field>
      </div>
      {f.auth_method === 'client_credentials' ? (
        <div className="form-grid mt">
          <Field label="Client ID"><input value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} autoComplete="off" /></Field>
          <Field label="Client secret"><input type="password" value={f.client_secret} onChange={(e) => setF({ ...f, client_secret: e.target.value })} autoComplete="new-password" /></Field>
        </div>
      ) : (
        <div className="mt"><Field label="Admin API access token (shpat_…)"><input type="password" value={f.access_token} onChange={(e) => setF({ ...f, access_token: e.target.value })} autoComplete="new-password" /></Field></div>
      )}
      <small className="muted" style={{ display: 'block', marginTop: 8 }}>
        Client credentials work only when the app and the store belong to the same Shopify organisation; tokens last 24 h and are refreshed automatically.
        The connection is tested against the live API before it is marked connected. See docs/SHOPIFY_SETUP.md.
      </small>
      <div className="row mt"><button className="primary" onClick={() => m.mutate()} disabled={m.isPending || !f.shop_domain}>{m.isPending ? 'Connecting…' : 'Connect & verify'}</button></div>
      <div className="mt"><ErrorBox error={m.error} /></div>
    </>
  )
}
