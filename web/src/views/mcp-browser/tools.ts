/**
 * MCP 浏览器纯函数 + 常量。
 *
 * 不依赖 Vue, 便于单测 —— 本仓库的 vitest 刻意不引入 @vitejs/plugin-vue
 * (见 web/vitest.config.ts 顶部注释), 也无法挂载 .vue, 所以页面里所有
 * 可判断的逻辑都必须落在这里, 组件只做渲染与转发。
 *
 * 全部函数接收原始 tool 对象 (Record<string, unknown>) 并返回基本类型。
 * 这是 "不归一化原始 payload 也能把界面做清楚" 的实现方式。
 */
import type { RawTool, RawToolsResponse } from '@/api/mcp-browser'

/** 展示顺序; 未收录的上游按名称追加在后。 */
export const UPSTREAM_ORDER = [
  'fuyao_ashare',
  'fuyao_index',
  'fuyao_meta',
  'fuyao_fund',
  'THS-LOCAL-GATEWAY',
] as const

export const UPSTREAM_TITLE: Record<string, string> = {
  fuyao_ashare: '同花顺 a-share',
  fuyao_index: '同花顺 a-share-index',
  fuyao_meta: '同花顺 meta',
  fuyao_fund: '同花顺 fund',
  'THS-LOCAL-GATEWAY': 'FQGate 本地网关',
}

/** 界面专门渲染过的字段; 之外的字段靠 extraKeys 提示 + 原始 JSON 兜底。 */
export const KNOWN_TOOL_KEYS = [
  'name',
  'title',
  'description',
  'inputSchema',
  'outputSchema',
  'annotations',
  '_meta',
]

/** MCP 规范里的 annotations 提示; 未知 key 会追加在后, 不丢。 */
export const ANNOTATION_LABELS: Record<string, string> = {
  readOnlyHint: '只读',
  destructiveHint: '破坏性',
  idempotentHint: '幂等',
  openWorldHint: '开放域',
}

export interface BrowserToggles {
  meta: boolean
  outputSchema: boolean
  annotations: boolean
}

export interface SchemaParam {
  name: string
  type: string
  required: boolean
  description: string
  enumValues: string[]
}

export interface AnnotationFlag {
  key: string
  label: string
  value: boolean
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function toolName(tool: RawTool): string {
  return typeof tool.name === 'string' ? tool.name : ''
}

export function toolTitle(tool: RawTool): string {
  return typeof tool.title === 'string' ? tool.title : ''
}

export function toolDescription(tool: RawTool): string {
  return typeof tool.description === 'string' ? tool.description : ''
}

/**
 * el-table 的 row-key; 上游理论上可能发没有 name 的对象, 不能让它撞 key。
 *
 * 不能用数组下标: el-table 的 row-key 回调只收到 row 一个参数 (拿不到 index),
 * 多个无名工具会全部坍缩成同一个 `__row_undefined`, 展开任一行会联动错乱。
 * 用 WeakMap 给每个**对象身份**发一个稳定序号 —— 同一次拉取内 row 引用不变
 * (filter 不拷贝), 重新拉取后旧 id 随对象一起被 GC。
 */
const anonRowIds = new WeakMap<RawTool, number>()
let anonRowSeq = 0

export function rowKeyOf(tool: RawTool): string {
  const name = toolName(tool)
  if (name) return name
  let id = anonRowIds.get(tool)
  if (id === undefined) {
    id = ++anonRowSeq
    anonRowIds.set(tool, id)
  }
  return `__row_${id}`
}

/** inputSchema.required 归一成字符串数组 (缺失/类型不对一律当空)。 */
export function requiredParams(tool: RawTool): string[] {
  const schema = tool.inputSchema
  if (!isRecord(schema)) return []
  const required = schema.required
  if (!Array.isArray(required)) return []
  return required.filter((v): v is string => typeof v === 'string')
}

/**
 * 把 inputSchema.properties 投影成表格用的行 —— 只读, 不改写原始对象。
 * 对畸形输入 (inputSchema 是字符串 / properties 是 null / 属性值是字符串) 一律
 * 降级而不是抛错, 因为这是上游数据, 形状不完全可控。
 */
export function schemaProperties(tool: RawTool): SchemaParam[] {
  const schema = tool.inputSchema
  if (!isRecord(schema)) return []
  const properties = schema.properties
  if (!isRecord(properties)) return []

  const required = requiredParams(tool)
  return Object.entries(properties).map(([name, raw]) => {
    const info = isRecord(raw) ? raw : {}
    return {
      name,
      type: typeof info.type === 'string' ? info.type : '',
      required: required.includes(name),
      description: typeof info.description === 'string' ? info.description : '',
      enumValues: Array.isArray(info.enum) ? info.enum.map((v) => String(v)) : [],
    }
  })
}

export function hasOutputSchema(tool: RawTool): boolean {
  return isRecord(tool.outputSchema)
}

export function hasMeta(tool: RawTool): boolean {
  return tool._meta !== undefined && tool._meta !== null
}

export function hasAnnotations(tool: RawTool): boolean {
  return isRecord(tool.annotations)
}

/**
 * 原始 payload 里界面没有专门列的字段。
 *
 * 这不是对账 —— 只是提示 "还有别的东西", 它们全都能在下面的原始 JSON 里看到。
 */
export function extraKeys(tool: RawTool): string[] {
  return Object.keys(tool)
    .filter((k) => !KNOWN_TOOL_KEYS.includes(k))
    .sort()
}

/** annotations 提示; 已知 4 个按白名单顺序在前, 未知 key 排序后追加。 */
export function annotationFlags(tool: RawTool): AnnotationFlag[] {
  const annotations = tool.annotations
  if (!isRecord(annotations)) return []

  const known = Object.keys(ANNOTATION_LABELS)
  const extra = Object.keys(annotations)
    .filter((k) => !known.includes(k))
    .sort()

  return [...known, ...extra]
    .filter((k) => k in annotations)
    .map((k) => ({
      key: k,
      label: ANNOTATION_LABELS[k] ?? k,
      value: Boolean(annotations[k]),
    }))
}

export function hasActiveFilters(query: string, toggles: BrowserToggles): boolean {
  return Boolean(query.trim()) || Object.values(toggles).some(Boolean)
}

/** 搜索 (name/title/description) + 3 个形状开关。 */
export function applyToolFilter(
  tools: RawTool[],
  query: string,
  toggles: BrowserToggles,
): RawTool[] {
  let out = tools
  const q = query.trim().toLowerCase()
  if (q) {
    out = out.filter(
      (t) =>
        toolName(t).toLowerCase().includes(q) ||
        toolTitle(t).toLowerCase().includes(q) ||
        toolDescription(t).toLowerCase().includes(q),
    )
  }
  if (toggles.meta) out = out.filter(hasMeta)
  if (toggles.outputSchema) out = out.filter(hasOutputSchema)
  if (toggles.annotations) out = out.filter(hasAnnotations)
  return out
}

export function formatToolJson(tool: RawTool): string {
  return JSON.stringify(tool, null, 2)
}

/**
 * 真实字节数。
 *
 * 不能用 text.length —— 它数的是 UTF-16 码元, 而中文描述占 3 字节, 会少算约
 * 3 倍, 于是详情面板的体积守卫形同虚设。
 */
export function jsonBytes(text: string): number {
  return new TextEncoder().encode(text).length
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 表头摘要, 例如 "87 个工具 · 3 页 · 108.3 KB" */
export function summarizePayload(response: RawToolsResponse): string {
  return [
    `${response.count} 个工具`,
    `${response.pages} 页`,
    formatBytes(response.bytes),
  ].join(' · ')
}
