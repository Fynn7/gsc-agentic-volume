import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 独占端口：5177（GSC 5173 / sandbox 5174 / mermaid 5176 / agentic-drive 5178 …）。
// strictPort 保证被占用时直接报错退出，禁止静默顺延到别的端口。
// 后端端口 follows GAV_PORT（默认 8765），代理目标同步跟随，禁止写死。
const backendPort = Number(process.env.GAV_PORT ?? 8765) || 8765;
const vitePort = Number(process.env.GAV_VITE_PORT ?? 5177) || 5177;

// https://vite.dev/config/
export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: vitePort,
    strictPort: true,
    headers: { "Cache-Control": "no-store" },
    // Dev-only: talk to the local Python harness on 127.0.0.1:<GAV_PORT>.
    // changeOrigin + Origin strip keep the backend's loopback/Host guards intact.
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (req) => req.removeHeader("origin"));
        },
      },
      "/artifact": {
        target: `http://127.0.0.1:${backendPort}`,
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
