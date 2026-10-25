// Thin fetch wrapper. All errors from the backend arrive as {error: {code, message, details}}.

export class ApiError extends Error {
  status: number
  code: string
  details: unknown
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

// Static preview build: answers GET requests from recorded demo responses; writes are refused.
type PreviewData = { responses: Record<string, unknown> }
const PREVIEW = (window as unknown as { __EGI_PREVIEW__?: PreviewData }).__EGI_PREVIEW__

function previewKey(path: string) {
  const [p, q = ''] = path.split('?')
  const params = [...new URLSearchParams(q).entries()].sort(([a], [b]) => a.localeCompare(b))
  return params.length ? `${p}?${new URLSearchParams(params).toString()}` : p
}

async function previewRequest<T>(method: string, path: string): Promise<T> {
  await new Promise((r) => setTimeout(r, 60))
  if (method !== 'GET') {
    throw new ApiError(0, 'preview', 'Anteprima statica: simulazioni, import e modifiche richiedono il server dell\'applicazione.')
  }
  const key = previewKey(path)
  if (key in PREVIEW!.responses) return PREVIEW!.responses[key] as T
  const base = key.split('?')[0]
  const fallback = Object.keys(PREVIEW!.responses).find((k) => k.split('?')[0] === base)
  if (fallback) return PREVIEW!.responses[fallback] as T
  throw new ApiError(404, 'preview', 'Questa vista non è inclusa nell\'anteprima statica.')
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (PREVIEW) return previewRequest<T>(method, path)
  const init: RequestInit = { method, credentials: 'same-origin', headers: {} }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  let res: Response
  try {
    res = await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError(0, 'network', 'Cannot reach the server. Is the backend running?')
  }
  const text = await res.text()
  const data = text ? safeJson(text) : null
  if (!res.ok) {
    const err = (data as { error?: { code: string; message: string; details?: unknown } } | null)?.error
    if (res.status === 401 && !path.startsWith('/auth')) window.dispatchEvent(new Event('egi:unauthorized'))
    throw new ApiError(res.status, err?.code ?? 'http_error', err?.message ?? `Request failed (${res.status})`, err?.details)
  }
  return data as T
}

function safeJson(t: string) {
  try {
    return JSON.parse(t)
  } catch {
    return t
  }
}

export const api = {
  get: <T>(p: string) => request<T>('GET', p),
  post: <T>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  put: <T>(p: string, b: unknown) => request<T>('PUT', p, b),
  patch: <T>(p: string, b: unknown) => request<T>('PATCH', p, b),
  del: <T>(p: string) => request<T>('DELETE', p),
  upload: <T>(p: string, form: FormData) => request<T>('POST', p, form),
}

export function qs(params: Record<string, string | number | boolean | null | undefined>) {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v))
  const out = s.toString()
  return out ? `?${out}` : ''
}
