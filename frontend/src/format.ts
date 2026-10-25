export function money(v: number | null | undefined, currency = 'USD', digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: digits, minimumFractionDigits: digits,
    }).format(v)
  } catch {
    return `${currency} ${v.toFixed(digits)}`
  }
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v)
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

export function signedPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(digits)}%`
}

export function date(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v)
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return '—'
  return new Date(v).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function ago(v: string | null | undefined): string {
  if (!v) return 'never'
  const s = (Date.now() - new Date(v).getTime()) / 1000
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} days ago`
}

export function label(s: string | null | undefined): string {
  if (!s) return '—'
  return s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}
