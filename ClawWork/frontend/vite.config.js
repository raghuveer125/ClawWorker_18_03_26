import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
  // Load env file based on `mode` in the current working directory.
  const env = loadEnv(mode, process.cwd(), '')

  // Port configuration (from env or defaults)
  const API_PORT = env.API_PORT || process.env.API_PORT || '8001'
  const FRONTEND_PORT = env.FRONTEND_PORT || process.env.FRONTEND_PORT || '3001'

  return {
    plugins: [react()],
    base: command === 'build' ? '/ClawWork/' : '/',
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined
            if (id.includes('plotly.js-dist-min') || id.includes('react-plotly.js')) return 'plotly'
            if (id.includes('xlsx')) return 'xlsx'
            if (id.includes('docx-preview') || id.includes('mammoth') || id.includes('jszip')) return 'documents'
            if (id.includes('recharts')) return 'charts'
            if (id.includes('react-router-dom')) return 'router'
            if (id.includes('react') || id.includes('react-dom')) return 'vendor'
            return undefined
          },
        },
      },
    },
    server: {
      port: parseInt(FRONTEND_PORT),
      allowedHosts: ['trading.bhoomidaksh.xyz', 'trading.bhoomidaksh.ai', '.ngrok-free.app', '.ngrok.io'],
      proxy: {
        '/api': {
          target: `http://localhost:${API_PORT}`,
          changeOrigin: true,
        },
        '/ws': {
          target: `ws://localhost:${API_PORT}`,
          ws: true,
        },
      },
    },
  }
})
