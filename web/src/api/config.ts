import { http } from './client'

/**
 * 乐观锁版本号。
 *
 * 2026-09-18 v2 起配置全量解构入 data/config.db 为唯一事实源, 版本号是整数
 * revision (从 0 开始, 每次净变更 +1); 仅当网关以
 * UNIHIVE_CONFIG_SOURCE=yaml 逃生模式启动时回落到旧的 16 位文件 hash 字符串。
 * 前端两种都接受。
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
// /api/config/fuyao-cache — FUYAO 工具级缓存 TTL (config.db fuyao_cache 实体段)
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

  fuyao_cache 整段作为 config_section 行写入 config.db; 保存后热生效, 无需重启。

  Returns:
    { fuyao_cache, version } — 新 revision 必须让前端更新本地 state。
  Throws:
    409 (version_conflict) — 前端应提示刷新后重试
    400 (validation_failed) — payload 校验失败, 含后端 message
    503 (config_store_unavailable) — 网关以 yaml 逃生模式启动, 配置库不可用
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
// /api/config/entities — config.db v2 实体表只读浏览 (2026-09-18 P2)
// ---------------------------------------------------------------------------

/** 实体来源标记: builtin=出厂模板 seed, user=控制台/导入写入。 */
export type ConfigEntityOrigin = 'builtin' | 'user' | (string & {})

/** upstream 实体行的元数据摘要 (不含 config_json 连接参数)。 */
export interface ConfigEntityUpstream {
  name: string
  type: string
  enabled: boolean
  origin: ConfigEntityOrigin
  /** UTC ISO。 */
  updated_at: string
}

/** config_section 实体行的元数据摘要。 */
export interface ConfigEntitySection {
  section: string
  origin: ConfigEntityOrigin
  updated_at: string
}

/** GET /api/config/entities 响应。yaml 逃生模式只回 ``{ source: 'yaml' }``。 */
export type ConfigEntities =
  | {
      source: 'db'
      /** 全局整数乐观锁版本, 与 /api/config 的 version 同源。 */
      revision: number
      /** 出厂模板内容 hash (12+ 位), P3 出厂更新中心的三方合并基线。 */
      template_version: string | null
      template_imported_at: string | null
      upstreams: ConfigEntityUpstream[]
      sections: ConfigEntitySection[]
      tools: {
        total: number
        /** origin → 行数 (目前全 builtin; P4 OpenAPI 重导入会出现 user)。 */
        by_origin: Record<string, number>
      }
    }
  | { source: 'yaml' }

/** GET /api/config/entities — 实体表只读浏览摘要。 */
export async function getConfigEntities(): Promise<ConfigEntities> {
  const { data } = await http.get<ConfigEntities>('/api/config/entities')
  return data
}

// ---------------------------------------------------------------------------
// 实体编辑 (P3): GET detail / PUT tool / PUT upstream
// ---------------------------------------------------------------------------

/** 工具实体 params[] 单项 (DB 已归一化为对象形态; 裸字符串只存在于旧快照)。 */
export interface ToolEntityParam {
  name: string
  type?: string
  required?: boolean
  description?: string
  enum?: Array<string | number>
  [key: string]: unknown
}

/** publish_mode 取值: default=正常发布 (受启用上游/env 门控), hidden=手动下架。 */
export type ToolPublishMode = 'default' | 'hidden'

/** GET /api/config/tools/{name} 的完整工具实体行。 */
export interface ToolEntity {
  name: string
  routing: string
  description: string
  cache_ttl_key: string | null
  requires_env: string | null
  publish_mode: ToolPublishMode | string
  /** {上游名: 上游方法} — 装配期重建 routing 映射的来源。 */
  mapping: Record<string, string>
  /** 完整 spec (装配来源, 与抽取列保持同步)。 */
  spec: Record<string, unknown>
  source_file: string | null
  position: number
  origin: ConfigEntityOrigin
  /** UTC ISO。 */
  updated_at: string
}

/** 单工具白名单 patch (后端 entity_edit.TOOL_WRITABLE_FIELDS)。 */
export interface ToolEditPatch {
  name?: string
  routing?: string
  description?: string
  cache_ttl_key?: string | null
  requires_env?: string | null
  publish_mode?: ToolPublishMode | string
  mapping?: Record<string, string>
  params?: ToolEntityParam[]
}

/** PUT 工具成功响应 (changed=false = no-op, revision 不增)。 */
export interface ToolEditResult {
  changed: boolean
  /** 改名后为新名 (其余场景回原名)。 */
  name: string
  revision: number
  /** 编辑后该工具是否在对外 /mcp 发布。 */
  published: boolean
  /** 编辑前是否发布 (决定 provider 是否做过 remove)。 */
  previously_published: boolean
  /** 本次变更触及的契约层字段 (纯描述层编辑为 [])。 */
  contract_fields: string[]
}

