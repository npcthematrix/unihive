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

/** 线程池实时快照 + 累计计数。 */
export interface ExecutorSnapshot {
  capacity: number
  /** 已创建 worker 数。 */
  workers: number
  /** 正在执行任务的 worker 数 (瞬时)。 */
  active: number
  /** 队列中等待的任务数 (瞬时)。 */
  queued: number
  /** 累计提交执行的任务数。 */
  total: number
  /** 历史峰值并发。 */
  peak: number
}

/** Reader 并发闸门 (信号量)。 */
export interface ReaderGate {
  capacity: number
  /** 当前占用 (瞬时)。 */
  acquired: number
  /** 当前等待者 (瞬时)。 */
  waiters: number
  /** 累计进入次数。 */
  total: number
  /** 历史峰值占用。 */
  peak: number
  /** 累计发生等待的次数。 */
  waiters_total: number
}

/** 当前实际连接的行情服务器。 */
export interface CurrentServer {
  host: string
  port: number
}

/** 单个 upstream client 的运行时指标 (key 随实现扩展) */
export interface UpstreamMetrics {
  total_requests?: number
  total_errors?: number
  error_counts?: Record<string, number>
  pool_stats?: Record<string, unknown>
  current_server?: CurrentServer | null
  thread_pool?: ExecutorSnapshot
  reader_gate?: ReaderGate | null
  daily_l1?: L1CacheStats
  top_board_l1?: L1CacheStats
  unusual_l1?: L1CacheStats
  [k: string]: unknown
}

/** THSDK 组件运行态 (ThsdkClient.get_metrics)。 */
export interface ThsdkMetrics {
  status: string
  available: boolean
  authenticated: boolean
  auth_mode: 'credentials' | 'guest'
  watchlist_write: boolean
  /** 熔断器: closed=正常 / open=熔断打开 / half_open=半开探活。 */
  circuit_breaker: 'closed' | 'open' | 'half_open'
  cb_failures: number
  /** open 态距自动探活的剩余秒数; 其它状态为 null。 */
  cb_cooldown_remaining_sec: number | null
  call_timeout_sec: number
  throttle_ms: number
  /** 距下次可调用的节流冷却剩余秒数。 */
  throttle_remaining_sec: number
  worker_pool: ExecutorSnapshot
  session_persisted: boolean
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
