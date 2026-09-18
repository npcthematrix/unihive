import { describe, expect, it } from 'vitest'
import { isUsableRevision, revisionOrSkip } from '@/views/config/revisions'

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

describe('revisionOrSkip', () => {
  it('passes usable revisions through (including 0)', () => {
    expect(revisionOrSkip(0)).toBe(0)
    expect(revisionOrSkip(3)).toBe(3)
    expect(revisionOrSkip('abc123')).toBe('abc123')
  })

  it('falls back to empty string (backend: skip optimistic check)', () => {
    expect(revisionOrSkip(undefined)).toBe('')
    expect(revisionOrSkip(null)).toBe('')
    expect(revisionOrSkip('')).toBe('')
  })
})
