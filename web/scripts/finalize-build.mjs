/**
 * vite build 后把产物部署到 Starlette 现有挂载点:
 *   dist/index.html      → ../../console.html          (Route("/") 与 /console.html)
 *   dist/assets/*        → ../../static/console/assets/* (Mount("/static"))
 *
 * base=/static/console/ 保证 HTML 内资源 URL 是 /static/console/assets/...。
 * 旧 SPA 的 console.js / console.css 留在 static/console/ 直到最终迁移阶段删除。
 */
import { cpSync, rmSync, copyFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(webRoot, '..')

const distIndex = resolve(webRoot, 'dist/index.html')
const distAssets = resolve(webRoot, 'dist/assets')
const targetHtml = resolve(projectRoot, 'console.html')
const targetAssets = resolve(projectRoot, 'static/console/assets')

rmSync(targetAssets, { recursive: true, force: true })
cpSync(distAssets, targetAssets, { recursive: true })
copyFileSync(distIndex, targetHtml)

console.log('[finalize-build] console.html + static/console/assets updated')
