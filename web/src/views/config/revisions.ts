import type { ConfigRevision } from '@/api/config'

/**
 * 判断一个值是否可作为乐观锁 revision 回带给后端。
 *
 * 注意 revision 0 是合法初值 (config.db 全新库), 不能用真值判断
 * (`v || ''` 会把 0 吞成空串, 而后端把空串理解为"跳过乐观锁校验")。
 */
export function isUsableRevision(v: unknown): v is ConfigRevision {
  if (typeof v === 'number') return Number.isFinite(v)
  return typeof v === 'string' && v !== ''
}
