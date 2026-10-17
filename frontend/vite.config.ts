import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 중에는 백엔드(127.0.0.1:8765)로 API/WebSocket을 넘긴다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', ws: true, changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 2500,
  },
})
