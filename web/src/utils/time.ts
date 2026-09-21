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

/** "上次刷新"相对时间标签 — StatusView / L1CacheView 共用。 */
export function formatRefreshLabel({ loadError, lastFetchSec, ageSec }: RefreshLabelOptions): string {
  if (loadError && lastFetchSec === 0) return `刷新失败 (${loadError})`
  if (!lastFetchSec) return '从未刷新'
  if (ageSec < 5) return '刚刚刷新'
  if (ageSec < 60) return `${ageSec} 秒前刷新`
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)} 分钟前刷新`
  return `${Math.floor(ageSec / 3600)} 小时前刷新`
}
