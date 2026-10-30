// Calendar (Gantt) view of offers: one row per offer, bars on a day timeline with a Today line.
// Offers without dates are never placed on the timeline: they are listed under it.
import { useEffect, useRef, useState } from 'react'
import { date } from './format'
import { Seg } from './ui'
import { OfferStatusBadge, TagChip, type Ev } from './offerStatus'

const DAY = 86_400_000
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DISPLAY: Record<string, [string, string]> = { full_site: ['d-full', 'Full-Displayed'], email_only: ['d-email', 'Email-Only'], hidden: ['d-hidden', 'Hidden'] }
const STATUS_TEXT: Record<string, string> = { in_progress: 'In Progress', scheduled: 'Scheduled', live: 'Live', paused: 'Paused', completed: 'Completed', cancelled: 'Cancelled' }
const COLOR_KEY = 'offershub.calendar.colorBy'

const utc = (s: string) => Date.parse(`${s}T00:00:00Z`)
const todayUtc = () => { const d = new Date(); return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) }
const monday = (t: number) => t - ((new Date(t).getUTCDay() + 6) % 7) * DAY

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return { ref, w }
}

function codesText(e: Ev) {
  const codes = [...e.coupon_codes, ...(e.linked_discounts ?? [])]
  return codes.length ? codes.join(', ') : e.activation === 'automatic' ? 'Automatic' : 'No code'
}

