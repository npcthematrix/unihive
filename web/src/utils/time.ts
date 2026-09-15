/**
 * 时间格式化工具。
 */

/** 把 epoch 毫秒格式化为本地时区 HH:MM:SS (24h), 用零填充。 */
export function formatHMS(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
