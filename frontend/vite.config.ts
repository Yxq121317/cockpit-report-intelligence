import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5171,
    strictPort: true,
    // 允许通过公网隧道（Cloudflare / localhost.run）访问开发服务器。
    allowedHosts: ['.trycloudflare.com', '.lhr.life'],
    // 前后端同源：开发时由 Vite 代理 /api 到本机后端，隧道下也能正常工作。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
