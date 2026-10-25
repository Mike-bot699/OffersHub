import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs separately on :8000; in production FastAPI serves dist/.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1000 },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
})
