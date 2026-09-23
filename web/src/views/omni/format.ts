/**
 * OmniView 子组件纯函数 + 常量。
 * 与旧 console.js OMNI 段行为保持一致, 不依赖 Vue。
 */

/** 数据库存储值 / 内部别名 → 中文展示名。 */
const SOURCE_DISPLAY: Record<string, string> = {
  all: '全部',
  THS: '同花顺',
  fuyao: '同花顺',
  TDX: 'TDX-QUANT',
  tqquant: 'TDX-QUANT',
  tdxquant: 'TDX-QUANT',
}

export function displaySource(raw: string | undefined | null): string {
  if (!raw) return '-'
  return SOURCE_DISPLAY[raw] || raw
}

const BOARD_TYPE_DISPLAY: Record<string, string> = {
  all: '全部',
  industry: '行业板块',
  concept: '概念板块',
  style: '风格板块',
  region: '地域板块',
}

export function displayBoardType(raw: string | undefined | null): string {
  if (!raw) return '-'
  return BOARD_TYPE_DISPLAY[raw] || raw
}

/**
 * SyncPanel 的 board_type 下拉项。顺序与数据库存储值一致 (同花顺/通达信共享)。
 * `all` 是 UI 哨兵, 后端透传 → 全量同步; 不属于 BOARD_TYPE_DISPLAY。
 */
export const BOARD_TYPE_KEYS = ['industry', 'concept', 'style', 'region'] as const

/** 控制台内部数据源取值 (注意与 MCP 工具对外的 THS/TDX 不同, 勿混用)。 */
export const SOURCE_KEYS = ['fuyao', 'tqquant'] as const

/** sync status → Element Plus el-tag type. */
export function statusTagType(status: string | undefined): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'success') return 'success'
  if (status === 'running') return 'warning'
  if (status === 'failed') return 'danger'
  return 'info'
}

/** sync status → unicode 前缀 (色盲用户友好, 不依赖颜色判别)。 */
export function statusIcon(status: string | undefined): string {
  if (status === 'success') return '✓ '
  if (status === 'running') return '⚠ '
  if (status === 'failed') return '✗ '
  return ''
}

/**
 * 解析后端时间字符串。后端 datetime('now')/CURRENT_TIMESTAMP 存的是 UTC
 * 'YYYY-MM-DD HH:MM:SS'（无 Z、无 T）；这里显式按 UTC 解析后转本地。
 * 已带时区/ISO 完整信息的输入则原样交给 Date。
 */
function parseStoredDate(s: string): Date | null {
  const raw = String(s).trim()
  const d = new Date(raw.includes('T') ? raw : raw.replace(' ', 'T') + 'Z')
  return Number.isFinite(d.getTime()) ? d : null
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 后端 UTC 时间 → 本地 'YYYY-MM-DD HH:MM:SS'。 */
export function formatTime(s: string | undefined | null): string {
  if (!s) return '-'
  const d = parseStoredDate(s)
  if (!d) return String(s).replace('T', ' ').substring(0, 19)
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
  )
}

/** 计算耗时 (秒)；start/end 同为 UTC，差值与时区无关。 */
export function formatDuration(start: string | undefined, end: string | undefined): string | null {
  if (!start || !end) return null
  const s = parseStoredDate(start)
  const e = parseStoredDate(end)
  if (!s || !e || e.getTime() < s.getTime()) return null
  return ((e.getTime() - s.getTime()) / 1000).toFixed(1) + 's'
}

/** UTC 时间 → 本地日期 YYYY-MM-DD (用于 sector 表格的更新时间列)。 */
export function formatDate(s: string | undefined | null): string {
  if (!s) return '-'
  const d = parseStoredDate(s)
  if (!d) return String(s).slice(0, 10)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** 3 个 subtab key 常量 */
export const TAB_KEYS = ['stats', 'query', 'sync'] as const
export type OmniTabKey = (typeof TAB_KEYS)[number]

export const TAB_TITLES: Record<OmniTabKey, string> = {
  stats: '数据统计',
  query: '板块查询',
  sync: '同步管理',
}
