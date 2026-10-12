import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 桌面端：构建产物由 FastAPI 后端直接托管（backend/app/main.py 的 SPA 兜底路由）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    // 开发态把 API 请求代理到后端，避免跨域
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8756',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  base: './',
})
