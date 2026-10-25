import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { AppState } from './state'
import './index.css'

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      {import.meta.env.VITE_PREVIEW === '1' ? (
        <MemoryRouter>
          <AppState>
            <App />
          </AppState>
        </MemoryRouter>
      ) : (
        <BrowserRouter>
          <AppState>
            <App />
          </AppState>
        </BrowserRouter>
      )}
    </QueryClientProvider>
  </StrictMode>,
)
