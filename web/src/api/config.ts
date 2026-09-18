import { http } from './client'

/**
 * 乐观锁版本号。
 *
 * 2026-09-18 P1 起持久化切到 data/config.db 薄覆盖层, 版本号是整数 revision
 * (从 0 开始, 每次净变更 +1); 仅当网关以 UNIHIVE_CONFIG_SOURCE=yaml 启动时
 * 回落到旧的 16 位文件 hash 字符串。前端两种都接受。
 */
export type ConfigRevision = number | string

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
  /** FUYAO 工具级 TTL 覆盖 (fuyao_cache.tools) */
  fuyao_cache?: FuyaoCacheConfig
  /**
   * 乐观锁版本: config.db 模式下是整数 revision, yaml 逃生模式下是
   * 16 位文件 hash 字符串。提交 PUT 时原样回带。
   */
  version?: ConfigRevision
  [key: string]: unknown
}

/** GET /api/config — 全量配置快照。 */
export async function getConfig(): Promise<GatewayConfig> {
  const { data } = await http.get<GatewayConfig>('/api/config')
  return data
}

// ---------------------------------------------------------------------------
// /api/config/fuyao-cache — FUYAO 工具级缓存 TTL 覆盖 (config.db overlay)
// ---------------------------------------------------------------------------

/** 4 个固定 endpoint — 仅用于面板分组展示 (后端 mapping 中的 fuyao_* 上游名)。 */
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
  /**
   * 工具级 TTL 覆盖: 键为 MCP tool 名称, 值为 TTL 秒数 (≥ 0, 0=不缓存)。
   * 生效值 = YAML 基线 tools 合并 config.db 覆盖行; 由 FuyaoCachePanel
   * 提交到后端 `/api/config/fuyao-cache` (与出厂相同的行会被服务端自动删行)。
   */
  tools?: Record<string, number>
  version?: ConfigRevision
}

/**
 * 派生某 gateway tool 对应的 FUYAO endpoint。
 *
 * 数据源是后端 `config.upstream_tool_mapping`:
 *   { gatewayToolName: { upstreamName: upstreamToolName, ... } }
 * 上游名即 4 个固定 endpoint 之一 (fuyao_ashare/fuyao_index/fuyao_meta/
 * fuyao_fund)。一个 gateway 工具可挂多个上游 (路由降级链), 此时按
 * FUYAO_ENDPOINT_KEYS 的固定顺序取第一个命中的 fuyao_* backer;
 * 没有任何 fuyao backer 返回 undefined (该工具不属于本面板)。
 */
export function fuyaoEndpointOf(
  toolName: string,
  upstreamToolMapping?: Record<string, Record<string, string>> | null,
): FuyaoEndpointKey | undefined {
  const backers = upstreamToolMapping?.[toolName]
  if (!backers) return undefined
  for (const ep of FUYAO_ENDPOINT_KEYS) {
    if (Object.prototype.hasOwnProperty.call(backers, ep)) return ep
  }
  return undefined
}

/** GET /api/config/fuyao-cache — 当前生效的 FUYAO 工具级 TTL (基线 + 覆盖)。 */
export async function getFuyaoCache(): Promise<FuyaoCacheConfig> {
  const { data } = await http.get<FuyaoCacheConfig>('/api/config/fuyao-cache')
  return data
}

/** PUT /api/config/fuyao-cache — 写入工具级 TTL 覆盖, 附带乐观锁 revision。

  只把与 YAML 出厂不同的值落进 config.db; 保存后热生效, 无需重启。

  Returns:
    { fuyao_cache, version } — 新 revision 必须让前端更新本地 state。
  Throws:
    409 (version_conflict) — 前端应提示刷新后重试
    400 (validation_failed) — payload 校验失败, 含后端 message
    503 (config_store_unavailable) — 网关以 yaml 模式启动, 覆盖层只读
*/
export async function saveFuyaoCache(
  payload: FuyaoCacheConfig,
  version: ConfigRevision,
): Promise<{ fuyao_cache: FuyaoCacheConfig; version: ConfigRevision }> {
  const { data } = await http.put<{
    fuyao_cache: FuyaoCacheConfig
    version: ConfigRevision
  }>('/api/config/fuyao-cache', { fuyao_cache: payload, version })
  return data
}

