/**
 * FQGate OpenAPI 导入对话框纯函数 (不依赖 Vue, 被 vitest 覆盖)。
 *
 * 与 config/updateCenter.ts 同一分工: .vue 组件只做渲染与转发, 所有可判断的
 * 选择/门控/文案逻辑都沉在这里。后端语义见
 * src/unihive/core/openapi_import.py 与 /api/config/import/* 路由:
 *
 * - added/changed 才可选可写; unchanged 只展示; unmapped 是"上游有但找不
 *   到 REST 路径"的跳过清单; removed 只是顾问提示, apply 永远不删工具行;
 * - 收窄勾选 = 带 tools 白名单重新 preview, impact / YAML 片段都以最新
 *   preview 为准 (后端按白名单重算, 前端不自己切片段)。
 */
import type {
  ImportImpact,
  ImportToolChange,
  OpenapiImportPreview,
} from '@/api/config'

export const IMPORT_ACTION_ADDED = 'added'
export const IMPORT_ACTION_CHANGED = 'changed'
export const IMPORT_ACTION_UNCHANGED = 'unchanged'

/** 契约差异字段 → 中文标签 (后端 _SIGNATURE_FIELDS: routing/description/mapping/spec)。 */
export const CHANGED_FIELD_LABELS: Record<string, string> = {
  routing: '路由键',
  description: '描述',
  mapping: '上游映射',
  spec: '参数契约',
}

export function changedFieldLabel(field: string): string {
  return CHANGED_FIELD_LABELS[field] ?? field
}

export function isSelectableAction(action: string): boolean {
  return action === IMPORT_ACTION_ADDED || action === IMPORT_ACTION_CHANGED
}

/** 台账里可勾选导入的行 (保持后端 plan 顺序)。 */
export function selectableChanges(
  changes: ImportToolChange[],
): ImportToolChange[] {
  return changes.filter((c) => isSelectableAction(c.action))
}

/** 首次拿到计划时的默认勾选 = 全部 added/changed。 */
export function defaultSelection(preview: OpenapiImportPreview | null): string[] {
  if (!preview) return []
  return selectableChanges(preview.changes).map((c) => c.name)
}

/** 不可变地切换单个工具勾选, 返回按名字排序的新数组 (白名单顺序无关)。 */
export function toggleSelected(selected: string[], name: string): string[] {
  const next = new Set(selected)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  return [...next].sort()
}

export function isAllSelected(
  preview: OpenapiImportPreview | null,
  selected: string[],
): boolean {
  if (!preview) return false
  const names = selectableChanges(preview.changes).map((c) => c.name)
  return names.length > 0 && names.every((n) => selected.includes(n))
}

/**
 * apply 前置门 (与服务端复核对齐的纯前端体验门):
 * 勾选非空 + 无 lint error。勾选必须是本次 preview 可选集的子集。
 */
export function canApplyImport(
  preview: OpenapiImportPreview | null,
  selected: string[],
): boolean {
  if (!preview || selected.length === 0) return false
  if (preview.lint_errors.length > 0) return false
  const allowed = new Set(
    selectableChanges(preview.changes).map((c) => c.name),
  )
  return selected.every((n) => allowed.has(n))
}

export function actionLabel(action: string): string {
  switch (action) {
    case IMPORT_ACTION_ADDED:
      return '新增'
    case IMPORT_ACTION_CHANGED:
      return '契约变更'
    case IMPORT_ACTION_UNCHANGED:
      return '无变化'
    default:
      return action
  }
}

export function actionTagType(
  action: string,
): 'success' | 'warning' | 'info' | 'primary' | 'danger' {
  switch (action) {
    case IMPORT_ACTION_ADDED:
      return 'success'
    case IMPORT_ACTION_CHANGED:
      return 'warning'
    default:
      return 'info'
  }
}

/** 运行态影响面 → 展示行 (导入不碰上游连接, 只出工具三组; 空组不出)。 */
export interface ImportImpactGroup {
  key: keyof ImportImpact
  label: string
  names: string[]
  tone: 'success' | 'danger' | 'warning'
}

export function importImpactGroups(impact: ImportImpact): ImportImpactGroup[] {
  const groups: ImportImpactGroup[] = [
    { key: 'tools_added', label: '新发布到 /mcp', names: impact.tools_added, tone: 'success' },
    { key: 'tools_removed', label: '将从 /mcp 摘除', names: impact.tools_removed, tone: 'danger' },
    {
      key: 'tools_republished',
      label: '契约变更, 先摘后注',
      names: impact.tools_republished,
      tone: 'warning',
    },
  ]
  return groups.filter((g) => g.names.length > 0)
}

export function hasImportImpact(impact: ImportImpact): boolean {
  return importImpactGroups(impact).length > 0
}

/** 下载片段文件名: openapi-import-tools-YYYYMMDD-HHMMSS.yaml (本地时区)。 */
export function fragmentFilename(date: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const stamp =
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  return `openapi-import-tools-${stamp}.yaml`
}

/** 触发浏览器下载 (Blob, 不走任何额外请求; 片段内容来自 preview 响应)。 */
export function downloadText(filename: string, text: string, mime = 'text/yaml'): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** axios 错误是否是 409 revision_conflict (提示重新生成预览)。 */
export function isRevisionConflictError(error: unknown): boolean {
  const e = error as { response?: { status?: number } } | null
  return e?.response?.status === 409
}

/** 502 openapi_fetch_failed / upstream_unavailable: 上游侧问题 (没起/拒绝)。 */
export function isUpstreamFetchError(error: unknown): boolean {
  const e = error as { response?: { status?: number } } | null
  return e?.response?.status === 502
}
