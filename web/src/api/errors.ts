/**
 * 错误跟踪 API — MCP 工具调用错误日志 (内存环形 + SQLite 溢写)。
 * 对应后端 GET /api/errors (console_api.errors_api)。
 */
import { http } from './client'

/** 单条错误记录中的路由跳点 (不含入参)。 */
export interface ErrorHop {
  source: string | null
  success: boolean | null
  error: string | null
  duration_ms: number | null
}

/** 单条 MCP 工具调用错误记录。 */
export interface ErrorItem {
  /** UTC ISO 毫秒, 如 2026-09-28T05:00:00.000Z */
  ts: string
  /** epoch 秒 (排序/去重用, 后端附加) */
  ts_epoch: number
  tool: string
  route_key: string
  /** 最终成功/失败的上游 (全链失败时 null)。 */
  source: string | null
  success: boolean
  /** 顶层错误消息。 */
  error: string
  duration_ms: number
  hops: ErrorHop[]
  /** 触发调用时的传入参数 (便于复现; 已截断)。 */
  params?: Record<string, unknown>
  /** 网关级错误类别 (跨上游归一)。 */
  error_kind?: ErrorKindType | null
}

/** 网关级统一错误类别 (与后端 ErrorKind 对齐)。 */
export type ErrorKindType =
  | 'invalid_request'
  | 'upstream_unavailable'
  | 'timeout'
  | 'rate_limited'
  | 'no_data'
  | 'internal_error'

export interface ErrorListResponse {
  items: ErrorItem[]
  in_memory: number
  in_db: number
  latest_ts: string | null
  /**
   * M7: 后端算作 `in_db > 0 or bool(history)` —— history=true 时恒为 true,
   * 与"库里到底有没有历史"无关。所以 UI 判断历史是否为空要用 in_db === 0,
   * 不能用这个字段, 否则历史模式下永远不会出现空态提示。
   */
  history_available: boolean
  /** 内存环形缓冲上限 (后端 _MEMORY_MAXLEN, 供前端展示)。 */
  memory_cap: number
  /** 触发批量写 SQLite 的阈值 (后端 _FLUSH_THRESHOLD)。 */
  flush_threshold: number
  /** SQLite 库内上限 (后端 _DB_MAX_ROWS, LRU 淘汰)。 */
  db_cap: number
  timestamp: number
}

export interface GetErrorsOptions {
  limit?: number
  /** true = SQLite 历史 + 内存近期合并; false = 仅内存近期。 */
  history?: boolean
}

/** GET /api/errors — 拉取 MCP 工具调用错误记录。 */
export async function getErrors(
  opts: GetErrorsOptions = {},
): Promise<ErrorListResponse> {
  const { data } = await http.get<ErrorListResponse>('/api/errors', {
    params: {
      limit: opts.limit ?? 50,
      history: opts.history ? 'true' : 'false',
    },
  })
  return data
}
