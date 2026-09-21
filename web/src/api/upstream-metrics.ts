import { http } from './client'

/** 单个 L1 内存缓存的运行时统计 (与后端 mootdx2 get_metrics 各子块对齐) */
export interface L1CacheStats {
  enabled: boolean
  hits?: number
  misses?: number
  /** null = 尚未有任何请求 */
  hit_rate?: number | null
  size?: number
  maxsize?: number
  /** 静态档为配置 TTL; daily_l1 为动态计算到次日 9:00 的剩余秒数 */
  ttl_seconds?: number
  /** 仅 daily_l1: Reader 并发信号量上限 */
  reader_semaphore_limit?: number
}

/** 单个 upstream client 的运行时指标 (key 随实现扩展) */
export interface UpstreamMetrics {
  total_requests?: number
  total_errors?: number
  error_counts?: Record<string, number>
  pool_stats?: Record<string, unknown>
  daily_l1?: L1CacheStats
  top_board_l1?: L1CacheStats
  unusual_l1?: L1CacheStats
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
