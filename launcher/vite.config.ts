import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 설치·설정 창 화면. Tauri가 dist를 앱에 넣는다. 모델 목록은 서버와 같은 backend/scribe/models.json을 쓴다.
export default defineConfig({
  plugins: [react()],
  base: './',
  clearScreen: false,
  server: { port: 1420, strictPort: true, fs: { allow: ['..'] } },
  build: { target: 'safari15', outDir: 'dist', emptyOutDir: true },
})
