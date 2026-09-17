import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'

/**
 * 单测配置 — 只覆盖 .ts 文件 (纯函数 + 客户端工具),
 * 不引入 @vitejs/plugin-vue: vitest 自带一份 vite, 类型与项目主 vite
 * 不完全一致会引发 Plugin 不兼容 (实测踩坑)。 单测用不到 .vue 编译。
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['tests/**/*.spec.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.ts'],
      exclude: [
        'src/main.ts',
        'src/api/auth.ts',
        'src/api/status.ts',
        'src/api/config.ts',
        'src/api/omni.ts',
        'src/api/mcp-browser.ts',
      ],
    },
  },
})
