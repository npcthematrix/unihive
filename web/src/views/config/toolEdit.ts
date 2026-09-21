/**
 * 工具实体热编辑 — 表单模型 / 白名单 patch 构建 / 客户端预校验 (P3-4)。
 *
 * 纯函数不依赖 Vue, 便于 vitest; 后端 entity_edit.py 是唯一权威,
 * 这里的正则/字段分层必须与其保持一致 (常量上方标注了对应来源)。
 */
import { stableJson } from './jsonCompare'
import type {
  ToolEntity,
  ToolEntityParam,
  ToolEditPatch,
} from '@/api/config'

/**
 * 契约层字段 (后端 entity_edit.TOOL_CONTRACT_FIELDS): 改变对外 /mcp 契约,
 * 需 confirm_dangerous + lint + 热重注册。顺序即确认框展示顺序。
 */
export const TOOL_CONTRACT_FIELDS = [
  'name',
  'routing',
  'requires_env',
  'mapping',
] as const
export type ToolContractField = (typeof TOOL_CONTRACT_FIELDS)[number]

const CONTRACT_SET: ReadonlySet<string> = new Set(TOOL_CONTRACT_FIELDS)

/** 字段是否属于契约层 (描述层之外的可写字段均为描述层, 直存即热生效)。 */
export function isContractField(field: string): boolean {
  return CONTRACT_SET.has(field)
}

/** patch 触及的契约层字段 (空数组 = 纯描述层编辑, 无需强确认)。 */
export function contractFieldsInPatch(patch: ToolEditPatch): ToolContractField[] {
  return TOOL_CONTRACT_FIELDS.filter((f) =>
    Object.prototype.hasOwnProperty.call(patch, f),
  )
}

/** 后端 entity_edit._TOOL_NAME_RE (对外 MCP 工具名)。 */
const TOOL_NAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/
/** 后端 entity_edit._ENV_VAR_RE。 */
const ENV_VAR_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

export function isValidToolName(v: string): boolean {
  return TOOL_NAME_RE.test(v)
}

/** 空串 = 清除 requires_env (null), 合法; 否则须是合法环境变量名。 */
export function isValidEnvVarName(v: string): boolean {
  return v === '' || ENV_VAR_RE.test(v)
}

// ---------------------------------------------------------------------------
// 表单模型
// ---------------------------------------------------------------------------

/** params[] 单行的表单态 (enum 用 JSON 文本编辑, '' = 无 enum)。 */
export interface ParamForm {
  name: string
  type: string
  required: boolean
  description: string
  enumText: string
  /** 实体里后端放行但表单不显式编辑的键 (default 等), 行未改名时原样带回。 */
  extra: Record<string, unknown>
}

/** mapping 单行: {上游名: 上游方法}。 */
export interface MappingForm {
  upstream: string
  method: string
}

export interface ToolEditForm {
  name: string
  routing: string
  /** '' = null (清除 requires_env)。 */
  requiresEnv: string
  mapping: MappingForm[]
  description: string
  /** '' = null (清除 cache_ttl_key)。 */
  cacheTtlKey: string
  /** publish_mode === 'hidden'。 */
  publishHidden: boolean
  params: ParamForm[]
}

/** spec.params 可能是裸字符串 (旧 YAML) — 归一化为表单行。 */
export function paramsToForm(
  params: ToolEntityParam[] | Array<string | ToolEntityParam> | undefined,
): ParamForm[] {
  if (!Array.isArray(params)) return []
  return params.map((p) => {
    if (typeof p === 'string') return blankParam(p)
    return {
      name: String(p.name ?? ''),
      type: String(p.type ?? ''),
      required: p.required === true,
      description: String(p.description ?? ''),
      enumText: Array.isArray(p.enum) ? JSON.stringify(p.enum) : '',
      extra: extraParamKeys(p),
    }
  })
}

function blankParam(name = ''): ParamForm {
  return { name, type: '', required: false, description: '', enumText: '', extra: {} }
}

const KNOWN_PARAM_KEYS = new Set(['name', 'type', 'required', 'description', 'enum'])

function extraParamKeys(p: ToolEntityParam): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (!KNOWN_PARAM_KEYS.has(k)) out[k] = v
  }
  return out
}

/** 实体行 → 编辑表单初值。params 取自 spec.params (装配事实源)。 */
export function toolFormFromEntity(tool: ToolEntity): ToolEditForm {
  const specParams = tool.spec.params as
    | Array<string | ToolEntityParam>
    | undefined
  return {
    name: tool.name,
    routing: tool.routing,
    requiresEnv: tool.requires_env ?? '',
    mapping: Object.entries(tool.mapping || {}).map(([upstream, method]) => ({
      upstream,
      method: String(method),
    })),
    description: tool.description ?? '',
    cacheTtlKey: tool.cache_ttl_key ?? '',
    publishHidden: tool.publish_mode === 'hidden',
    params: paramsToForm(specParams),
  }
}

// ---------------------------------------------------------------------------
// 表单 → patch (只含相对实体真正变化的字段)
// ---------------------------------------------------------------------------

export interface BuiltParams {
  value?: ToolEntityParam[]
  errors: string[]
}

/**
 * 表单参数行 → 后端 params[]:
 * - 全空行 (未填写) 忽略;
 * - enumText 非空时必须是 JSON 数组 (string/number);
 * - 重名/缺名报错;
 * - type/description 留空不下发该键。
 */
