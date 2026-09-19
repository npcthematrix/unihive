/**
 * 出厂更新中心纯函数 (不依赖 Vue, 便于单测)。
 *
 * 后端门控语义: apply 时每条字段冲突都必须在 resolutions 表里显式裁决
 * (key 缺失即 "未裁决", 400 拒绝; 显式 keep_cur 也算已裁决)。
 * 引擎 dry-run 候选对未裁决冲突默认按 keep_cur 合并, 这里据此做初始值,
 * 用户可逐条改判 accept_new, 也可批量裁决。
 */
import type {
  ConflictResolution,
  FactoryTemplateStatus,
  ResolutionMap,
  UpgradeConflict,
  UpgradeEntityChange,
  UpgradeImpact,
  UpgradePreview,
} from '@/api/config'

/** 冲突稳定标识, 与后端 FieldConflict.key 同构: "<kind>:<name>:<path>"。 */
export function conflictKey(c: {
  kind: string
  name: string
  path: string
}): string {
  return `${c.kind}:${c.name}:${c.path}`
}

export function isResolution(value: unknown): value is ConflictResolution {
  return value === 'keep_cur' || value === 'accept_new'
}

/** 单条冲突是否已被用户显式裁决 (值合法即算, keep_cur 也是裁决)。 */
export function isConflictDecided(
  c: UpgradeConflict,
  resolutions: ResolutionMap,
): boolean {
  return isResolution(resolutions[conflictKey(c)])
}

/** 所有尚未裁决的冲突 (apply 按钮置灰 + 逐条确认提示的依据)。 */
export function pendingConflicts(
  conflicts: UpgradeConflict[],
  resolutions: ResolutionMap,
): UpgradeConflict[] {
  return conflicts.filter((c) => !isConflictDecided(c, resolutions))
}

/**
 * dry-run 返回后初始化裁决表: 每条冲突都给显式初值, 采用引擎本次候选的
 * 实际解法 (合法值), 否则回落到 keep_cur。用户审阅时看到的就是候选当前态。
 */
export function seedResolutions(conflicts: UpgradeConflict[]): ResolutionMap {
  const seeded: ResolutionMap = {}
  for (const c of conflicts) {
    seeded[conflictKey(c)] = isResolution(c.resolution) ? c.resolution : 'keep_cur'
  }
  return seeded
}

/** 不可变地写入单条裁决。 */
export function withResolution(
  resolutions: ResolutionMap,
  key: string,
  choice: ConflictResolution,
): ResolutionMap {
  if (resolutions[key] === choice) return resolutions
  return { ...resolutions, [key]: choice }
}

/** 不可变地把所有冲突批量裁决为同一选择 (已有裁决也覆盖)。 */
export function decideAll(
  conflicts: UpgradeConflict[],
  choice: ConflictResolution,
): ResolutionMap {
  const next: ResolutionMap = {}
  for (const c of conflicts) next[conflictKey(c)] = choice
  return next
}

/**
 * apply 前置条件 (与后端门控对齐, 纯前端体验门, 后端仍会复核):
 * 有净变更、无 lint error、每条冲突都已裁决。
 */
export function canApplyUpgrade(
  preview: UpgradePreview | null,
  resolutions: ResolutionMap,
): boolean {
  if (!preview || !preview.changed) return false
  if (preview.lint_errors.length > 0) return false
  return pendingConflicts(preview.conflicts, resolutions).length === 0
}

/** 合并处置 → 中文标签; 未知 action 原样透传 (前向兼容)。 */
export function actionLabel(action: string): string {
  switch (action) {
    case 'added':
      return '新增'
    case 'removed':
      return '删除'
    case 'updated':
      return '更新'
    case 'kept_user':
      return '保留用户改动'
    case 'unchanged':
      return '无变化'
    default:
      return action
  }
}

/** 合并处置 → el-tag type。 */
export function actionTagType(
  action: string,
): 'primary' | 'success' | 'danger' | 'warning' | 'info' {
  switch (action) {
    case 'added':
      return 'success'
    case 'removed':
      return 'danger'
    case 'updated':
      return 'primary'
    case 'kept_user':
      return 'warning'
    default:
      return 'info'
  }
}

/** 实体类别 → 中文标签。 */
export function kindLabel(kind: string): string {
  if (kind === 'section') return '配置段'
  if (kind === 'upstream') return '上游'
  if (kind === 'tool') return '工具'
  return kind
}

/** 台账过滤掉 unchanged, 只留真正有处置的实体。 */
export function meaningfulChanges(
  changes: UpgradeEntityChange[],
): UpgradeEntityChange[] {
  return changes.filter((c) => c.action !== 'unchanged')
}

/** 冲突三向值的展示文本: null/undefined → —; 对象/数组 → 缩进 JSON。 */
export function formatConflictValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

/** 运行态影响面 → 展示行 (空清单不出行)。 */
export interface ImpactGroup {
  key: keyof UpgradeImpact
  label: string
  names: string[]
  /** danger=摘除/断开 (需要用户留意), success=新增, warning=重注册/重连。 */
  tone: 'success' | 'danger' | 'warning'
}

export function impactGroups(impact: UpgradeImpact): ImpactGroup[] {
  const groups: ImpactGroup[] = [
    { key: 'tools_added', label: '新发布工具', names: impact.tools_added, tone: 'success' },
    { key: 'tools_removed', label: '将摘除工具', names: impact.tools_removed, tone: 'danger' },
    {
      key: 'tools_republished',
      label: '契约变更重注册',
      names: impact.tools_republished,
      tone: 'warning',
    },
    {
      key: 'upstreams_attach',
      label: '新连接上游',
      names: impact.upstreams_attach,
      tone: 'success',
    },
    {
      key: 'upstreams_reconnect',
      label: '重连上游',
      names: impact.upstreams_reconnect,
      tone: 'warning',
    },
    {
      key: 'upstreams_detach',
      label: '断开上游',
      names: impact.upstreams_detach,
      tone: 'danger',
    },
  ]
  return groups.filter((g) => g.names.length > 0)
}

/** 影响面是否全空 (no-op 时不渲染影响区)。 */
export function hasImpact(impact: UpgradeImpact): boolean {
  return impactGroups(impact).length > 0
}

/** 类型守卫: template-status 的 db 响应。 */
export function isDbStatus(
  status: FactoryTemplateStatus | null,
): status is Extract<FactoryTemplateStatus, { source: 'db' }> {
  return !!status && status.source === 'db'
}