export type ToolEntityDetail =
  | { source: 'db'; revision: number; tool: ToolEntity }
  | { source: 'yaml' }

/** GET /api/config/upstreams/{name} 的完整上游实体行。 */
export interface UpstreamEntity {
  name: string
  type: string
  enabled: boolean
  /**
   * 异型连接块实体原值 — 密钥字段是 ``${VAR}`` 占位符 (不脱敏)。
   * 不含列键 enabled/type (后端 400 拒绝)。
   */
  config: Record<string, unknown>
  origin: ConfigEntityOrigin
  updated_at: string
}

export type UpstreamEntityDetail =
  | { source: 'db'; revision: number; upstream: UpstreamEntity }
  | { source: 'yaml' }

/** PUT 上游连接块成功响应。 */
export interface UpstreamEditResult {
  changed: boolean
  name: string
  revision: number
  enabled: boolean
  /** 启用态热重连后 client 是否真的连上 (禁用态恒 false)。 */
  connected: boolean
  contract_fields: string[]
}

/** 422 lint_rejected 错误体里的单条 issue。 */
export interface LintIssueBody {
  severity: 'error' | 'warning' | string
  code: string
  message: string
}

/** GET /api/config/tools/{name} — 单工具实体详情 (编辑表单数据源)。 */
export async function getToolEntity(name: string): Promise<ToolEntityDetail> {
  const { data } = await http.get<ToolEntityDetail>(
    `/api/config/tools/${encodeURIComponent(name)}`,
  )
  return data
}

/**
 * PUT /api/config/tools/{name} — 白名单热编辑。
 *
 * 契约层字段 (name/routing/requires_env/mapping) 必须 confirmDangerous=true;
 * 否则 400 dangerous_confirmation_required (带 contract_fields)。
 * 409 revision_conflict / 422 lint_rejected (带 issues) / 503 yaml 模式。
 */
export async function updateToolEntity(
  name: string,
  patch: ToolEditPatch,
  opts: { confirmDangerous?: boolean; revision?: number } = {},
): Promise<ToolEditResult> {
  const body: Record<string, unknown> = { patch }
  if (opts.confirmDangerous) body.confirm_dangerous = true
  if (opts.revision !== undefined) body.revision = opts.revision
  const { data } = await http.put<ToolEditResult>(
    `/api/config/tools/${encodeURIComponent(name)}`,
    body,
  )
  return data
}

/** GET /api/config/upstreams/{name} — 单上游实体详情 (占位符原值)。 */
export async function getUpstreamEntity(name: string): Promise<UpstreamEntityDetail> {
  const { data } = await http.get<UpstreamEntityDetail>(
    `/api/config/upstreams/${encodeURIComponent(name)}`,
  )
  return data
}

/**
 * PUT /api/config/upstreams/{name} — 整体替换连接块 (可选改类型)。
 *
 * 建连契约始终要求 confirmDangerous=true; config 是完整连接块 (异型 dict,
 * 不含 enabled/type); 密钥只接受 ${VAR} 占位符, 明文 400 拒绝。
 */
export async function updateUpstreamConfigEntity(
  name: string,
  config: Record<string, unknown>,
  opts: { type?: string; confirmDangerous?: boolean; revision?: number } = {},
): Promise<UpstreamEditResult> {
  const body: Record<string, unknown> = { config }
  if (opts.type !== undefined) body.type = opts.type
  if (opts.confirmDangerous) body.confirm_dangerous = true
  if (opts.revision !== undefined) body.revision = opts.revision
  const { data } = await http.put<UpstreamEditResult>(
    `/api/config/upstreams/${encodeURIComponent(name)}`,
    body,
  )
  return data
}

// ---------------------------------------------------------------------------
// 出厂更新中心 (P3-7): template-status / upgrade dry-run+apply / backup
// ---------------------------------------------------------------------------

/** 单条字段冲突的裁决方式: 保留库内当前值 / 采用出厂新值。 */
export type ConflictResolution = 'keep_cur' | 'accept_new'

/** conflict key → 裁决, 键形如 "tool:foo_bar:description"。 */
export type ResolutionMap = Record<string, ConflictResolution>

/** GET /api/config/template-status (db 模式)。yaml 逃生模式只回 source。 */
export type FactoryTemplateStatus =
  | {
      source: 'db'
      revision: number | null
      /** 库内快照 (三方合并基线) hash。 */
      seeded_version: string | null
      /** 当前出厂 YAML 重建模板 hash; 构建失败为 null 并带 build_error。 */
      current_version: string | null
      template_changed: boolean
      factory_counts?: { upstreams: number; tools: number; sections: number }
      /** 出厂 YAML 语法错误/明文密钥等构建失败原因 (不外泄堆栈)。 */
      build_error?: string
    }
  | { source: 'yaml' }

