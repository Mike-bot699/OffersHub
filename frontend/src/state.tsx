import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export type StoreRow = {
  id: number
  code: string
  name: string
  currency: string
  timezone: string
  is_demo: boolean
  logo?: string | null
  product_host?: string | null
  status: string
  platform: string
  shop_domain: string | null
  commercial_settings: Record<string, number | null>
  notes: string | null
  objectives: { id: number; objective: string; priority: number; params: Record<string, unknown> }[]
  integration: { status: string; shop_domain: string | null }
  freshness: Record<string, { status: string; last_success_at: string | null; records: number }>
}

export type Meta = {
  version: string
  env: string
  auth_mode: string
  encryption_configured: boolean
  ai_available: boolean
  ai_model: string | null
  shopify_api_version: string
  database: string
  objectives: string[]
  strategy_types: string[]
  offer_types: string[]
  write_actions_enabled: boolean
}

type Filters = {
  storeId: number | null // null = portfolio (all stores)
  days: number
  includeDemo: boolean
}

type Ctx = Filters & {
  set: (f: Partial<Filters>) => void
  stores: StoreRow[]
  store: StoreRow | null
  meta: Meta | undefined
  periodQs: string
}

const C = createContext<Ctx | null>(null)
const KEY = 'egi.filters'

function load(): Filters {
  if (import.meta.env.VITE_PREVIEW === '1') return { storeId: 1, days: 90, includeDemo: true }
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { storeId: null, days: 90, includeDemo: true, ...JSON.parse(raw) }
  } catch {
    /* storage unavailable */
  }
  return { storeId: null, days: 90, includeDemo: true }
}

export function AppState({ children }: { children: ReactNode }) {
  const [f, setF] = useState<Filters>(load)
  const storesQ = useQuery({ queryKey: ['stores'], queryFn: () => api.get<StoreRow[]>('/stores') })
  const metaQ = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/meta') })
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(f))
    } catch {
      /* ignore */
    }
  }, [f])
  const stores = useMemo(() => (storesQ.data ?? []).filter((s) => f.includeDemo || !s.is_demo), [storesQ.data, f.includeDemo])
  const store = stores.find((s) => s.id === f.storeId) ?? null
  useEffect(() => {
    if (f.storeId !== null && storesQ.data && !store) setF((p) => ({ ...p, storeId: null }))
  }, [f.storeId, storesQ.data, store])
  const value: Ctx = {
    ...f,
    set: (p) => setF((prev) => ({ ...prev, ...p })),
    stores,
    store,
    meta: metaQ.data,
    periodQs: `days=${f.days}`,
  }
  return <C.Provider value={value}>{children}</C.Provider>
}

export function useApp() {
  const v = useContext(C)
  if (!v) throw new Error('AppState missing')
  return v
}
