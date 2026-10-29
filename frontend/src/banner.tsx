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

/** The desktop banner of a design file: the element with id "banner-desktop" when present,
 *  otherwise the whole file. Returns a standalone document plus its natural size. */
function desktopDoc(html: string): { doc: string; w: number; h: number } {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const styles = [...parsed.querySelectorAll('link[rel="stylesheet"], style')].map((n) => n.outerHTML).join('\n')
  const el = parsed.getElementById('banner-desktop')
  if (!el) return { doc: html, w: 1975, h: 750 }
  const w = parseFloat(el.style.width) || 1975
  const h = parseFloat(el.style.height) || 750
  el.style.margin = '0'
  return { doc: `<!doctype html><html><head><meta charset="utf-8">${styles}<style>body{margin:0;background:transparent;overflow:hidden}</style></head><body>${el.outerHTML}</body></html>`, w, h }
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
  const [all, setAll] = useState(false)
  const b = q.data
  return (
    <Drawer open onClose={onClose} title={<div><h2>{name}</h2><small className="muted">Banner design{b?.filename ? ` · ${b.filename}` : ''}</small></div>}>
      {q.isLoading || !b ? <Loading /> : !b.content ? <small>No banner uploaded.</small> : (
        <>
          <div className="section-title" style={{ marginTop: 0 }}>Desktop</div>
          <BannerView banner={b} width={Math.min(640, window.innerWidth - 64)} />
          {b.kind === 'html' && (
            <>
              <div className="row mt"><button onClick={() => setAll(!all)}>{all ? 'Hide' : 'Show'} every format in the file</button></div>
              {all && (
                <div className="mt" style={{ overflow: 'auto', maxHeight: 520, border: '1px solid var(--border)', borderRadius: 4 }}>
                  <Scaled doc={b.content} w={5700} h={900} width={1710} />
                </div>
              )}
              <small className="muted" style={{ display: 'block', marginTop: 8 }}>Shown in a sandbox with scripts disabled. Images referenced with relative paths in the file cannot be shown here.</small>
            </>
          )}
        </>
      )}
    </Drawer>
  )
}
