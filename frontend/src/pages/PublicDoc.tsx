// A shared live doc (/d/<token>): read-only, no sign-in, composed by the server on every visit.
import { useEffect, useState } from 'react'
import DOMPurify from 'dompurify'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../api'
import { BannerPair } from '../banner'

type Tag = { name: string; bg?: string; fg?: string }
type DocEvent = {
  id: number; store_code: string; store_name: string; name: string; tags: Tag[]; when: string; start_date: string
  display: string | null; display_text: string | null; banner: boolean; emails: { subject: string; preview: string }[]
  offer: string | null; codes: string[]; threshold: string | null; activation: string | null
  bonus: { free_shipping: string | null; giveaway_product: string | null; free_gift: string | null } | null
  products: string | null; channels: { key: string; name: string }[]
  forecast: { orders: number | null; uses: number | null; aov: string | null; estimate: boolean; basis?: string; missing?: string } | null
  extra_details: string | null
  changes: { date: string; items: { field: string; text: string }[] } | null
}
export type DocData = {
  name: string; tags: Tag[]; start_date: string; end_date: string; stores: { code: string; name: string }[]
  custom_top: string | null; custom_bottom: string | null; focus_rule: string
  focus: { id: number; store_code: string; name: string; sku: string; url: string | null; perf: string | null; tags: string[] }[]
  events: DocEvent[]
  product_offers: { store_code: string; name: string; sku: string | null; url: string | null; offer: string | null }[]
  extra_activities: { store_code: string; name: string; description: string | null; price: string | null; code: string | null }[]
}

const CHANNEL_COLOR: Record<string, string> = { facebook: '#1877f2', email: '#000', google: '#d93025' }
const PERF_COLOR: Record<string, [string, string]> = {
  'Best Seller': ['#fff1e8', '#a33a00'], 'Top Seller': ['#fff8e1', '#7a5a00'], Popular: ['#e8f0fe', '#1d4ed8'], Free: ['#eef8ee', '#1a7a1a'],
}

