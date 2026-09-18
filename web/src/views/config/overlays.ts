/**
 * config.db 薄覆盖层面板的纯函数 (不依赖 Vue, 便于单测)。
 *
 * 覆盖路径是点分叶子路径, 目前 P1 只产出两类:
 *   - upstreams.<name>.enabled   上游热开关 (删除 = 恢复出厂启用态)
 *   - fuyao_cache.tools.<tool>   FUYAO 工具级 TTL (删除 = 回落全局档位)
 */

export type OverlayKind = 'upstream-enabled' | 'fuyao-ttl' | 'other'

export interface OverlayKindInfo {
  kind: OverlayKind
  /** 中文标签, 用于表格「类型」列。 */
  label: string
  /** 是否保存后立即热生效 (影响确认框文案)。 */
  hot: boolean
}

const ENABLED_RE = /^upstreams\.([A-Za-z0-9_-]+)\.enabled$/
const FUYAO_TOOL_RE = /^fuyao_cache\.tools\.(.+)$/

/** 覆盖路径归类。 */
export function overlayKind(path: string): OverlayKindInfo {
  if (ENABLED_RE.test(path)) {
    return { kind: 'upstream-enabled', label: '上游开关', hot: true }
  }
  if (FUYAO_TOOL_RE.test(path)) {
    return { kind: 'fuyao-ttl', label: 'FUYAO TTL', hot: true }
  }
  return { kind: 'other', label: '其他', hot: false }
}

/** upstreams.<name>.enabled → <name>; 其他路径返回 null。 */
export function upstreamNameOf(path: string): string | null {
  const m = ENABLED_RE.exec(path)
  return m ? m[1] : null
}

/** fuyao_cache.tools.<tool> → <tool>; 其他路径返回 null。 */
export function fuyaoToolOf(path: string): string | null {
  const m = FUYAO_TOOL_RE.exec(path)
  return m ? m[1] : null
}

/**
 * 覆盖值的紧凑展示:
 * - 布尔/数字: 原样字符串
 * - 字符串: 加 JSON 引号
 * - 其他 (对象/数组/null): JSON 序列化
 * 循环引用等异常时回落 String(value)。
 */
export function formatOverlayValue(value: unknown): string {
  if (typeof value === 'boolean' || typeof value === 'number') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** enabled 覆盖值的人类描述 (true=强制启用 / false=强制停用)。 */
export function describeEnabledValue(value: unknown): string {
  return value === true ? '强制启用' : value === false ? '强制停用' : String(value)
}

/** TTL 秒的人类描述 (0 = 不缓存)。 */
export function describeTtlSeconds(value: unknown): string {
  if (value === 0) return '不缓存 (0s)'
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (value < 60) return `${value} 秒`
    if (value < 3600) return `${value / 60} 分钟`
    if (value < 86400) return `${value / 3600} 小时`
    return `${value / 86400} 天`
  }
  return formatOverlayValue(value)
}

/** updated_at (UTC ISO) → 本地紧凑时间; 解析失败原样返回。 */
export function formatUpdatedAt(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}
