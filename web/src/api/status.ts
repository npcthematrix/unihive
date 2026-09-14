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
  db_size_mb?: number
  hits?: number | null
  misses?: number | null
  hit_rate?: number | null
  timestamp: number
  error?: string
}

export interface GatewayStatus {
  timestamp: number
  summary: StatusSummary
  upstreams: Record<string, UpstreamStatus>
  cache: CacheStats
}

/** GET /api/status — 网关/上游状态 + 缓存运行时统计。 */
export async function getStatus(): Promise<GatewayStatus> {
  const { data } = await http.get<GatewayStatus>('/api/status')
  return data
}
