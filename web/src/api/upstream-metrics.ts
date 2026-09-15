import { http } from './client'

/** get_daily L1 内存缓存运行时统计 (与后端 MooTDX2Client.get_metrics() 对齐) */
export interface DailyL1Stats {
  enabled: boolean
  hits?: number
  misses?: number
  /** null = 尚未有任何请求 */
  hit_rate?: number | null
  size?: number
  maxsize?: number
  ttl_seconds?: number
  inflight_keys?: number
}

/** 单个 upstream client 的运行时指标 (key 可能随实现扩展) */
export interface UpstreamMetrics {
  total_requests?: number
  total_errors?: number
  error_counts?: Record<string, number>
  pool_stats?: Record<string, unknown>
  daily_l1?: DailyL1Stats
  [k: string]: unknown
}

export interface UpstreamMetricsResponse {
  upstream: string
  metrics: UpstreamMetrics
  timestamp: number
}

/** GET /api/upstreams/<name>/metrics — 拉某个上游的运行时指标 */
export async function getUpstreamMetrics(
  name: string,
): Promise<UpstreamMetricsResponse> {
  const { data } = await http.get<UpstreamMetricsResponse>(
    `/api/upstreams/${encodeURIComponent(name)}/metrics`,
  )
  return data
}