export function paramsFromForm(rows: ParamForm[]): BuiltParams {
  const errors: string[] = []
  const value: ToolEntityParam[] = []
  const seen = new Set<string>()
  rows.forEach((row, i) => {
    const pos = i + 1
    const isBlank =
      !row.name.trim() &&
      !row.type.trim() &&
      !row.description.trim() &&
      !row.enumText.trim()
    if (isBlank) return
    const name = row.name.trim()
    if (!name) {
      errors.push(`参数第 ${pos} 行缺少名称`)
      return
    }
    if (seen.has(name)) {
      errors.push(`参数名重复: ${name}`)
      return
    }
    seen.add(name)
    const param: ToolEntityParam = { name, required: row.required === true }
    const type = row.type.trim()
    if (type) param.type = type
    const description = row.description.trim()
    if (description) param.description = description
    if (row.enumText.trim()) {
      const parsed = parseEnumText(row.enumText, pos, errors)
      if (parsed !== undefined) param.enum = parsed
    }
    for (const [k, v] of Object.entries(row.extra)) {
      if (!(k in param)) param[k] = v
    }
    value.push(param)
  })
  return errors.length ? { errors } : { value, errors }
}

function parseEnumText(
  text: string,
  pos: number,
  errors: string[],
): Array<string | number> | undefined {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    errors.push(`参数第 ${pos} 行 enum 不是合法 JSON`)
    return undefined
  }
  if (
    !Array.isArray(parsed) ||
    parsed.some((v) => typeof v === 'boolean' || (typeof v !== 'string' && typeof v !== 'number'))
  ) {
    errors.push(`参数第 ${pos} 行 enum 必须是字符串/数字的 JSON 数组`)
    return undefined
  }
  return parsed as Array<string | number>
}

export interface BuiltMapping {
  value?: Record<string, string>
  errors: string[]
}

/** 表单 mapping 行 → {上游: 方法}; 全空行忽略; 缺一侧/重名报错。 */
export function mappingFromForm(rows: MappingForm[]): BuiltMapping {
  const errors: string[] = []
  const value: Record<string, string> = {}
  rows.forEach((row, i) => {
    const pos = i + 1
    const upstream = row.upstream.trim()
    const method = row.method.trim()
    if (!upstream && !method) return
    if (!upstream || !method) {
      errors.push(`映射第 ${pos} 行需要同时填写上游名与上游方法`)
      return
    }
    if (Object.prototype.hasOwnProperty.call(value, upstream)) {
      errors.push(`映射第 ${pos} 行上游重复: ${upstream}`)
      return
    }
    value[upstream] = method
  })
  return errors.length ? { errors } : { value, errors }
}

/** 表单整体预校验 (后端 lint 是最终闸门, 这里只挡低级形态错误)。 */
export function validateToolForm(form: ToolEditForm): string[] {
  const errors: string[] = []
  const name = form.name.trim()
  if (!name) errors.push('工具名不能为空')
  else if (!isValidToolName(name)) {
    errors.push('工具名须以字母开头, 只含字母/数字/下划线')
  }
  if (!form.routing.trim()) errors.push('routing 不能为空')
  if (!isValidEnvVarName(form.requiresEnv.trim())) {
    errors.push('requires_env 须是合法环境变量名 (字母/下划线开头), 或留空')
  }
  errors.push(...mappingFromForm(form.mapping).errors)
  errors.push(...paramsFromForm(form.params).errors)
  return errors
}

/**
 * 实体 params (可能含裸字符串/缺 required) 的归一化比较形态。
 * 走与表单完全相同的 toForm → fromForm 管线, 保证"未动参数"不产生 patch
 * (裸字符串归一为 {name}, 省略的 required:false/空 description 不参与差异)。
 */
function canonicalParams(entity: ToolEntity): ToolEntityParam[] {
  const raw = entity.spec.params as
    | Array<string | ToolEntityParam>
    | undefined
  return paramsFromForm(paramsToForm(raw)).value ?? []
}

export interface BuiltToolPatch {
  patch: ToolEditPatch
  contractFields: ToolContractField[]
  errors: string[]
}

/**
 * 对比表单与实体, 构建**只含净变化字段**的 patch + 触及的契约字段清单。
 * 无任何变化时 patch 为空对象 (调用方按 no-op 处理, 不发请求)。
 */
export function buildToolPatch(
  form: ToolEditForm,
  entity: ToolEntity,
): BuiltToolPatch {
  const errors = validateToolForm(form)
  if (errors.length) return { patch: {}, contractFields: [], errors }

  const patch: ToolEditPatch = {}
  const name = form.name.trim()
  if (name !== entity.name) patch.name = name

  const routing = form.routing.trim()
  if (routing !== entity.routing) patch.routing = routing

  const requiresEnv = form.requiresEnv.trim() || null
  if (requiresEnv !== (entity.requires_env ?? null)) {
    patch.requires_env = requiresEnv
  }

  const mappingBuilt = mappingFromForm(form.mapping)
  if (mappingBuilt.value && stableJson(mappingBuilt.value) !== stableJson(entity.mapping || {})) {
    patch.mapping = mappingBuilt.value
  }

  const description = form.description
  if (description !== (entity.description ?? '')) patch.description = description

  const cacheTtlKey = form.cacheTtlKey.trim() || null
  if (cacheTtlKey !== (entity.cache_ttl_key ?? null)) {
    patch.cache_ttl_key = cacheTtlKey
  }

  const publishMode = form.publishHidden ? 'hidden' : 'default'
  if (publishMode !== entity.publish_mode) patch.publish_mode = publishMode

  const paramsBuilt = paramsFromForm(form.params)
  if (
    paramsBuilt.value &&
    stableJson(paramsBuilt.value) !== stableJson(canonicalParams(entity))
  ) {
    patch.params = paramsBuilt.value
  }

  return { patch, contractFields: contractFieldsInPatch(patch), errors: [] }
}
