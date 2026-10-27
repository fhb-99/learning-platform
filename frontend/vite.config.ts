import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    // 开发环境通过同源代理调用后端，数据库凭据仅保存在后端。
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
