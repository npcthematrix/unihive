import { http } from './client'

/** GET /api/runtime 返回的网关进程运行态 (计数在 _execute_cached 入口累计)。 */
export interface RuntimeStats {
  pid: number
  /** initialize 成功后的运行秒数; null = 网关尚未就绪。 */
  uptime_seconds: number | null
  /** 当前在途工具调用数。 */
  active_requests: number
  /** 当前发布的 MCP 工具数。 */
  tool_count: number
  /** 近 1 分钟窗口内的调用数。 */
  calls_last_1m: number
  /** 近 5 分钟窗口内的调用数。 */
  calls_last_5m: number
  /** 调用速率 (次/分); 当前等同 calls_last_1m, 留字段便于后续换滑窗。 */
  calls_per_min: number
  timestamp: number
}

/** GET /api/runtime — 网关 PID / 运行时长 / 在途 / 工具数 / 近期调用。 */
export async function getRuntimeStats(): Promise<RuntimeStats> {
  const { data } = await http.get<RuntimeStats>('/api/runtime')
  return data
}