// ---------------------------------------------------------------------------
// /api/config/upstreams/<name>/enabled — 上游启用/禁用热开关
// ---------------------------------------------------------------------------

/** PUT 热开关响应: 持久化 version + 运行时热增删/连接结果。 */
export interface UpstreamToggleResult {
  name: string
  enabled: boolean
  /** 启用时 client 是否真正连上; 禁用响应恒 false。 */
  connected: boolean
  tools_added: string[]
  tools_removed: string[]
  /** 目标态与当前态一致时后端 no-op, 前后端均不改动。 */
  changed?: boolean
  /** config.db 整数 revision (yaml 逃生模式下为文件 hash 字符串)。 */
  version: ConfigRevision
}

/**
 * PUT /api/config/upstreams/{name}/enabled — 热切换上游启用态。
 *
 * 后端先把 ``upstreams.<name>.enabled`` 覆盖写入 config.db (与出厂值相同
 * 则删行), 再实时增删对外 /mcp 的接口并连接/断开上游 client, 无需重启。
 *
 * Throws:
 *   409 (version_conflict) — 配置已被其他会话改动, 应提示刷新后重试
 *   404 — 未知上游名
 *   500 — 覆盖已落库但运行时热切换失败 (重启可收敛到目标态)
 *   503 (config_store_unavailable) — 网关以 yaml 模式启动, 只读不可写
 */
export async function setUpstreamEnabled(
  name: string,
  enabled: boolean,
  version: ConfigRevision,
): Promise<UpstreamToggleResult> {
  const { data } = await http.put<UpstreamToggleResult>(
    `/api/config/upstreams/${encodeURIComponent(name)}/enabled`,
    { enabled, version },
  )
  return data
}

// ---------------------------------------------------------------------------
// /api/config/overlays — config.db 薄覆盖层管理 (2026-09-18 P1)
// ---------------------------------------------------------------------------

/** 单行配置覆盖 (与后端 OverlayRow 对齐)。 */
export interface ConfigOverlayRow {
  /** 点路径, 如 upstreams.THS-LOCAL-GATEWAY.enabled / fuyao_cache.tools.get_x */
  path: string
  /** 覆盖值 (JSON 反序列化后的原始类型: bool / number / string / object)。 */
  value: unknown
  /** 该行最后写入时的全局 revision。 */
  revision: number
  /** UTC ISO 时间字符串。 */
  updated_at: string
}

export interface ConfigOverlayList {
  revision: number
  overlays: ConfigOverlayRow[]
}

export interface OverlayMutationResult {
  version: ConfigRevision
  /** 运行时热收敛告警 (如 enabled 行删除后运行时未注入 — 重启后生效)。 */
  warnings: string[]
}

/** GET /api/config/overlays — 全量覆盖行 + 当前 revision。503 = yaml 逃生模式。 */
export async function getConfigOverlays(): Promise<ConfigOverlayList> {
  const { data } = await http.get<ConfigOverlayList>('/api/config/overlays')
  return data
}

/**
 * DELETE /api/config/overlays/{path} — 删除单行覆盖 (恢复该路径出厂值)。
 *
 * ``upstreams.<name>.enabled`` 行删除后后端会立即按出厂值做运行时热收敛;
 * 其他路径重启或下次读时生效。409 版本冲突 / 400 路径非法。
 */
export async function deleteConfigOverlay(
  path: string,
  version: ConfigRevision,
): Promise<OverlayMutationResult & { path: string }> {
  const { data } = await http.delete<OverlayMutationResult & { path: string }>(
    `/api/config/overlays/${encodeURIComponent(path)}`,
    { data: { version } },
  )
  return data
}

/** POST /api/config/overlays/reset — 一键清空全部覆盖行 (整体恢复出厂)。 */
export async function resetConfigOverlays(
  version: ConfigRevision,
): Promise<OverlayMutationResult> {
  const { data } = await http.post<OverlayMutationResult>(
    '/api/config/overlays/reset',
    { version },
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
