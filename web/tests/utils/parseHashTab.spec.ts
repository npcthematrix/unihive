import { describe, expect, it } from 'vitest'
import { parseHashTab } from '@/views/_shared/hashTab'

type Tab = 'a' | 'b' | 'c'
const VALID: readonly Tab[] = ['a', 'b', 'c']

describe('parseHashTab', () => {
  it('有效字符串 → 命中', () => {
    expect(parseHashTab('a', VALID, 'a')).toBe('a')
    expect(parseHashTab('b', VALID, 'a')).toBe('b')
  })

  it('无效字符串 → fallback', () => {
    expect(parseHashTab('__proto__', VALID, 'a')).toBe('a')
    expect(parseHashTab('zzz', VALID, 'c')).toBe('c')
  })

  it('空字符串 → fallback', () => {
    expect(parseHashTab('', VALID, 'a')).toBe('a')
  })

  it('数组形式取 [0]', () => {
    expect(parseHashTab(['b', 'c'], VALID, 'a')).toBe('b')
    expect(parseHashTab(['__proto__', 'b'], VALID, 'a')).toBe('a') // [0] 无效 → fallback
  })

  it('空数组 → fallback (q[0] = undefined → 非字符串 → "")', () => {
    expect(parseHashTab([], VALID, 'a')).toBe('a')
  })

  it('null/undefined → fallback', () => {
    expect(parseHashTab(null, VALID, 'a')).toBe('a')
    expect(parseHashTab(undefined, VALID, 'a')).toBe('a')
  })

  it('数字/对象/布尔 → fallback (非字符串)', () => {
    expect(parseHashTab(42, VALID, 'a')).toBe('a')
    expect(parseHashTab({ foo: 'b' }, VALID, 'a')).toBe('a')
    expect(parseHashTab(true, VALID, 'a')).toBe('a')
  })

  it('原型注入防御: valid 不包含 __proto__', () => {
    expect(parseHashTab('__proto__', VALID, 'c')).toBe('c')
  })
})
