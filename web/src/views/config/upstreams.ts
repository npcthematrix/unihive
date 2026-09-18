import type { UpstreamConfig } from '@/api/config'

/** 上游启用态判定。缺省 (enabled 字段缺失) 与后端一致视为禁用。 */
export function isUpstreamEnabled(cfg: unknown): boolean {
  return (
    typeof cfg === 'object' &&
    cfg !== null &&
    (cfg as UpstreamConfig).enabled === true
  )
}

/** 开关按钮文案: 当前启用 → 按钮动作为「禁用」, 反之「启用」。 */
export function toggleLabel(enabled: boolean): string {
  return enabled ? '禁用' : '启用'
}

/** UpstreamsPanel 热开关成功后向 ConfigView 冒泡的载荷。 */
export interface UpstreamEnabledChanged {
  name: string
  enabled: boolean
  /** 乐观锁新版本, ConfigView 必须回写到本地 config.version。 */
  version: string
  /** 启用时上游 client 是否真的连上 (禁用态恒为 false)。 */
  connected: boolean
  toolsAdded: string[]
  toolsRemoved: string[]
}
