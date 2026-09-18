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

/**
 * 提交写请求时取 revision: 可用则原样回带 (含 0), 否则回落空串
 * (后端语义: 空串 = 不校验乐观锁)。
 */
export function revisionOrSkip(v: unknown): ConfigRevision | '' {
  return isUsableRevision(v) ? v : ''
}