const day = (s: string) => new Date(`${s}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

function TagPill({ t, small }: { t: Tag; small?: boolean }) {
  return <span className={`pd-tag ${small ? 'sm' : ''}`} style={t.bg ? { background: t.bg, color: t.fg } : undefined}>{t.name}</span>
}

function Custom({ html }: { html: string | null }) {
  if (!html?.trim()) return null
  // Pasted text or HTML, cleaned: formatting, links and images stay; scripts and handlers are removed.
  const clean = DOMPurify.sanitize(/<[a-z][\s\S]*>/i.test(html) ? html : html.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!).replace(/\n/g, '<br>'))
  return <div className="pd-custom" dangerouslySetInnerHTML={{ __html: clean }} />
}

/** The offer line with its codes in bold. */
function OfferText({ text, codes }: { text: string; codes: string[] }) {
  if (!codes.length) return <>{text}</>
  const parts = text.split(new RegExp(`\\b(${codes.map((c) => c.replace(/[^A-Z0-9]/gi, '')).join('|')})\\b`))
  return <>{parts.map((p, i) => (codes.includes(p) ? <b key={i}>{p}</b> : <span key={i}>{p}</span>))}</>
}

function Field({ label, children, full }: { label: string; children: React.ReactNode; full?: boolean }) {
  return <div className={`pd-f ${full ? 'full' : ''}`}><b>{label}:</b><span>{children}</span></div>
}

function ThresholdInfo({ amount }: { amount: string }) {
  return (
    <span className="pd-i" tabIndex={0}>i
      <span className="pd-tip">The code applies only when the cart reaches the minimum ({amount.replace('+', '')}); below it the discount is not applied.</span>
    </span>
  )
}

function EventCard({ e, token }: { e: DocEvent; token: string }) {
  const banner = useQuery({ queryKey: ['public-banner', token, e.id], enabled: e.banner, staleTime: 10 * 60_000,
    queryFn: () => api.get<{ kind: 'html' | 'image' | null; content: string | null; filename: string | null }>(`/public/docs/${token}/banner/${e.id}`) })
  const b = e.bonus
  return (
    <section className="pd-offer">
      <div className="pd-when">{e.when}</div>
      <h3>{e.name}</h3>
      {e.tags.length > 0 && <div className="pd-tags">{e.tags.map((t) => <TagPill key={t.name} t={t} />)}</div>}
      {e.changes && (
        <div className="pd-changes"><b>⚠️ Updated {day(e.changes.date).replace(/ \d{4}$/, '')}:</b>{' '}
          {e.changes.items.map((c) => c.text).join(' · ')}
        </div>
      )}
      {e.banner && banner.data?.content && <BannerPair banner={banner.data} />}
      {e.emails.length > 0 && (
        <div className="pd-email">{e.emails.map((m, i) => (
          <div key={i} className="pd-mail">
            {e.emails.length > 1 && <div className="pd-mailn">Email {i + 1}</div>}
            {m.subject && <div><span className="pd-k2">Subject</span>{m.subject}</div>}
            {m.preview && <div><span className="pd-k2">Preview text</span>{m.preview}</div>}
          </div>))}
        </div>
      )}
      <div className="pd-fields">
        {e.offer && <Field label="Offer" full><OfferText text={e.offer} codes={e.codes} /></Field>}
        {e.display_text && <Field label="Displayed">{e.display_text}</Field>}
        {e.codes.length > 0 && <Field label="Coupon code">{e.codes.map((c, i) => <span key={c}>{i > 0 && ' / '}<b>{c}</b></span>)}</Field>}
        {e.threshold && <Field label="Threshold">{e.threshold} <ThresholdInfo amount={e.threshold} /></Field>}
        {e.activation && <Field label="Activation">{e.activation}</Field>}
        {b?.free_shipping && <Field label="Free shipping">{b.free_shipping}</Field>}
        {b?.giveaway_product && <Field label="Give-away product">{b.giveaway_product}</Field>}
        {b?.free_gift && <Field label="Free gift">{b.free_gift}</Field>}
        {e.products && <Field label="Products">{e.products}</Field>}
        {e.channels.length > 0 && <Field label="Advertising">{e.channels.map((c) => <span key={c.key} className="pd-tag sm pd-ch" style={{ background: CHANNEL_COLOR[c.key], color: '#fff' }}>{c.name}</span>)}</Field>}
      </div>
      {e.forecast?.missing && <div className="pd-box"><b>Forecast</b><span className="pd-meta" style={{ margin: 0 }}>{e.forecast.missing}</span></div>}
      {e.forecast && !e.forecast.missing && (
        <div className="pd-box"><b>Forecast</b>
          {e.forecast.estimate && <span className="pd-est" title="From the same event last year: its codes' orders, scaled to this year's days when daily data exist, and its AOV. Replaced by a forecast typed on the offer.">estimate · {e.forecast.basis}</span>}
          {e.forecast.orders != null && <span><em>{e.forecast.orders}</em> orders</span>}
          {e.forecast.uses != null && <span><em>{e.forecast.uses}</em> code uses</span>}
          {e.forecast.aov && <span>AOV <em>{e.forecast.aov}</em></span>}
        </div>
      )}
      {e.extra_details?.trim() && <div className="pd-box"><b>Extra details:</b><span style={{ whiteSpace: 'pre-line' }}>{e.extra_details}</span></div>}
    </section>
  )
}

const SECTION_INFO: Record<string, [string, string]> = {
  events: ['Events', 'The store-wide and collection offers in this period: dates, offer, codes, where they are shown and the creatives.'],
  focus: ['Products to focus', 'The products to push in banners, emails and ads: best performers of the last 30 days plus the ones tagged for this period.'],
  products: ['Product offers', 'Offers attached to single products (bundles, add-ons, gifts), shown on their product pages.'],
  extras: ['Extra activities', 'Always-on extras the customer can add or receive at checkout.'],
}

function SectionHead({ id }: { id: keyof typeof SECTION_INFO }) {
  const [title, desc] = SECTION_INFO[id]
  return <div className="pd-sec" id={`sec-${id}`}><h2>{title}</h2><p>{desc}</p></div>
}

const tocDate = (s: string) => new Date(`${s}T00:00:00Z`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' })

function Toc({ d }: { d: DocData }) {
  const go = (id: string) => (ev: React.MouseEvent) => { ev.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
  const items: [string, string, boolean][] = [['events', 'Events', d.events.length > 0], ['focus', 'Products to focus', d.focus.length > 0],
    ['products', 'Product offers', d.product_offers.length > 0], ['extras', 'Extra activities', d.extra_activities.length > 0]]
  return (
    <nav className="pd-toc" aria-label="Contents">
      <div className="pd-toc-t">Contents</div>
      {items.filter(([, , on]) => on).map(([id, label]) => (
        <div key={id}>
          <a href={`#sec-${id}`} onClick={go(`sec-${id}`)}>{label}</a>
          {id === 'events' && <div className="pd-toc-sub">{d.events.map((e) => <a key={e.id} href={`#ev-${e.id}`} onClick={go(`ev-${e.id}`)}><span className="pd-toc-d">{tocDate(e.start_date)}</span><span>{e.name}</span></a>)}</div>}
        </div>
      ))}
    </nav>
  )
}

