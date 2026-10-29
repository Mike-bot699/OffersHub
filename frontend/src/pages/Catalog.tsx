import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { dateTime, money, num } from '../format'
import { NeedStore } from '../NeedStore'
import type { StoreRow } from '../state'
import { Card, Empty, ErrorBox, Loading, PageHead, Source, useSort } from '../ui'

type Item = {
  id: number; title: string; handle: string | null; product_type: string | null; url: string | null
  price_min: number | null; price_max: number | null; perf_tag: string | null; perf_label: string | null; perf_rank: number | null
  units_30d: number | null; revenue_30d: number | null; manual_tags: string[]; source: string
}
type Catalog = { products: Item[]; currency: string; tags_basis: string | null; tags_computed_at: string | null; min_ranked: number; rule: string }

const TAG_FILTERS = [
  { id: 'best_seller', label: '🔥 Best Seller' }, { id: 'top_seller', label: '⭐ Top Seller' }, { id: 'popular', label: 'Popular' },
  { id: 'free', label: 'Free' }, { id: 'unranked', label: 'Unranked' },
]
const BASIS: Record<string, string> = { eli_z_sales_report: 'Eli Z sales report (last 30 days)', orders: 'store orders (last 30 days)' }

export default function Products() {
  return <NeedStore title="Products">{(s) => <CatalogFor store={s} />}</NeedStore>
}

function CatalogFor({ store }: { store: StoreRow }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['catalog', store.id], queryFn: () => api.get<Catalog>(`/stores/${store.id}/catalog`) })
  const refresh = useMutation({ mutationFn: () => api.post(`/stores/${store.id}/product-tags/refresh`), onSuccess: () => qc.invalidateQueries({ queryKey: ['catalog', store.id] }) })
  const [tag, setTag] = useState<string | null>(null)
  const [manual, setManual] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const products = q.data?.products ?? []
  const manualTags = [...new Set(products.flatMap((p) => p.manual_tags))].sort()
  const shown = products.filter((p) => (!tag || (tag === 'unranked' ? !p.perf_tag : p.perf_tag === tag))
    && (!manual || p.manual_tags.includes(manual)) && (!search || p.title.toLowerCase().includes(search.toLowerCase())))
  const { sorted, th } = useSort(shown.map((p) => ({ ...p, _rank: p.perf_rank ?? 1e9 })), '_rank', false)
  return (
    <>
      <PageHead title="Products" desc="Live products only (active in the catalog), with performance tags from the last 30 days of sales and your own manual tags."
        actions={<button onClick={() => refresh.mutate()} disabled={refresh.isPending}>{refresh.isPending ? 'Recomputing…' : 'Recompute tags'}</button>} />
      <ErrorBox error={refresh.error} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : !products.length ? (
        <Card><Empty title="No live products">Sync the catalog from Eli Z in Settings → Stores.</Empty></Card>
      ) : (
        <Card>
          <div className="row" style={{ marginBottom: 10, justifyContent: 'space-between' }}>
            <div className="chips">
              <button className={!tag ? 'chip on' : 'chip'} onClick={() => setTag(null)}>All <span className="muted">{products.length}</span></button>
              {TAG_FILTERS.map((t) => (
                <button key={t.id} className={tag === t.id ? 'chip on' : 'chip'} onClick={() => setTag(tag === t.id ? null : t.id)}>
                  {t.label} <span className="muted">{products.filter((p) => (t.id === 'unranked' ? !p.perf_tag : p.perf_tag === t.id)).length}</span>
                </button>))}
              {manualTags.map((m) => (
                <button key={m} className={manual === m ? 'chip on' : 'chip'} onClick={() => setManual(manual === m ? null : m)}>
                  <span className="badge tag-manual">{m}</span> <span className="muted">{products.filter((p) => p.manual_tags.includes(m)).length}</span>
                </button>))}
            </div>
            <input placeholder="Search products" value={search} onChange={(e) => setSearch(e.target.value)} style={{ maxWidth: 220 }} />
          </div>
          <div className="table-wrap" style={{ maxHeight: 680 }}>
            <table>
              <thead><tr>{th('_rank', 'Rank')}{th('title', 'Product', false)}<th>Type</th><th className="num">Price</th><th>Performance</th>
                {th('units_30d', 'Units 30d')}{th('revenue_30d', 'Revenue 30d')}<th>Manual tags</th></tr></thead>
              <tbody>{sorted.map((p) => (
                <tr key={p.id}>
                  <td className="num">{p.perf_rank ?? '—'}</td>
                  <td style={{ minWidth: 220 }}>{p.url ? <a href={p.url} target="_blank" rel="noreferrer noopener">{p.title}</a> : p.title}</td>
                  <td><small>{p.product_type ?? '—'}</small></td>
                  <td className="num">{p.price_min == null ? '—' : p.price_min === p.price_max ? money(p.price_min, q.data!.currency) : `${money(p.price_min, q.data!.currency)}–${money(p.price_max, q.data!.currency)}`}</td>
                  <td>{p.perf_label ? <span className={`badge tag-${p.perf_tag}`}>{p.perf_label}</span> : <small className="muted">Unranked</small>}</td>
                  <td className="num">{num(p.units_30d)}</td>
                  <td className="num">{money(p.revenue_30d, q.data!.currency)}</td>
                  <td><ManualTags p={p} storeId={store.id} known={manualTags} /></td>
                </tr>))}
              </tbody>
            </table>
          </div>
          <Source>Tags from {BASIS[q.data!.tags_basis ?? ''] ?? 'no sales data yet'}{q.data!.tags_computed_at ? `, computed ${dateTime(q.data!.tags_computed_at)}` : ''}. {q.data!.rule} Minimum products with sales for bands: {q.data!.min_ranked} (Settings → Stores).</Source>
        </Card>
      )}
    </>
  )
}

function ManualTags({ p, storeId, known }: { p: Item; storeId: number; known: string[] }) {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const save = useMutation({ mutationFn: (tags: string[]) => api.put(`/products/${p.id}/tags`, { manual_tags: tags }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['catalog', storeId] }); setAdding(false); setText('') } })
  return (
    <div className="row" style={{ gap: 4 }}>
      {p.manual_tags.map((t) => (
        <span key={t} className="badge tag-manual">{t}
          <button className="tag-x" aria-label={`Remove ${t}`} onClick={() => save.mutate(p.manual_tags.filter((x) => x !== t))}>×</button></span>))}
      {adding ? (
        <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) save.mutate([...p.manual_tags, text.trim()]) }} className="row" style={{ gap: 4 }}>
          <input autoFocus list={`tags-${p.id}`} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Featured" style={{ width: 120, padding: '2px 6px' }} />
          <datalist id={`tags-${p.id}`}>{['Featured', ...known].map((k) => <option key={k} value={k} />)}</datalist>
          <button type="submit" disabled={save.isPending}>Add</button>
        </form>
      ) : <button className="ghost tag-add" onClick={() => setAdding(true)}>+ tag</button>}
      <ErrorBox error={save.error} />
    </div>
  )
}
