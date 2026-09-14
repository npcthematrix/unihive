import { http } from './client'

/** 参数可以是对象, 也可能是裸字符串名或 '?' 占位 (YAML 旧写法, 展示时过滤)。 */
export interface ToolParamInfo {
  name: string
  type?: string
  required?: boolean
  description?: string
  enum?: Array<string | number>
}

export type ToolParam = string | ToolParamInfo

export type DataSourceType = 'online' | 'offline' | 'mixed'

export interface ToolSpec {
  name: string
  description: string
  /** 主数据源 = 上游名, 与分组 key 一致。 */
  source: string
  dangerous: boolean
  /** realtime_quote 已在后端置空 (实时不缓存)。 */
  cache_ttl_key: string | null
  cache_ttl_seconds: number | null
  data_source_type: DataSourceType | null
  routing: string | null
  /** 降级链, 顺序即优先级, [0] 为主源。 */
  chain: string[]
  params: ToolParam[]
}

export interface InterfacesResponse {
  timestamp: number
  tools: ToolSpec[]
}

/** GET /api/interfaces — 全量工具清单 + routing chain + 缓存 TTL (后端 5s 缓存)。 */
export async function getInterfaces(): Promise<InterfacesResponse> {
  const { data } = await http.get<InterfacesResponse>('/api/interfaces')
  return data
}
