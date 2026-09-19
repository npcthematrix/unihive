/**
 * 「配置库」面板纯函数 (不依赖 Vue, 便于单测)。
 *
 * v2 配置事实源是 data/config.db 实体表; 每个实体行带 origin:
 *   - builtin: 出厂 YAML 模板 seed (空库自动导入)
 *   - user:    控制台写入 / OpenAPI 重导入 (P3/P4)
 */
import type { ConfigEntities, ConfigEntityOrigin } from '@/api/config'

/** 实体来源 → 中文标签。未知 origin 原样透传 (前向兼容)。 */
export function originLabel(origin: ConfigEntityOrigin): string {
  if (origin === 'builtin') return '出厂模板'
  if (origin === 'user') return '用户修改'
  return String(origin)
}

/** 实体来源 → el-tag type。 */
export function originTagType(origin: ConfigEntityOrigin): 'info' | 'warning' {
  return origin === 'builtin' ? 'info' : 'warning'
}

/** 出厂模板 hash 只展示前 12 位 (后端日志同款截断)。 */
export function shortTemplateVersion(version: string | null | undefined, len = 12): string {
  if (!version) return '—'
  return version.length <= len ? version : version.slice(0, len)
}

/** updated_at (UTC ISO) → 本地紧凑时间; 解析失败原样返回。 */
export function formatUpdatedAt(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** 类型守卫: db 实体模式响应。 */
export function isDbEntities(
  entities: ConfigEntities | null | undefined,
): entities is Extract<ConfigEntities, { source: 'db' }> {
  return !!entities && entities.source === 'db'
}

/**
 * 用户改动实体总数 (origin 非 builtin): upstream + section 行 + 工具按
 * origin 聚合的非 builtin 行数。tab 角标与面板摘要共用。
 */
export function userModifiedCount(entities: ConfigEntities | null | undefined): number {
  if (!isDbEntities(entities)) return 0
  const upstreamHits = entities.upstreams.filter((u) => u.origin !== 'builtin').length
  const sectionHits = entities.sections.filter((s) => s.origin !== 'builtin').length
  const toolHits = Object.entries(entities.tools.by_origin)
    .filter(([origin]) => origin !== 'builtin')
    .reduce((sum, [, n]) => sum + n, 0)
  return upstreamHits + sectionHits + toolHits
}