/** GET /api/config/template-status — 出厂模板 hash 变化信号 (只给信号, 不自动升级)。 */
export async function getTemplateStatus(): Promise<FactoryTemplateStatus> {
  const { data } = await http.get<FactoryTemplateStatus>('/api/config/template-status')
  return data
}

/** 合并台账: 单个实体/配置段在本次合并中的处置。 */
export interface UpgradeEntityChange {
  kind: 'section' | 'upstream' | 'tool' | string
  name: string
  action: 'added' | 'removed' | 'updated' | 'kept_user' | 'unchanged' | string
  fields: string[]
  /** 该实体上发生冲突的字段 path 列表。 */
  conflicts: string[]
}

/** 字段级冲突三向值; old=合并基线, cur=库内当前, new=出厂新值 (缺失侧为 null)。 */
export interface UpgradeConflict {
  kind: string
  name: string
  path: string
  conflict: string
  /** 引擎在本次 dry-run 候选中实际采用的解法 (缺省 keep_cur)。 */
  resolution: ConflictResolution | string
  old: unknown
  cur: unknown
  new: unknown
}

/** 出厂更新对运行态的影响面 (dry-run 预测 / apply 实际, 同构)。 */
export interface UpgradeImpact {
  tools_added: string[]
  tools_removed: string[]
  tools_republished: string[]
  upstreams_attach: string[]
  upstreams_reconnect: string[]
  upstreams_detach: string[]
}

export interface UpgradeSummary {
  added: number
  removed: number
  updated: number
  kept_user: number
  conflicts: number
}

/** POST /api/config/upgrade/dry-run 响应: 台账 + 冲突 + lint + 运行态影响。 */
export interface UpgradePreview {
  changed: boolean
  revision: number
  old_version: string
  new_version: string
  template_changed: boolean
  summary: UpgradeSummary
  sections: UpgradeEntityChange[]
  upstreams_changes: UpgradeEntityChange[]
  tools_changes: UpgradeEntityChange[]
  conflicts: UpgradeConflict[]
  /** 服务端实际采纳的裁决 key (已校验形态)。 */
  resolutions_applied: string[]
  lint_errors: LintIssueBody[]
  lint_warnings: LintIssueBody[]
  impact: UpgradeImpact
}

/** POST /api/config/upgrade/apply 成功响应。changed=false no-op 时字段从简。 */
export interface UpgradeApplyResult {
  changed: boolean
  revision: number
  old_version: string
  new_version: string
  summary: UpgradeSummary
  sections?: UpgradeEntityChange[]
  upstreams_changes?: UpgradeEntityChange[]
  tools_changes?: UpgradeEntityChange[]
  resolutions_applied?: string[]
  lint_warnings?: LintIssueBody[]
  tools_added?: string[]
  tools_removed?: string[]
  tools_republished?: string[]
  upstreams_attached?: string[]
  upstreams_reconnected?: string[]
  upstreams_detached?: string[]
  /** 建连失败等非阻断警告 (工具仍按 enabled 发布, 与开机语义一致)。 */
  warnings?: string[]
  /** 升级前 JSON 备份绝对路径。 */
  backup_path?: string
}

/** 最近一份升级前备份的下载地址 (cookie 同源鉴权, 可直接挂 <a href>)。 */
export const CONFIG_UPGRADE_BACKUP_URL = '/api/config/upgrade/backup/latest'

/**
 * POST /api/config/upgrade/dry-run — 字段级三方合并预览 (零写入)。
 * 400 裁决形态错误/仍有未裁决冲突; 409 revision 冲突; 422 lint error; 503 yaml 模式。
 */
export async function previewConfigUpgrade(
  resolutions?: ResolutionMap,
): Promise<UpgradePreview> {
  const body = resolutions ? { resolutions } : {}
  const { data } = await http.post<UpgradePreview>('/api/config/upgrade/dry-run', body)
  return data
}

/**
 * POST /api/config/upgrade/apply — 热应用出厂更新 (后端要求 confirm:true)。
 *
 * 应用前服务端写 JSON 备份; 成功体带 backup_path。revision 用于乐观锁,
 * 建议回带 dry-run 返回的 revision, 409 时需重新检查更新。
 */
