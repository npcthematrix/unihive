import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 产物路径必须挂在 Starlette 的 Mount('/static', directory=static/) 下:
// base=/static/console/ → dist/assets/* 部署后通过 /static/console/assets/* 访问。
// 仓库根的 console.html 由 scripts/finalize-build.mjs 从 dist/index.html 拷贝。
export default defineConfig(({ command }) => ({
  plugins: [vue()],
  // dev 用根路径 (http://localhost:5173/); build 产物挂到 Mount('/static') 下
  base: command === 'build' ? '/static/console/' : '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // 开发期所有同源后端路径代理到本地网关 (默认 :18080)
      '/api': { target: 'http://127.0.0.1:18080', changeOrigin: true },
      '/login': { target: 'http://127.0.0.1:18080', changeOrigin: true },
      '/logout': { target: 'http://127.0.0.1:18080', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:18080', changeOrigin: true },
      '/mcp': { target: 'http://127.0.0.1:18080', changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Element Plus 全量引入单 chunk ~1.09MB (gzip 341KB); 内部本机工具可接受,
    // 阶段 6 可换 unplugin-vue-components 按需引入进一步压缩。
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
        },
      },
    },
  },
}))
