import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { useApp } from './state'
import { DemoBadge, ErrorBox, Seg } from './ui'
import Overview from './pages/Overview'
import Stores from './pages/Stores'
import Products from './pages/Products'
import Basket from './pages/Basket'
import Discounts from './pages/Discounts'
import { LiveOffers, OfferDatabase, OffersCalendar } from './pages/Offers'
import OfferAnalysis from './pages/OfferAnalysis'
import Simulator from './pages/Simulator'
import Analytics from './pages/Analytics'
import Opportunities from './pages/Opportunities'
import Experiments from './pages/Experiments'
import Integrations from './pages/Integrations'
import Settings from './pages/Settings'

// Main work: offers, discounts and order value. Secondary: data sources and set-up.
const NAV_MAIN = [
  ['/', 'Overview'],
  ['/live-offers', 'Live offers'],
  ['/basket', 'AOV & basket'],
  ['/discounts', 'Discounts'],
  ['/calendar', 'Offer planning'],
  ['/offer-database', 'Offer database'],
  ['/offer-analysis', 'Offer analysis'],
  ['/opportunities', 'Opportunities'],
  ['/experiments', 'Experiments'],
  ['/simulator', 'Simulator'],
] as const
const NAV_DATA = [
  ['/products', 'Products'],
  ['/analytics', 'Store analytics'],
  ['/stores', 'Stores'],
  ['/integrations', 'Integrations'],
  ['/settings', 'Settings'],
] as const

export default function App() {
  const { stores, storeId, days, includeDemo, set, store } = useApp()
  const auth = useQuery({ queryKey: ['auth'], queryFn: () => api.get<{ mode: string; authenticated: boolean }>('/auth/status') })
  const [locked, setLocked] = useState(false)
  useEffect(() => {
    const h = () => setLocked(true)
    window.addEventListener('egi:unauthorized', h)
    return () => window.removeEventListener('egi:unauthorized', h)
  }, [])
  if ((auth.data && !auth.data.authenticated) || locked) return <Login onDone={() => setLocked(false)} />

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          OffersHub
          <small className="muted">Offers, discounts & AOV</small>
        </div>
        <nav className="nav">
          {NAV_MAIN.map(([to, text]) => (
            <NavLink key={to} to={to} end={to === '/'}>{text}</NavLink>
          ))}
          <div className="nav-group">Data & setup</div>
          {NAV_DATA.map(([to, text]) => (
            <NavLink key={to} to={to} className="nav-secondary">{text}</NavLink>
          ))}
        </nav>
      </aside>
      <div className="main">
        <div className="topbar">
          <label className="row" style={{ gap: 6 }}>
            <span className="muted">Store</span>
            <select value={storeId ?? ''} onChange={(e) => set({ storeId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">All stores (portfolio)</option>
              {stores.map((s) => (
                <option key={s.id} value={s.id}>{s.code} — {s.name}{s.is_demo ? ' [DEMO]' : ''}</option>
              ))}
            </select>
          </label>
          <Seg value={days} onChange={(v) => set({ days: v })}
            options={[{ value: 30, label: '30d' }, { value: 90, label: '90d' }, { value: 180, label: '180d' }, { value: 365, label: '12m' }]} />
          <label className="row" style={{ gap: 6 }} title="Demo data is synthetic and never mixed into real totals unless shown">
            <input type="checkbox" checked={includeDemo} onChange={(e) => set({ includeDemo: e.target.checked })} />
            <span className="secondary">Show demo data</span>
          </label>
          <span className="spacer" />
          {store?.is_demo && <DemoBadge />}
        </div>
        {import.meta.env.VITE_PREVIEW === '1' && (
          <div className="content" style={{ paddingBottom: 0 }}>
            <div className="notice info">
              <strong>Anteprima statica.</strong> Interfaccia reale dell'applicazione con i dati sintetici del negozio DEMO,
              registrati dal server. Si naviga liberamente; simulazioni, import e modifiche richiedono l'app in esecuzione.
            </div>
          </div>
        )}
        {store?.is_demo && (
          <div className="content" style={{ paddingBottom: 0 }}>
            <div className="notice demo">
              <strong>Demo store.</strong> Everything shown for {store.code} is synthetic data generated to demonstrate the
              application. It is not a real business and is excluded from portfolio totals.
            </div>
          </div>
        )}
        <main className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/basket" element={<Basket />} />
            <Route path="/discounts" element={<Discounts />} />
            <Route path="/live-offers" element={<LiveOffers />} />
            <Route path="/calendar" element={<OffersCalendar />} />
            <Route path="/offer-database" element={<OfferDatabase />} />
            <Route path="/offer-analysis" element={<OfferAnalysis />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/experiments" element={<Experiments />} />
            <Route path="/simulator" element={<Simulator />} />
            <Route path="/products" element={<Products />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/stores" element={<Stores />} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<p>Page not found.</p>} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const qc = useQueryClient()
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh', padding: 16 }}>
      <form className="card" style={{ width: 340 }} onSubmit={async (e) => {
        e.preventDefault()
        try {
          await api.post('/auth/login', { password: pw })
          await qc.invalidateQueries()
          onDone()
        } catch (x) { setErr(x) }
      }}>
        <h1 style={{ marginBottom: 12 }}>OffersHub</h1>
        <label className="field">Password<input type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} /></label>
        <div className="mt"><button className="primary" type="submit">Sign in</button></div>
        <div className="mt"><ErrorBox error={err} /></div>
      </form>
    </div>
  )
}
