import { http } from './client'

// ---------------------------------------------------------------------------
// /api/config — 全量配置快照 (敏感字段已服务端脱敏)
// ---------------------------------------------------------------------------

/** 单条上游配置。env / retry 等可选字段是 yaml 里出现的就回, 否则省略。 */
export interface UpstreamConfig {
  enabled?: boolean
  description?: string
  type?: string
  /** 仅 http 类型有 base_url; tdx_quant / mootdx2 / omni 等本地类型可能缺省。 */
  base_url?: string
  /** 服务端已脱敏 (前 2 + *** + 后 2)。 */
  api_key?: string
  timeout_seconds?: number
  /** 同上游 env 子表, 密钥类字段服务端脱敏。 */
  env?: Record<string, string>
  retry?: Record<string, unknown>
  [key: string]: unknown
}

/** 日志配置项的值; 旧版允许嵌套对象, 也允许字符串/数字。 */
export type LoggingValue = string | number | boolean | Record<string, unknown> | null

/** 工具规格参数: YAML 旧写法可能是裸字符串名, 新写法是对象。 */
export interface ConfigToolParam {
  name?: string
  type?: string
  required?: boolean
  description?: string
  [key: string]: unknown
}

export interface ConfigTool {
  name: string
  description?: string
  /** 路由键 → 上游工具名 映射。顺序即优先级, [0] 为主源。 */
  chain?: Record<string, string>
  /** deprecated: chain 已包含此信息, 但旧版仍然独立回传。 */
  upstream?: string
  upstream_tool?: string
  /** 旧 yaml 里这两个字段是顶层而非 cache 嵌套。 */
  cache_ttl_key?: string | null
  dangerous?: boolean
  params?: Array<string | ConfigToolParam>
  [key: string]: unknown
}

/** cache.ttl 段; value 是秒数 (number)。 */
export interface CacheConfig {
  enabled?: boolean
  db_path?: string
  ttl?: Record<string, number>
  [key: string]: unknown
}

export interface GatewayConfig {
  /** 后端按行读盘顺序回传的字典视图; 旧版 Object.entries 遍历顺序。 */
  upstreams?: Record<string, UpstreamConfig>
  upstream_tool_mapping?: Record<string, Record<string, string>>
  tools?: ConfigTool[]
  cache?: CacheConfig
  logging?: Record<string, LoggingValue>
  disable_dangerous?: boolean
  strict_registry?: boolean
  /** FUYAO 嵌套 TTL — endpoint.default + 工具级 override */
  fuyao_cache?: FuyaoCacheConfig
  /** 乐观锁 hash (size+mtime), 提交 PUT 时回带 */
  version?: string
  [key: string]: unknown
}

/** GET /api/config — 全量配置快照。 */
export async function getConfig(): Promise<GatewayConfig> {
  const { data } = await http.get<GatewayConfig>('/api/config')
  return data
}

// ---------------------------------------------------------------------------
// /api/config/fuyao-cache — FUYAO 嵌套缓存 TTL (endpoint default + tool override)
// ---------------------------------------------------------------------------

/** 单个 endpoint 的 default + 工具级 override。 */
export interface FuyaoEndpointConfig {
  /** endpoint 级默认 TTL 秒数 (≥ 0) */
  default: number
  /** 工具级覆盖, 键为 MCP tool 名称 */
  override?: Record<string, number>
}

/** 4 个固定 endpoint — 与后端 FUYAO_ENDPOINT_NAMES 对齐。 */
export const FUYAO_ENDPOINT_KEYS = [
  'fuyao_ashare',
  'fuyao_index',
  'fuyao_meta',
  'fuyao_fund',
] as const
export type FuyaoEndpointKey = (typeof FUYAO_ENDPOINT_KEYS)[number]

/** endpoint 中文标签 — 用于 UI 标题。 */
export const FUYAO_ENDPOINT_TITLES: Record<FuyaoEndpointKey, string> = {
  fuyao_ashare: 'A 股行情 (a-share)',
  fuyao_index: 'A 股指数 (a-share-index)',
  fuyao_meta: '跨市场元数据 (meta)',
  fuyao_fund: '基金 (fund)',
}

export interface FuyaoCacheConfig {
  endpoints?: Partial<Record<FuyaoEndpointKey, FuyaoEndpointConfig>>
  version?: string
}

/** GET /api/config/fuyao-cache — 当前 FUYAO 嵌套 TTL 配置。 */
export async function getFuyaoCache(): Promise<FuyaoCacheConfig> {
  const { data } = await http.get<FuyaoCacheConfig>('/api/config/fuyao-cache')
  return data
}

/** PUT /api/config/fuyao-cache — 写入 FUYAO 嵌套 TTL, 附带乐观锁 version。

  Returns:
    { fuyao_cache, version } — 新 version 必须让前端更新本地 state。
  Throws:
    409 (version_conflict) — 前端应提示刷新后重试
    400 (validation_failed) — payload 校验失败, 含后端 message
*/
export async function saveFuyaoCache(
  payload: FuyaoCacheConfig,
  version: string,
): Promise<{ fuyao_cache: FuyaoCacheConfig; version: string }> {
  const { data } = await http.put<{ fuyao_cache: FuyaoCacheConfig; version: string }>(
    '/api/config/fuyao-cache',
    { fuyao_cache: payload, version },
  )
  return data
}

// ---------------------------------------------------------------------------
// /api/proxy/settings — TDX_QUANT 代理 (mootdx2 → tdx_quant) 设置
// ---------------------------------------------------------------------------

export type TdxQuantStatus =
  | 'ONLINE'
  | 'DEGRADED'
  | 'OFFLINE'
  | 'UNAVAILABLE'
  | 'DISABLED'
  | 'MISSING'
  | 'FAILED'
  | 'LOADING'
  | 'UNKNOWN'

export interface ProxyCoverageEntry {
  mootdx2_tool: string
  tdx_quant_tool: string
}

export interface ProxySettings {
  enabled: boolean
  tdx_quant_status: TdxQuantStatus
  coverage: ProxyCoverageEntry[]
}

/** GET /api/proxy/settings — 当前状态 + tdx_quant 实时状态 + 覆盖范围。 */
export async function getProxySettings(): Promise<ProxySettings> {
  const { data } = await http.get<ProxySettings>('/api/proxy/settings')
  return data
}

/** PUT /api/proxy/settings — 启用/停用代理。返回最新设置。 */
export async function setProxySettings(enabled: boolean): Promise<ProxySettings> {
  const { data } = await http.put<ProxySettings>('/api/proxy/settings', { enabled })
  return data
}
