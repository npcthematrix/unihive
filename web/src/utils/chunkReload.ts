/**
 * Round-13 簇M: 旧标签页懒加载 chunk 404 的自愈刷新。
 *
 * 部署链路: vite 产出内容 hash chunk → finalize-build 整目录替换
 * static/console/assets 并发布新 console.html (no-cache)。如果用户的
 * 标签页还停在旧 HTML 上, 切换路由时动态 import 旧 hash chunk 会 404,
 * 表现为整页空白/导航卡死。检测到这类错误后自动 reload 拉新 HTML 即可恢复。
 *
 * 防循环: 刷新时间戳写 sessionStorage; 若新页面仍立刻 chunk 失败
 * (发布本身就是坏的), 冷却窗内不再自动刷新, 避免无限 reload。
 *
 * storage 可能不可用 (Firefox cookie 被禁时 sessionStorage 访问抛
 * SecurityError, Safari 隐私模式 setItem 抛错): 任何 storage 异常都
 * 不得阻断自愈, 退回模块级内存时间戳 —— 冷却窗在当前页面生命周期内
 * 仍然有效。
 */

export const CHUNK_RELOAD_COOLDOWN_MS = 10_000
export const CHUNK_RELOAD_TS_KEY = 'unihive:chunk-reload-at'

const CHUNK_ERROR_PATTERNS = [
  // Chrome / Edge
  /Failed to fetch dynamically imported module/i,
  // Firefox
  /error loading dynamically imported module/i,
  // Safari
  /Importing a module script failed/i,
]

/** 判断错误是否为懒加载 chunk 加载失败 (404/网络切换/部署替换)。 */
export function isChunkLoadError(error: unknown): boolean {
  const e = error as { name?: string; message?: unknown } | null | undefined
  if (!e || typeof e !== 'object') return false
  if (e.name === 'ChunkLoadError') return true
  if (typeof e.message !== 'string') return false
  return CHUNK_ERROR_PATTERNS.some((re) => re.test(e.message as string))
}

/** 冷却判定: 从未刷过, 或上次自动刷新已过冷却窗 → 允许。 */
export function shouldReloadNow(lastReloadAt: number | null, now: number): boolean {
  return lastReloadAt === null || now - lastReloadAt >= CHUNK_RELOAD_COOLDOWN_MS
}

export interface ChunkReloadDeps {
  now?: () => number
  storage?: Pick<Storage, 'getItem' | 'setItem'>
  reload?: () => void
}

// storage 不可用时的内存兜底时间戳 (仅当前页面生命周期)。
let memoryTimestamp: string | null = null

/**
 * 记录时间戳并触发 reload。返回 false 表示冷却窗内被抑制
 * (调用方不必再做任何事 —— 页面停在错误态好过无限刷新)。
 */
export function reloadForStaleChunk(deps: ChunkReloadDeps = {}): boolean {
  const now = deps.now ?? (() => Date.now())
  const reload = deps.reload ?? (() => window.location.reload())

  // 取 storage 本身也可能抛 (某些浏览器 cookie 被禁时访问 sessionStorage 即炸)。
  let storage: Pick<Storage, 'getItem' | 'setItem'> | null = null
  if (deps.storage) {
    storage = deps.storage
  } else {
    try {
      storage = window.sessionStorage
    } catch {
      storage = null
    }
  }

  let raw: string | null = memoryTimestamp
  if (storage) {
    try {
      raw = storage.getItem(CHUNK_RELOAD_TS_KEY)
    } catch {
      raw = memoryTimestamp
    }
  }
  let lastReloadAt: number | null = null
  if (raw !== null) {
    const parsed = Number(raw)
    if (Number.isFinite(parsed)) lastReloadAt = parsed
  }

  const at = now()
  if (!shouldReloadNow(lastReloadAt, at)) return false

  const stamped = String(at)
  if (storage) {
    try {
      storage.setItem(CHUNK_RELOAD_TS_KEY, stamped)
    } catch {
      memoryTimestamp = stamped
    }
  } else {
    memoryTimestamp = stamped
  }
  reload()
  return true
}

type RouterWithOnError = {
  /** vue-router 4.x: 返回取消订阅函数。 */
  onError: (handler: (error: unknown) => unknown) => (() => void) | void
}

/**
 * 安装两路监听并返回卸载函数:
 *  - window 'vite:preloadError': vite 预加载动态 import 失败时触发,
 *    仅在我们确实接管 (要 reload) 时 preventDefault; 冷却抑制时必须放行,
 *    否则 __vitePreload 会 resolve undefined 造成空白路由;
 *  - router.onError: 懒加载组件的动态 import reject 时 vue-router 上报。
 */
export function installChunkReloadGuard(
  router: RouterWithOnError,
  deps: ChunkReloadDeps = {},
): () => void {
  const onPreloadError = (event: Event): void => {
    // 仅当真的要自愈刷新时才阻止 vite 默认抛错; 抑制刷新时放行,
    // 让 vite 的错误正常中止本次导航 (错误边界/onError 仍能看到它)。
    if (reloadForStaleChunk(deps)) event.preventDefault()
  }
  const onRouterError = (error: unknown): void => {
    if (isChunkLoadError(error)) reloadForStaleChunk(deps)
  }

  window.addEventListener('vite:preloadError', onPreloadError)
  const offRouterError = router.onError(onRouterError)

  return () => {
    window.removeEventListener('vite:preloadError', onPreloadError)
    if (typeof offRouterError === 'function') offRouterError()
  }
}
