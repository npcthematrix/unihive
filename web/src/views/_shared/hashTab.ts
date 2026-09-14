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