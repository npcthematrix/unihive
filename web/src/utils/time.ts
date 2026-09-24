/**
 * 时间格式化工具。
 */

/** 把 epoch 毫秒格式化为本地时区 HH:MM:SS (24h), 用零填充。 */
export function formatHMS(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export interface RefreshLabelOptions {
  /** 当前加载错误; 与从未成功拉取叠加时显示"刷新失败 (msg)" */
  loadError?: string
  /** 上次成功 fetch 的时间戳 (sec), 0 = 从未拉过 */
  lastFetchSec: number
  /** 距上次 fetch 的秒数 */
  ageSec: number
}

/** "上次刷新"相对时间标签 — 监控类页面共用。 */
export function formatRefreshLabel({ loadError, lastFetchSec, ageSec }: RefreshLabelOptions): string {
  if (loadError && lastFetchSec === 0) return `刷新失败 (${loadError})`
  if (!lastFetchSec) return '从未刷新'
  if (ageSec < 5) return '刚刚刷新'
  if (ageSec < 60) return `${ageSec} 秒前刷新`
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)} 分钟前刷新`
  return `${Math.floor(ageSec / 3600)} 小时前刷新`
}

/**
 * "更新于 HH:MM:SS" / "加载失败" / "尚未加载" 三态标签。监控/管理页共用。
 *
 * - loadError 非空 → "加载失败"
 * - lastRefresh 为 0/null/undefined → "尚未加载"
 * - 否则 → "更新于 HH:MM:SS"
 */
export function formatLastRefreshLabel(
  loadError: string | null | undefined,
  lastRefresh: number | null | undefined,
): string {
  if (loadError) return '加载失败'
  if (!lastRefresh) return '尚未加载'
  return `更新于 ${formatHMS(lastRefresh)}`
}
