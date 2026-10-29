// Banner designs uploaded for full-site offers. HTML files are rendered in a sandboxed iframe
// with scripts disabled (no allow-scripts, no allow-same-origin), so a design file can never run
// code inside the application. External images and fonts referenced by the file still load.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { Drawer, Loading } from './ui'

type Banner = { kind: 'html' | 'image' | null; content: string | null; filename: string | null }

export function useBanner(eventId: number, enabled = true) {
  return useQuery({ queryKey: ['banner', eventId], enabled, staleTime: 5 * 60_000,
    queryFn: () => api.get<Banner>(`/events/${eventId}/banner`) })
}

type Format = { label: string; doc: string; w: number; h: number }

function standalone(styles: string, el: HTMLElement): string {
  el.style.margin = '0'
  return `<!doctype html><html><head><meta charset="utf-8">${styles}<style>body{margin:0;background:transparent;overflow:hidden}</style></head><body>${el.outerHTML}</body></html>`
}

/** Every format in a design file: elements with data-screen-label or an id starting with
 *  "banner"/"label", each as a standalone document with its natural size and the caption the
 *  file shows above it (e.g. "Mobile · 560×750"). */
function formats(html: string): Format[] {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const styles = [...parsed.querySelectorAll('link[rel="stylesheet"], style')].map((n) => n.outerHTML).join('\n')
  const els = [...parsed.querySelectorAll<HTMLElement>('[data-screen-label], [id^="banner"], [id^="label"]')]
    .filter((el, i, all) => all.indexOf(el) === i && !all.some((o) => o !== el && o.contains(el)))
  return els.map((el) => {
    const prev = el.previousElementSibling
    const caption = prev && prev.tagName === 'P' ? prev.textContent?.trim() : null
    return { label: caption || el.getAttribute('data-screen-label') || el.id, w: parseFloat(el.style.width) || 1200,
      h: parseFloat(el.style.height) || 600, doc: standalone(styles, el) }
  })
}

/** The desktop banner of a design file ("banner-desktop"), or the whole file without one. */
function desktopDoc(html: string): { doc: string; w: number; h: number } {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const styles = [...parsed.querySelectorAll('link[rel="stylesheet"], style')].map((n) => n.outerHTML).join('\n')
  const el = parsed.getElementById('banner-desktop')
  if (!el) return { doc: html, w: 1975, h: 750 }
  return { doc: standalone(styles, el), w: parseFloat(el.style.width) || 1975, h: parseFloat(el.style.height) || 750 }
}

function Scaled({ doc, w, h, width }: { doc: string; w: number; h: number; width: number }) {
  const s = width / w
  return (
    <div style={{ width, height: h * s, overflow: 'hidden', borderRadius: 4, background: '#f4f4f2', flex: 'none' }}>
      <iframe title="Banner preview" sandbox="" srcDoc={doc} loading="lazy"
        style={{ width: w, height: h, border: 0, transform: `scale(${s})`, transformOrigin: '0 0', pointerEvents: 'none' }} />
    </div>
  )
}

export function BannerView({ banner, width }: { banner: Banner; width: number }) {
  if (!banner.content) return null
  if (banner.kind === 'image') return <img src={banner.content} alt="Banner" style={{ width, display: 'block', borderRadius: 4 }} />
  const d = desktopDoc(banner.content)
  return <Scaled {...d} width={width} />
}

/** Small desktop-banner preview for a table cell; opens the full preview on click. */
export function BannerThumb({ eventId, name }: { eventId: number; name: string }) {
  const [open, setOpen] = useState(false)
  const q = useBanner(eventId)
  return (
    <>
      <button className="banner-thumb" title="Open banner preview" onClick={(e) => { e.stopPropagation(); setOpen(true) }}>
        {q.data?.content ? <BannerView banner={q.data} width={120} /> : <span className="muted">…</span>}
      </button>
      {/* The drawer renders inside a table row: keep its clicks from reaching the row. */}
      {open && <span onClick={(e) => e.stopPropagation()}><BannerDrawer eventId={eventId} name={name} onClose={() => setOpen(false)} /></span>}
    </>
  )
}

export function BannerDrawer({ eventId, name, onClose }: { eventId: number; name: string; onClose: () => void }) {
  const q = useBanner(eventId)
  const [full, setFull] = useState(false)
  const b = q.data
  const fmts = b?.kind === 'html' && b.content ? formats(b.content) : []
  return (
    <Drawer open onClose={onClose} title={<div><h2>{name}</h2><small className="muted">Banner design{b?.filename ? ` · ${b.filename}` : ''}</small></div>}>
      {q.isLoading || !b ? <Loading /> : !b.content ? <small>No banner uploaded.</small> : (
        <>
          <div className="section-title" style={{ marginTop: 0 }}>Desktop</div>
          <button className="banner-open" title="View full screen" onClick={() => setFull(true)}>
            <BannerView banner={b} width={Math.min(640, window.innerWidth - 64)} />
          </button>
          <div className="row mt">
            <button className="primary" onClick={() => setFull(true)}>{fmts.length > 1 ? `View all ${fmts.length} formats full screen` : 'View full screen'}</button>
          </div>
          {fmts.length > 1 && <small className="muted" style={{ display: 'block', marginTop: 6 }}>Formats in the file: {fmts.map((f) => f.label).join(' · ')}</small>}
          <small className="muted" style={{ display: 'block', marginTop: 8 }}>Shown in a sandbox with scripts disabled. Images referenced with relative paths in the file cannot be shown here.</small>
          {full && <FullScreen banner={b} fmts={fmts} name={name} onClose={() => setFull(false)} />}
        </>
      )}
    </Drawer>
  )
}

/** Each format as large as the window allows (never above its real size), one under the other. */
function FullScreen({ banner, fmts, name, onClose }: { banner: Banner; fmts: Format[]; name: string; onClose: () => void }) {
  const avail = window.innerWidth - 96
  return (
    <div className="banner-full" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="banner-full-head" onClick={(e) => e.stopPropagation()}>
        <strong>{name}</strong><button onClick={onClose} aria-label="Close">✕ Close</button>
      </div>
      <div className="banner-full-body" onClick={(e) => e.stopPropagation()}>
        {banner.kind === 'image' ? <img src={banner.content!} alt="Banner" style={{ maxWidth: avail, display: 'block' }} />
          : (fmts.length ? fmts : [{ label: 'Banner', ...desktopDoc(banner.content!) }]).map((f) => (
            <div key={f.label} className="banner-full-item">
              <div className="banner-full-label">{f.label}</div>
              <Scaled doc={f.doc} w={f.w} h={f.h} width={Math.min(f.w, avail)} />
            </div>))}
      </div>
    </div>
  )
}
