import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
import path from 'node:path'

// Vite config: React + Tailwind v4. Dev server proxies the FastAPI backend (127.0.0.1:5230).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5231,
    proxy: {
      '/api': 'http://127.0.0.1:5230',
      '/ws': { target: 'ws://127.0.0.1:5230', ws: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 2500 },
})