export async function applyConfigUpgrade(payload: {
  resolutions?: ResolutionMap
  revision?: number
}): Promise<UpgradeApplyResult> {
  const body: Record<string, unknown> = { confirm: true }
  if (payload.resolutions) body.resolutions = payload.resolutions
  if (payload.revision !== undefined) body.revision = payload.revision
  const { data } = await http.post<UpgradeApplyResult>('/api/config/upgrade/apply', body)
  return data
}

// ---------------------------------------------------------------------------
// FQGate OpenAPI 实时导入 (P4): /api/config/import/preview|apply
//
// 活拉 THS-LOCAL-GATEWAY 的 openapi.json (REST 契约权威) + tools/list, 在
// 服务端转成工具实体行, 以 origin='user' 热写 config.db 并热注册; apply 永不
// 删除工具 (removed 仅顾问提示), 不重连上游。
// ---------------------------------------------------------------------------

/** 导入台账里单个工具的处置。 */
export interface ImportToolChange {
  name: string
  action: 'added' | 'changed' | 'unchanged' | string
  /** 契约差异字段 (routing/description/mapping/spec), unchanged 为 []。 */
  changed_fields: string[]
}

/** 上游声明了但找不到 REST 路径的 MCP 工具 (跳过, 不进实体)。 */
export interface ImportUnmappedTool {
  name: string
  reason: string
}

export interface ImportSummary {
  added: number
  changed: number
  unchanged: number
  /** 顾问计数: 历史导入行在上游已消失 (apply 不会真的删行)。 */
  removed: number
  unmapped: number
}

/** 导入对运行态的影响面 (上游连接三键恒为 [], 导入只动工具)。 */
export interface ImportImpact {
  tools_added: string[]
  tools_removed: string[]
  tools_republished: string[]
  upstreams_attach: string[]
  upstreams_reconnect: string[]
  upstreams_detach: string[]
}

/** POST /api/config/import/preview 响应。 */
export interface OpenapiImportPreview {
  changed: boolean
  revision: number
  upstream: string
  summary: ImportSummary
  changes: ImportToolChange[]
  unmapped: ImportUnmappedTool[]
  removed: string[]
  /** 本次计划实际会写入的工具 (added+changed 与白名单的交)。 */
  selected: string[]
  lint_errors: LintIssueBody[]
  lint_warnings: LintIssueBody[]
  impact: ImportImpact
  /** 与 config/tools_ths_local.yaml 同形的 flow-YAML 评审片段。 */
  yaml_fragment: string
}

/** POST /api/config/import/apply 成功响应 (no-op 时仅 changed/revision/...)。 */
export interface OpenapiImportApplyResult {
  changed: boolean
  revision: number
  upstream: string
  summary: ImportSummary
  changes?: ImportToolChange[]
  unmapped?: ImportUnmappedTool[]
  removed?: string[]
  selected?: string[]
  lint_warnings?: LintIssueBody[]
  tools_added?: string[]
  tools_removed?: string[]
  tools_republished?: string[]
  upstreams_attached?: string[]
  upstreams_reconnected?: string[]
  upstreams_detached?: string[]
  warnings?: string[]
  backup_path?: string
  yaml_fragment?: string
}

/**
 * POST /api/config/import/preview — 活拉上游 OpenAPI 生成导入计划 (零写入)。
 *
 * tools 非空时为白名单: 只评估给定工具 (必须都在 added/changed 集, 否则 400)。
 * 502 openapi_fetch_failed=上游没起/拉取失败; 503=yaml 逃生模式或上游未连接;
 * 404=上游未注册; 422=lint error。
 */
export async function previewOpenapiImport(opts: {
  upstream?: string
  tools?: string[]
} = {}): Promise<OpenapiImportPreview> {
  const body: Record<string, unknown> = {}
  if (opts.upstream !== undefined) body.upstream = opts.upstream
  if (opts.tools !== undefined) body.tools = opts.tools
  const { data } = await http.post<OpenapiImportPreview>(
    '/api/config/import/preview',
    body,
  )
  return data
}

/**
 * POST /api/config/import/apply — 热应用导入 (契约层强确认)。
 *
 * 固定带 confirm_dangerous=true; revision 建议回带 preview 的 revision,
 * 409 revision_conflict 时须重新 preview。
 */
export async function applyOpenapiImport(payload: {
  upstream?: string
  tools?: string[]
  revision?: number
}): Promise<OpenapiImportApplyResult> {
  const body: Record<string, unknown> = { confirm_dangerous: true }
  if (payload.upstream !== undefined) body.upstream = payload.upstream
  if (payload.tools !== undefined) body.tools = payload.tools
  if (payload.revision !== undefined) body.revision = payload.revision
  const { data } = await http.post<OpenapiImportApplyResult>(
    '/api/config/import/apply',
    body,
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
