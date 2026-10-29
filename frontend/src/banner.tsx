// Banner designs uploaded for full-site offers. HTML files are rendered in a sandboxed iframe
// with scripts disabled (no allow-scripts, no allow-same-origin), so a design file can never run
// code inside the application. External images and fonts referenced by the file still load.
import { useState } from 'react'
import { createPortal } from 'react-dom'
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
  const [open, setOpen] = useState<Format | null>(null)
  const b = q.data
  const fmts = b?.kind === 'html' && b.content ? formats(b.content) : []
  const avail = Math.min(716, window.innerWidth - 48)
  // Wide formats take the full width, mid-size ones share a row, small labels sit three to a row.
  const sizeOf = (f: Format) => (f.w >= 1000 ? avail : f.w >= 400 ? (avail - 16) / 2 : Math.min(f.w, (avail - 32) / 3))
  return (
    <Drawer open onClose={onClose} title={<div><h2>{name}</h2><small className="muted">Banner design{b?.filename ? ` · ${b.filename}` : ''} · click a format to enlarge</small></div>}>
      {q.isLoading || !b ? <Loading /> : !b.content ? <small>No banner uploaded.</small> : b.kind === 'image' ? (
        <button className="banner-open" onClick={() => setOpen({ label: 'Banner', doc: '', w: 0, h: 0 })}><BannerView banner={b} width={avail} /></button>
      ) : (
        <div className="banner-grid">
          {(fmts.length ? fmts : [{ label: 'Banner', ...desktopDoc(b.content) }]).map((f) => (
            <button key={f.label} className="banner-open" title="Enlarge" onClick={() => setOpen(f)} style={{ width: sizeOf(f) }}>
              <div className="banner-label">{f.label}</div>
              <Scaled doc={f.doc} w={f.w} h={f.h} width={sizeOf(f)} />
            </button>))}
        </div>
      )}
      {b?.content && <small className="muted" style={{ display: 'block', marginTop: 10 }}>Shown in a sandbox with scripts disabled. Images referenced with relative paths in the file cannot be shown here.</small>}
      {open && b && <Popup banner={b} f={open} onClose={() => setOpen(null)} />}
    </Drawer>
  )
}

/** One format, as large as the window allows (never above its real size), above everything else. */
function Popup({ banner, f, onClose }: { banner: Banner; f: Format; onClose: () => void }) {
  const maxW = window.innerWidth - 80
  const maxH = window.innerHeight - 140
  const body = banner.kind === 'image'
    ? <img src={banner.content!} alt="Banner" style={{ maxWidth: maxW, maxHeight: maxH, display: 'block' }} />
    : <Scaled doc={f.doc} w={f.w} h={f.h} width={f.w * Math.min(1, maxW / f.w, maxH / f.h)} />
  return createPortal(
    <div className="banner-popup" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="banner-popup-inner" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
          <strong style={{ color: '#fff' }}>{f.label}</strong>
          <button onClick={onClose}>✕ Close</button>
        </div>
        {body}
      </div>
    </div>,
    document.body,
  )
}
