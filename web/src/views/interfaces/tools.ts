/**
 * 接口页纯函数 + 常量 (与旧 console.js 行为保持一致)。
 * 不依赖 Vue, 便于单测和在组件间复用。
 */
import type { DataSourceType, ToolParam, ToolSpec } from '@/api/interfaces'

export const ALL_KEY = '__all__'

/** 旧版固定分组顺序; 新增上游 (thsdk/omni) 追加在后。 */
export const KNOWN_GROUP_ORDER = [
  'tdx_quant',
  'mootdx2',
  'fuyao_ashare',
  'fuyao_index',
  'fuyao_meta',
  'fuyao_fund',
] as const

/** 分组展示名; 未收录的分组回退原始 key。 */
export const GROUP_TITLE: Record<string, string> = {
  [ALL_KEY]: '全部',
  tdx_quant: '通达信-量化终端',
  mootdx2: 'MooTDX2',
  fuyao_ashare: '同花顺-a-share',
  fuyao_index: '同花顺-a-share-index',
  fuyao_meta: '同花顺-meta',
  fuyao_fund: '同花顺-fund',
  thsdk: 'THSDK 本地终端',
  omni: 'OMNIDATA 本地库',
}

export interface ToolToggles {
  dangerous: boolean
  cache: boolean
  noparam: boolean
}

/** '?' 是 YAML 里"未知参数"的占位, 计数/展示都忽略。 */
export function isParamPlaceholder(p: ToolParam): boolean {
  return typeof p === 'string' && p === '?'
}

export function paramName(p: ToolParam): string {
  if (typeof p === 'string') return p
  return p.name || ''
}

/** 无参数: 空、或仅含一个 '?' 占位。 */
export function hasNoParams(tool: ToolSpec): boolean {
  const params = tool.params || []
  return params.length === 0 || (params.length === 1 && isParamPlaceholder(params[0]))
}

export function hasCache(tool: ToolSpec): boolean {
  return Boolean(tool.cache_ttl_key) && tool.cache_ttl_key !== 'realtime_quote'
}

/** 表格行内的参数预览名列表 (已过滤占位)。 */
export function previewParamNames(tool: ToolSpec): string[] {
  return (tool.params || [])
    .filter((p) => !isParamPlaceholder(p))
    .map(paramName)
    .filter(Boolean)
}

export function hasActiveFilters(
  query: string,
  toggles: ToolToggles,
  dataSource: string,
): boolean {
  return Boolean(query) || Object.values(toggles).some(Boolean) || Boolean(dataSource)
}

/** 搜索 (name/description/source) + 3 个开关 + 数据源 chip, 语义同旧版。 */
export function applyToolsFilter(
  tools: ToolSpec[],
  query: string,
  toggles: ToolToggles,
  dataSource: DataSourceType | '',
): ToolSpec[] {
  let out = tools
  const q = query.trim().toLowerCase()
  if (q) {
    out = out.filter(
      (t) =>
        (t.name || '').toLowerCase().includes(q) ||
        (t.description || '').toLowerCase().includes(q) ||
        (t.source || '').toLowerCase().includes(q),
    )
  }
  if (toggles.dangerous) out = out.filter((t) => t.dangerous)
  if (toggles.cache) out = out.filter(hasCache)
  if (toggles.noparam) out = out.filter(hasNoParams)
  if (dataSource) out = out.filter((t) => t.data_source_type === dataSource)
  return out
}

/** 按主数据源 (source) 分桶。 */
export function bucketByGroup(tools: ToolSpec[]): Map<string, ToolSpec[]> {
  const buckets = new Map<string, ToolSpec[]>()
  for (const tool of tools) {
    const key = tool.source || ''
    const bucket = buckets.get(key)
    if (bucket) bucket.push(tool)
    else buckets.set(key, [tool])
  }
  return buckets
}

/** 已知顺序在前 (仅保留实际出现的), 未知 source 按首次出现追加。 */
export function buildGroupOrder(sources: string[]): string[] {
  const known = KNOWN_GROUP_ORDER as readonly string[]
  return [...known.filter((k) => sources.includes(k)), ...sources.filter((s) => !known.includes(s))]
}

/** 搜索时若当前分组已无匹配, 自动切到第一个有匹配的分组 (旧 maybeAutoSwitch)。 */
export function firstGroupWithMatch(
  buckets: Map<string, ToolSpec[]>,
  groupOrder: string[],
): string {
  return groupOrder.find((k) => (buckets.get(k) || []).length > 0) || ALL_KEY
}
