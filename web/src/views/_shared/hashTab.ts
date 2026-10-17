/**
 * 共享 hash deep-link 工具。
 *
 * 阶段 4-6 的 ConfigView / OmniView / GuidesView 各自重复了一份等价的
 * parseTabFromQuery 闭包 (5 行), 仅 TAB_KEYS 常量不同。提取到一处, 避免
 * 后续类型收紧时三处飘移。
 */

/**
 * 从 vue-router 的 `route.query.tab` (或任意 unknown) 解析出有效的 subtab key。
 *
 * 规则:
 * - 数组形式取第一个元素 (vue-router 数组 query 标准形式)
 * - 非字符串一律 fallback
 * - 不在 valid 列表里也 fallback (防 `?tab=__proto__` 类注入)
 */
export function parseHashTab<T extends string>(
  q: unknown,
  valid: readonly T[],
  fallback: T,
): T {
  const candidate = Array.isArray(q) ? q[0] : q
  const value = typeof candidate === 'string' ? candidate : ''
  return (valid as readonly string[]).includes(value) ? (value as T) : fallback
}

/**
 * WAI-ARIA Tabs 的 roving tabindex 方向键导航: 给定当前 key 与按键, 返回应聚焦 /
 * 激活的目标 key。Left/Right 循环切换, Home/End 跳首尾; 非导航键返回 null
 * (调用方据此决定是否 preventDefault + 移动焦点)。
 *
 * 抽到共享: ConfigView 的顶层 tablist 与 GatewayMonitorView 的缓存子 tablist
 * 是同一套交互, 原先只在 ConfigView 内联实现, 现两处共用避免逻辑漂移。
 */
export function nextTabKey<T extends string>(
  keys: readonly T[],
  current: T,
  key: string,
): T | null {
  const idx = keys.indexOf(current)
  if (idx < 0) return null
  let next = idx
  if (key === 'ArrowRight') next = (idx + 1) % keys.length
  else if (key === 'ArrowLeft') next = (idx - 1 + keys.length) % keys.length
  else if (key === 'Home') next = 0
  else if (key === 'End') next = keys.length - 1
  else return null
  return keys[next]
}