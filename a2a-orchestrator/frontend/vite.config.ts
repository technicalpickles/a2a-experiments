import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies to the service so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:9300',
      '/agui': 'http://127.0.0.1:9300',
    },
  },
})
