/**
 * vite build 后把产物部署到 Starlette 现有挂载点:
 *   dist/index.html      → ../../console.html          (Route("/") 与 /console.html)
 *   dist/assets/*        → ../../static/console/assets/* (Mount("/static"))
 *   dist/favicon.svg     → ../../static/console/favicon.svg
 *   dist/favicon.png     → ../../static/console/favicon.png
 *   dist/apple-touch-icon.png → ../../static/console/apple-touch-icon.png
 *
 * base=/static/console/ 保证 HTML 内资源 URL 是 /static/console/assets/...。
 * 旧 SPA 的 console.js / console.css 留在 static/console/ 直到最终迁移阶段删除。
 *
 * favicon cache-busting: 浏览器对 favicon 的缓存比普通资源顽固得多, URL 不变
 * 就一直显示旧图标 (换图后用户「没生效」几乎都是这个原因)。这里按图标内容
 * 算哈希并写进 console.html 的 href, 换图后 URL 自动变化, 无需手动改版本号。
 */
import { cpSync, rmSync, copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(webRoot, '..')

const distIndex = resolve(webRoot, 'dist/index.html')
const distAssets = resolve(webRoot, 'dist/assets')
const distFaviconSvg = resolve(webRoot, 'dist/favicon.svg')
const distFaviconPng = resolve(webRoot, 'dist/favicon.png')
const distAppleTouchIcon = resolve(webRoot, 'dist/apple-touch-icon.png')
const targetHtml = resolve(projectRoot, 'console.html')
const targetAssets = resolve(projectRoot, 'static/console/assets')
const targetFaviconSvg = resolve(projectRoot, 'static/console/favicon.svg')
const targetFaviconPng = resolve(projectRoot, 'static/console/favicon.png')
const targetAppleTouchIcon = resolve(projectRoot, 'static/console/apple-touch-icon.png')

rmSync(targetAssets, { recursive: true, force: true })
cpSync(distAssets, targetAssets, { recursive: true })

// public/ 下的散列无关静态文件 (favicon / apple-touch-icon) 单独拷, 缺失不阻断构建。
// 先拷图标再处理 HTML —— 哈希要从落地后的文件算。
const iconFiles = [
  [distFaviconSvg, targetFaviconSvg],
  [distFaviconPng, targetFaviconPng],
  [distAppleTouchIcon, targetAppleTouchIcon],
]
const copiedIcons = []
for (const [from, to] of iconFiles) {
  if (!existsSync(from)) continue
  copyFileSync(from, to)
  copiedIcons.push(to)
}

/** 图标内容哈希 (前 8 位)。PNG 由 SVG 派生, 一并纳入可覆盖「只换位图」的情况。 */
const iconHash = createHash('sha1')
  .update(copiedIcons.map((p) => readFileSync(p)).reduce((a, b) => Buffer.concat([a, b]), Buffer.alloc(0)))
  .digest('hex')
  .slice(0, 8)

// 给三个图标引用加 ?v=<hash>。负向先行断言防止二次部署时叠加 (?v=a?v=b)。
const ICON_RE = /(\/static\/console\/(?:favicon\.svg|favicon\.png|apple-touch-icon\.png))(?!\?v=)/g
const html = readFileSync(distIndex, 'utf8').replace(ICON_RE, `$1?v=${iconHash}`)
writeFileSync(targetHtml, html, 'utf8')

console.log(`[finalize-build] console.html + static/console/assets updated (icon v=${iconHash})`)
