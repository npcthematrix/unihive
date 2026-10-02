import { http } from './client'

/** /api/status 返回的上游状态取值 (与后端 console_api.py 一致)。 */
export type UpstreamStatusValue =
  | 'online'
  | 'degraded'
  | 'offline'
  | 'configured'
  | 'disabled'
  | 'unknown'

export interface UpstreamStatus {
  enabled: boolean
  type: string
  description: string
  /** online=探活成功; configured=已启用但该类型不发探测请求; disabled=未启用。 */
  status: UpstreamStatusValue
  latency_ms: number | null
  last_error: string | null
}

export interface StatusSummary {
  total: number
  online: number
  degraded: number
  offline: number
  configured: number
  disabled: number
  /** 后端按实际状态桶动态累加, 可能出现 unknown 等额外键。 */
  [key: string]: number
}

export interface CacheStats {
  enabled: boolean
  /** 停用态 (cache DB 不存在/异常) 时后端只回 enabled+error+timestamp, 以下键整体缺失。 */
  entries?: number | null
  live_entries?: number | null
  /** 已过期但尚未被 cleanup tick 清掉的条目。 */
  expired_entries?: number | null
  /** expires_at IS NULL 的历史遗留行 (Round-12 后禁止新写)。 */
  null_expiry_entries?: number | null
  /** 最快到期的活跃条目 (epoch 秒)。 */
  nearest_expiry?: number | null
  /** 最旧条目写入时间 (epoch 秒)。 */
  oldest_created?: number | null
  db_size_mb?: number
  /** cache.db-wal 文件大小 (字节)。 */
  wal_size_bytes?: number
  /** DELETE 后未回收的死页数 (VACUUM 可回收)。 */
  freelist_count?: number | null
  page_count?: number | null
  page_size?: number | null
  hits?: number | null
  misses?: number | null
  hit_rate?: number | null
  /** cache_stats.json 写入时间/进程 pid (识别是否来自当前网关进程)。 */
  stats_updated_at?: number | null
  stats_pid?: number | null
  timestamp: number
  error?: string
}

export interface GatewayStatus {
  timestamp: number
  /** 网关进程 pid (与 cache.stats_pid 比对)。 */
  pid?: number
  summary: StatusSummary
  upstreams: Record<string, UpstreamStatus>
  cache: CacheStats
}

/** GET /api/status — 网关/上游状态 + 缓存运行时统计。 */
export async function getStatus(options?: { signal?: AbortSignal }): Promise<GatewayStatus> {
  const { data } = await http.get<GatewayStatus>('/api/status', options)
  return data
}