export function DocBody({ d, token }: { d: DocData; token: string }) {
  const multi = d.stores.length > 1
  let lastStore = ''
  return (
    <div className="pd-layout">
      <Toc d={d} />
      <div className="pd-page">
        <div className="pd-head">
          <div className="pd-eyebrow">Offers in plan</div>
          <h1>{d.name}</h1>
          <div className="pd-meta">{d.stores.map((s) => s.name).join(', ')} · {day(d.start_date)} to {day(d.end_date)} · this page always shows the latest version</div>
          {d.tags.length > 0 && <div className="pd-tags">{d.tags.map((t) => <TagPill key={t.name} t={t} />)}</div>}
        </div>
        <Custom html={d.custom_top} />
        {d.events.length > 0 && <>
          <SectionHead id="events" />
          {d.events.map((e) => {
            const head = e.store_code !== lastStore ? <div className="pd-store">{e.store_code} · {e.store_name}</div> : null
            lastStore = e.store_code
            return <div key={e.id} id={`ev-${e.id}`}>{head}<EventCard e={e} token={token} /></div>
          })}
        </>}
        {d.focus.length > 0 && <>
          <SectionHead id="focus" />
          {d.focus_rule && <div className="pd-meta">{d.focus_rule} · recalculated each time the doc is opened</div>}
          <table className="pd-table pd-focus"><thead><tr>{multi && <th>Store</th>}<th>Product</th><th>SKU</th><th>Performance</th><th>Extra tags</th></tr></thead>
            <tbody>{d.focus.map((p) => (
              <tr key={p.id}>
                {multi && <td data-label="Store">{p.store_code}</td>}
                <td data-label="Product" className="pd-main">{p.url ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.name}</a> : p.name}</td>
                <td data-label="SKU" className="pd-mono">{p.sku}</td>
                <td data-label="Performance">{p.perf && <span className="pd-tag sm" style={{ background: PERF_COLOR[p.perf]?.[0], color: PERF_COLOR[p.perf]?.[1] }}>{p.perf === 'Best Seller' ? '🔥 ' : ''}{p.perf}</span>}</td>
                <td data-label="Extra tags">{p.tags.map((t) => <span key={t} className="pd-tag sm" style={{ background: '#000', color: '#fff', marginRight: 4 }}>{t}</span>)}</td>
              </tr>))}
            </tbody></table>
        </>}
        {d.product_offers.length > 0 && <>
          <SectionHead id="products" />
          <table className="pd-table"><thead><tr>{multi && <th>Store</th>}<th>Name</th><th>SKU</th><th>Dedicated offer</th></tr></thead>
            <tbody>{d.product_offers.map((p, i) => (
              <tr key={i}>{multi && <td data-label="Store">{p.store_code}</td>}
                <td data-label="Name" className="pd-main">{p.url ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.name}</a> : p.name}</td>
                <td data-label="SKU" className="pd-mono">{p.sku ?? '-'}</td><td data-label="Dedicated offer">{p.offer ?? '-'}</td></tr>))}
            </tbody></table>
        </>}
        {d.extra_activities.length > 0 && <>
          <SectionHead id="extras" />
          <table className="pd-table pd-acts"><thead><tr>{multi && <th>Store</th>}<th>Activity</th><th>What the customer gets</th><th>Price / value</th></tr></thead>
            <tbody>{d.extra_activities.map((a, i) => (
              <tr key={i}>{multi && <td data-label="Store">{a.store_code}</td>}<td data-label="Activity" className="pd-main"><b>{a.name}</b></td>
                <td data-label="What the customer gets">{a.description}{a.code && <> · code <b>{a.code}</b></>}</td><td data-label="Price / value">{a.price ?? '-'}</td></tr>))}
            </tbody></table>
        </>}
        <Custom html={d.custom_bottom} />
        {!d.events.length && !d.product_offers.length && !d.focus.length && !d.extra_activities.length && (
          <p className="pd-meta">No offers in this period yet.</p>
        )}
      </div>
    </div>
  )
}

export default function PublicDoc({ token }: { token: string }) {
  const q = useQuery({ queryKey: ['public-doc', token], queryFn: () => api.get<DocData>(`/public/docs/${token}`), retry: false })
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (q.data) document.title = `${q.data.name} · Offers` }, [q.data])
  if (q.isLoading) return <div className="pd-wrap"><div className="pd-page"><p className="pd-meta">Loading…</p></div></div>
  if (q.error || !q.data) {
    const msg = q.error instanceof ApiError ? q.error.message : 'This doc could not be loaded.'
    return <div className="pd-wrap"><div className="pd-page"><h1>Doc not available</h1><p className="pd-meta">{msg}</p></div></div>
  }
  return (
    <div className="pd-wrap">
      <div className="pd-bar">
        <button type="button" onClick={() => window.print()}>Download PDF</button>
        <a href={`/api/public/docs/${token}/docx`} onClick={() => { setSaving(true); setTimeout(() => setSaving(false), 3000) }}>
          {saving ? 'Preparing…' : 'Download .docx'}</a>
      </div>
      <DocBody d={q.data} token={token} />
    </div>
  )
}
