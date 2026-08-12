import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Both planes proxy to the service in development, so the browser sees one
// origin — which is also why the service's card rewrite (keyed on the
// request's own Host) hands the a2a-js client URLs that stay inside the
// proxy chain.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:9300',
      '/a2a': 'http://127.0.0.1:9300',
      '/agui': 'http://127.0.0.1:9300',
    },
  },
})
