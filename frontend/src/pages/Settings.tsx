import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { dateTime, label, num } from '../format'
import { useApp } from '../state'
import { Card, ErrorBox, PageHead, Status, Tabs } from '../ui'
import { StoresSettings } from './StoreSettings'

type CsvReport = { kind: string; dry_run: boolean; rows_total: number; rows_imported: number; rows_skipped: number; issues: { row: number; issue: string }[] }

export default function Settings() {
  const { meta, store } = useApp()
  const qc = useQueryClient()
  const demo = useMutation({ mutationFn: () => api.post('/demo/reset'), onSuccess: () => qc.invalidateQueries() })
  const demoDel = useMutation({ mutationFn: () => api.del('/demo'), onSuccess: () => qc.invalidateQueries() })
  const backups = useQuery({ queryKey: ['backups'], queryFn: () => api.get<{ file: string; bytes: number; created: string }[]>('/admin/backups'), retry: false })
  const backup = useMutation({ mutationFn: () => api.post('/admin/backups'), onSuccess: () => backups.refetch() })
  const [tab, setTab] = useState('stores')
  const imports = useQuery({ queryKey: ['imports'], queryFn: () => api.get<{ id: number; kind: string; filename: string; status: string; rows_imported: number; rows_skipped: number; warning_count: number; created_at: string }[]>('/imports') })
  return (
    <>
      <PageHead title="Settings" desc="Stores and their Eli Z connection, data imports, backups and application configuration." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'stores', label: 'Stores' }, { id: 'data', label: 'Data & application' }]} />
      {tab === 'stores' && <StoresSettings />}
      {tab === 'data' && <>
      <div className="grid g2">
        <Card title="Data imports for the selected store">
          {!store ? <small>Select a store in the top bar to import costs or traffic for it.</small> : store.is_demo ? <small>Imports into the demo store are disabled.</small> : (
            <div className="stack">
              <CsvImport storeId={store.id} kind="costs" title="Unit costs (CSV)" help="Columns: sku or variant_id, unit_cost, optional currency and effective_from. Matched within this store only." />
              <CsvImport storeId={store.id} kind="traffic" title="Daily traffic (CSV)" help="Columns: day, sessions, optional sessions with cart additions / reached checkout / completed checkout, optional product handle. Shopify Analytics and GA4 exports work." />
            </div>
          )}
        </Card>
        <Card title="Application">
          <dl className="kv">
            <dt>Version</dt><dd>{meta?.version}</dd>
            <dt>Environment</dt><dd>{meta?.env}</dd>
            <dt>Database</dt><dd>{meta?.database}</dd>
            <dt>Authentication</dt><dd>{meta?.auth_mode === 'none' ? 'off (local use only)' : meta?.auth_mode}</dd>
            <dt>Credential encryption</dt><dd><Status value={meta?.encryption_configured ? 'success' : 'failed'} text={meta?.encryption_configured ? 'configured' : 'EGI_SECRET_KEY missing'} /></dd>
            <dt>Shopify API version</dt><dd>{meta?.shopify_api_version}</dd>
            <dt>AI commentary</dt><dd>{meta?.ai_available ? `on (${meta.ai_model})` : 'off — set EGI_ANTHROPIC_API_KEY'}</dd>
            <dt>Store write actions</dt><dd>{meta?.write_actions_enabled ? 'enabled' : 'disabled (approval + manual apply only)'}</dd>
          </dl>
          <small className="muted">Configuration is read from environment variables / backend/.env. See README.</small>
        </Card>
        <Card title="Backups">
          <p className="secondary">Online SQLite backup with an integrity check. The encryption key is not included — keep it separately.</p>
          <button onClick={() => backup.mutate()} disabled={backup.isPending}>Create backup now</button>
          <ErrorBox error={backup.error ?? backups.error} />
          <table className="mt"><tbody>{backups.data?.map((b) => <tr key={b.file}><td className="mono">{b.file}</td><td className="num">{num(b.bytes / 1024)} KB</td><td><small>{dateTime(b.created)}</small></td></tr>)}</tbody></table>
        </Card>
        <Card title="Demo data">
          <p className="secondary">A synthetic store (code DEMO) to explore every feature without credentials. It is labelled everywhere, excluded from real totals and cannot be connected.</p>
          <div className="row">
            <button onClick={() => demo.mutate()} disabled={demo.isPending}>{demo.isPending ? 'Building…' : 'Create / reset demo store'}</button>
            <button className="danger" onClick={() => demoDel.mutate()}>Delete demo store</button>
          </div>
          <ErrorBox error={demo.error ?? demoDel.error} />
        </Card>
      </div>
      <Card title="Import history" className="mt">
        <table><thead><tr><th>When</th><th>Kind</th><th>File</th><th>Status</th><th className="num">Imported</th><th className="num">Skipped</th><th className="num">Warnings</th></tr></thead>
          <tbody>{imports.data?.map((b) => <tr key={b.id}><td><small>{dateTime(b.created_at)}</small></td><td>{label(b.kind)}</td><td>{b.filename}</td><td><Status value={b.status === 'completed' ? 'success' : b.status} text={b.status} /></td><td className="num">{b.rows_imported}</td><td className="num">{b.rows_skipped}</td><td className="num">{b.warning_count}</td></tr>)}</tbody></table>
      </Card>
      </>}
    </>
  )
}

function CsvImport({ storeId, kind, title, help }: { storeId: number; kind: string; title: string; help: string }) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const m = useMutation({
    mutationFn: (dry: boolean) => { const fd = new FormData(); fd.append('file', file!); fd.append('dry_run', String(dry)); return api.upload<CsvReport>(`/stores/${storeId}/imports/${kind}`, fd) },
    onSuccess: (r) => { if (!r.dry_run) qc.invalidateQueries() },
  })
  const r = m.data
  return (
    <div>
      <h3>{title}</h3>
      <small className="muted">{help}</small>
      <div className="row mt">
        <input type="file" accept=".csv,.tsv,.txt" onChange={(e) => { setFile(e.target.files?.[0] ?? null); m.reset() }} />
        <button onClick={() => m.mutate(true)} disabled={!file || m.isPending}>Preview</button>
        <button className="primary" onClick={() => m.mutate(false)} disabled={!file || m.isPending || !r?.dry_run}>Import</button>
      </div>
      <div className="mt"><ErrorBox error={m.error} /></div>
      {r && <div className={`notice ${r.dry_run ? 'info' : 'good'} mt`}>{r.dry_run ? 'Preview: ' : 'Imported: '}{r.rows_imported} of {r.rows_total} rows usable, {r.rows_skipped} skipped.
        {r.issues.length > 0 && <ul className="tight">{r.issues.slice(0, 12).map((i, k) => <li key={k}><small>row {i.row}: {i.issue}</small></li>)}</ul>}</div>}
    </div>
  )
}
