/**
 * 数字 / 百分比格式化工具。
 */

/** 把 0-1 的比率格式化为百分比字符串, 如 0.913 -> "91.3%"。 */
export function formatPercent(rate: number, digits = 1): string {
  return `${(rate * 100).toFixed(digits)}%`
}

/** 紧凑时长: >=1h 用 h, >=1min 用 min, 否则 s。L1 缓存块/监控页共用。 */
export function formatTtl(seconds: number): string {
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h`
  if (seconds >= 60) return `${Math.floor(seconds / 60)}min`
  return `${seconds}s`
}
