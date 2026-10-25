import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  server: {
    headers: { "Cache-Control": "no-store" },
    // Dev-only: talk to the local Python harness on 127.0.0.1:8765.
    // changeOrigin + Origin strip keep the backend's loopback/Host guards intact.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (req) => req.removeHeader("origin"));
        },
      },
      "/artifact": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
  plugins: [react()],
  resolve: {
    alias: {
      '@fynns/ui': fileURLToPath(
        new URL('./node_modules/@fynn7/ui-design-core/src/index.ts', import.meta.url),
      ),
    },
    dedupe: ['react', 'react-dom'],
  },
})
