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

/** 运行时长: 天/时/分/秒两级组合, 如 "2天 3小时"、"15分 42秒"、"8秒"。 */
export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分`
  if (minutes > 0) return `${minutes}分 ${Math.floor(seconds % 60)}秒`
  return `${Math.floor(seconds)}秒`
}
