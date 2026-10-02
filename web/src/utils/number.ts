/**
 * 数字 / 百分比格式化工具。
 */

/** 把 0-1 的比率格式化为百分比字符串, 如 0.913 -> "91.3%"。 */
export function formatPercent(rate: number, digits = 1): string {
  return `${(rate * 100).toFixed(digits)}%`
}

/**
 * 数字 → 字符串; null / undefined 一律展示为占位 "—",
 * 避免"0"和"未知"歧义. 监控/缓存卡共用 (H5 抽取)。
 */
export function numOrDash(value: number | null | undefined): string {
  return value == null ? '—' : String(value)
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

/** 过滤掉采样点中的 null/undefined, 返回纯数字序列 (用于 sparkline 渲染)。 */
export function definedPoints(pts: (number | null)[]): number[] {
  return pts.filter((v): v is number => v != null)
}

/** sparkline 的"比率"型 aria-label: 起点% → 终点%, 含采样点计数。不足 2 点返回 "采样不足"。 */
export function rateAria(label: string, pts: (number | null)[]): string {
  const defined = definedPoints(pts)
  if (defined.length < 2) return `${label}: 采样不足`
  return `${label}: ${formatPercent(defined[0])} → ${formatPercent(defined[defined.length - 1])}, 共 ${defined.length} 个采样点`
}

/** sparkline 的"计数"型 aria-label: 起点 → 终点, 含采样点计数。不足 2 点返回 "采样不足"。 */
export function countAria(label: string, pts: (number | null)[]): string {
  const defined = definedPoints(pts)
  if (defined.length < 2) return `${label}: 采样不足`
  return `${label}: ${defined[0]} → ${defined[defined.length - 1]}, 共 ${defined.length} 个采样点`
}