export function OffersCalendar({ rows, onOpen, compact = false, days: fixedDays }: {
  rows: Ev[]; onOpen: (e: Ev) => void; compact?: boolean; days?: number
}) {
  const [months, setMonths] = useState(3)
  const [shift, setShift] = useState(0) // in 4-week steps
  const [colorBy, setColorByState] = useState<'display' | 'status'>(() => {
    try { return localStorage.getItem(COLOR_KEY) === 'status' ? 'status' : 'display' } catch { return 'display' }
  })
  const setColorBy = (v: 'display' | 'status') => { setColorByState(v); try { localStorage.setItem(COLOR_KEY, v) } catch { /* storage unavailable */ } }
  const { ref, w } = useWidth()
  const today = todayUtc()
  const from = compact ? today - 3 * DAY : monday(today - 7 * DAY) + shift * 28 * DAY
  const days = fixedDays ?? (compact ? 30 : months === 1 ? 35 : months * 30 + 7)
  const to = from + (days - 1) * DAY
  const px = Math.max(w / days, compact ? 8 : months === 6 ? 6 : 10)
  const width = days * px
  const multiStore = new Set(rows.map((r) => r.store_code)).size > 1
  const undated = rows.filter((r) => !r.start_date || !r.end_date)
  const dated = rows.filter((r) => r.start_date && r.end_date).sort((a, b) => a.start_date!.localeCompare(b.start_date!) || a.end_date!.localeCompare(b.end_date!))
  const inView = dated.filter((r) => utc(r.end_date!) >= from && utc(r.start_date!) <= to)
  const before = dated.filter((r) => utc(r.end_date!) < from).length
  const after = dated.filter((r) => utc(r.start_date!) > to).length

  const monthsHead: { label: string; w: number }[] = []
  const weeksHead: { label: string; left: number; w: number }[] = []
  const weekends: number[] = []
  for (let i = 0; i < days; i++) {
    const d = new Date(from + i * DAY)
    if (i === 0 || d.getUTCDate() === 1) {
      const next = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)
      monthsHead.push({ label: `${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`, w: Math.min(Math.round((next - +d) / DAY), days - i) * px })
    }
    if (i === 0 || d.getUTCDay() === 1) weeksHead.push({ label: `${d.getUTCDate()} ${MON[d.getUTCMonth()]}`, left: i * px, w: Math.min(7 - ((d.getUTCDay() + 6) % 7), days - i) * px })
    if (d.getUTCDay() === 6) weekends.push(i)
  }
  const x = (s: string) => ((utc(s) - from) / DAY) * px
  const todayX = ((today - from) / DAY) * px + px / 2

  return (
    <div className={`cal ${compact ? 'compact' : ''}`}>
      {!compact && (
        <div className="row" style={{ marginBottom: 10, gap: 8 }}>
          <Seg value={months} onChange={(m) => { setMonths(m); setShift(0) }} options={[{ value: 1, label: '1M' }, { value: 3, label: '3M' }, { value: 6, label: '6M' }]} />
          <button type="button" onClick={() => setShift(shift - 1)} aria-label="Earlier">‹</button>
          <button type="button" onClick={() => setShift(0)} disabled={shift === 0}>Today</button>
          <button type="button" onClick={() => setShift(shift + 1)} aria-label="Later">›</button>
          <small className="muted">{date(new Date(from).toISOString().slice(0, 10))} → {date(new Date(to).toISOString().slice(0, 10))}</small>
          <span className="spacer" />
          <small className="muted">Colour by</small>
          <Seg value={colorBy} onChange={setColorBy} options={[{ value: 'display', label: 'Displayed' }, { value: 'status', label: 'Status' }]} />
        </div>
      )}
      <div className="cal-grid">
        <div className="cal-left">
          <div className="cal-head">Offer</div>
          {inView.map((e) => (
            <div key={e.id} className="cal-cell" onClick={() => onOpen(e)} title={e.name}>
              <div className="nm">{multiStore && <span className="badge" style={{ marginRight: 4 }}>{e.store_code}</span>}{e.name}</div>
              <div className="meta"><OfferStatusBadge status={e.status} />{!compact && e.tags?.map((t) => <TagChip key={t} tag={t} />)}</div>
            </div>
          ))}
        </div>
        <div className="cal-right" ref={ref}>
          <div className="cal-canvas" style={{ width }}>
            <div className="cal-head">
              <div className="cal-months">{monthsHead.map((m, i) => <div key={i} style={{ width: m.w }}>{m.label}</div>)}</div>
              <div className="cal-weeks">{weeksHead.map((m, i) => <div key={i} style={{ width: m.w }}>{m.w > 34 ? m.label : ''}</div>)}</div>
            </div>
            {weekends.map((i) => <div key={i} className="cal-wkend" style={{ left: i * px, width: 2 * px }} />)}
            {weeksHead.map((m, i) => <div key={i} className="cal-vline" style={{ left: m.left }} />)}
            {today >= from && today <= to && <div className="cal-today" style={{ left: todayX }}><span>TODAY</span></div>}
            {inView.map((e) => {
              const a = Math.max(x(e.start_date!), 0)
              const b = Math.min(x(e.end_date!) + px, width)
              const one = e.start_date === e.end_date
              const text = codesText(e)
              const outside = one || b - a < text.length * 6.6 + 16
              const color = colorBy === 'status' ? `s-${e.status ?? 'unset'}` : (DISPLAY[e.display ?? '']?.[0] ?? 'd-unset')
              const tip = `${e.name}\n${date(e.start_date)} → ${date(e.end_date)}\n${text}\nDisplayed: ${DISPLAY[e.display ?? '']?.[1] ?? 'not set'} · ${STATUS_TEXT[e.status ?? ''] ?? 'Status not set'}`
              return (
                <div key={e.id} className="cal-lane">
                  <div className={`cal-bar ${color} ${e.status === 'live' ? 'live' : ''} ${one ? 'one' : ''}`} title={tip} onClick={() => onOpen(e)}
                    style={one ? { left: a + px / 2 - 8 } : { left: a, width: b - a }}>
                    {!outside && <span className="cd">{text}</span>}
                  </div>
                  {outside && <span className="cal-out" style={{ left: (one ? a + px / 2 + 12 : b + 6) }} onClick={() => onOpen(e)}>{text}</span>}
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {!inView.length && <div className="muted" style={{ padding: '12px 4px' }}>No dated offer in this window.</div>}
      {(before > 0 || after > 0) && (
        <small className="muted" style={{ display: 'block', marginTop: 6 }}>
          Outside this window: {before > 0 && `${before} earlier`}{before > 0 && after > 0 && ' · '}{after > 0 && `${after} later`}
        </small>
      )}
      {!compact && (
        <div className="cal-legend">
          {colorBy === 'display'
            ? <>{Object.values(DISPLAY).map(([c, t]) => <span key={c}><i className={`sw ${c}`} />{t}</span>)}<span><i className="sw d-unset" />Displayed not set</span></>
            : <>{Object.entries(STATUS_TEXT).map(([k, t]) => <span key={k}><i className={`sw s-${k}`} />{t}</span>)}<span><i className="sw s-unset" />Not set</span></>}
          <span><i className="sw live-sw" />Live now</span>
          <span><i className="sw today-sw" />Today</span>
        </div>
      )}
      {!compact && undated.length > 0 && (
        <div className="cal-undated">
          <strong>No dates ({undated.length})</strong> <small className="muted">— add start and end dates to place them on the calendar</small>
          <div className="chips" style={{ marginTop: 6 }}>{undated.map((e) => <button key={e.id} type="button" className="chip" onClick={() => onOpen(e)}>{e.name}</button>)}</div>
        </div>
      )}
    </div>
  )
}
