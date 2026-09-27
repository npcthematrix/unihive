import { describe, expect, it } from 'vitest'
import { isUsableRevision } from '@/views/config/revisions'

describe('isUsableRevision', () => {
  it('accepts finite numbers including zero', () => {
    // 关键: revision 0 是全新 config.db 的合法初值
    expect(isUsableRevision(0)).toBe(true)
    expect(isUsableRevision(1)).toBe(true)
    expect(isUsableRevision(42)).toBe(true)
  })

  it('accepts non-empty strings (yaml 模式文件 hash)', () => {
    expect(isUsableRevision('7cc0cccaae05872c')).toBe(true)
  })

  it('rejects empty string, nullish, NaN, Infinity, booleans', () => {
    expect(isUsableRevision('')).toBe(false)
    expect(isUsableRevision(undefined)).toBe(false)
    expect(isUsableRevision(null)).toBe(false)
    expect(isUsableRevision(Number.NaN)).toBe(false)
    expect(isUsableRevision(Number.POSITIVE_INFINITY)).toBe(false)
    expect(isUsableRevision(true)).toBe(false)
    expect(isUsableRevision({})).toBe(false)
  })
})

/**
 * revisionOrSkip 已在 3f5c625 死代码清扫中删除 (ConfigView.vue 重构后
 * 唯一调用点用 ``v || ''`` 直接内联, 行为保持一致; 见 revisions.ts 头注释)。
 * 删除对应 describe 块, 不再保留死测试。
 */
