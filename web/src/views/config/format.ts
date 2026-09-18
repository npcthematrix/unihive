/**
 * ConfigView 子组件纯函数 + 常量。
 * 与旧 console.js 行为保持一致 (humanizeDuration 等), 不依赖 Vue, 便于复用。
 */

/** 把秒数格式化为人类可读字符串 (与旧 console.js:676-685 一致)。 */
export function humanizeDuration(seconds: number): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s < 0) return '-'
  if (s < 60) return `${s} 秒`
  if (s < 3600) {
    const m = s / 60
    return `${Number.isInteger(m) ? m : m.toFixed(1)} 分钟`
  }
  if (s < 86400) {
    const h = s / 3600
    return `${Number.isInteger(h) ? h : h.toFixed(1)} 小时`
  }
  const d = s / 86400
  return `${Number.isInteger(d) ? d : d.toFixed(1)} 天`
}

/** 把日志配置值渲染为键值对数组 (用于网格展示)。 */
export interface LogKvRow {
  key: string
  display: string
}

export function loggingToRows(logging: Record<string, unknown> | undefined): LogKvRow[] {
  if (!logging) return []
  return Object.entries(logging).map(([k, v]) => {
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      const inner = Object.entries(v as Record<string, unknown>)
        .map(([k2, v2]) => `${k2}=${String(v2)}`)
        .join(', ')
      return { key: k, display: inner || '-' }
    }
    return { key: k, display: v == null ? '-' : String(v) }
  })
}

/** 把 retry 对象 JSON 化 (旧版 console.js 直接 escapeHtml(JSON.stringify(cfg.retry))) */
export function retryToString(retry: unknown): string {
  try {
    return JSON.stringify(retry)
  } catch {
    return String(retry)
  }
}

/** 工具参数渲染: 字符串原样返回; 对象输出 "name" 或 "*name" (required) */
export interface ParamCell {
  text: string
  required: boolean
}

export function paramCells(params: Array<string | { name?: string; required?: boolean }> | undefined): ParamCell[] {
  if (!params) return []
  return params.map((p) => {
    if (typeof p === 'string') return { text: p, required: false }
    const name = p.name || '?'
    return { text: name, required: Boolean(p.required) }
  })
}

/** 拼接所有参数名, 用作搜索键 (与旧版 console.js:906 searchKey 等价) */
export function paramsSearchKey(params: Array<string | { name?: string }> | undefined): string {
  if (!params) return ''
  return params.map((p) => (typeof p === 'string' ? p : p.name || '')).join(' ')
}

/** 7 个 subtab 的 key 常量 */
export const TAB_KEYS = [
  'upstreams',
  'mapping',
  'tools',
  'cache',
  'proxy',
  'fuyaoCache',
  'overlays',
] as const
export type TabKey = (typeof TAB_KEYS)[number]

/** subtab 展示标题 */
export const TAB_TITLES: Record<TabKey, string> = {
  upstreams: '上游',
  mapping: '工具映射',
  tools: '工具规格',
  cache: '缓存',
  proxy: '代理设置',
  fuyaoCache: 'FUYAO 缓存',
  overlays: '本地覆盖',
}
