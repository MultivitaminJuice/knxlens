import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Gebaut wird direkt ins Python-Paket: knxlens/web/static/editor (kein Node zur Laufzeit nötig).
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../knxlens/web/static/editor',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8770' },
  },
})
