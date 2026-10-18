import { describe, expect, test } from 'vitest'
import { pickActiveSection, type SectionCandidate } from '@/utils/scrollSpy'

function cand(id: string, top: number): SectionCandidate {
  return { id, top }
}

describe('pickActiveSection', () => {
  test('空列表 → null（保持当前高亮不变）', () => {
    expect(pickActiveSection([])).toBeNull()
  })

  test('单节可见 → 该节', () => {
    expect(pickActiveSection([cand('a', 120)])).toBe('a')
  })

  test('多节可见 → 取 top 最小（最靠上）的一节', () => {
    expect(pickActiveSection([cand('a', 300), cand('b', 100), cand('c', 220)])).toBe('b')
  })

  test('顺序无关: 输入顺序不影响结果', () => {
    const asc = [cand('a', 10), cand('b', 50), cand('c', 90)]
    const desc = [cand('c', 90), cand('b', 50), cand('a', 10)]
    expect(pickActiveSection(asc)).toBe('a')
    expect(pickActiveSection(desc)).toBe('a')
  })

  test('top 为负（已滚过吸顶线）也算可见候选, 且优先于正值', () => {
    expect(pickActiveSection([cand('a', 40), cand('b', -12)])).toBe('b')
  })

  test('空 id 的候选被忽略', () => {
    expect(pickActiveSection([cand('', -100), cand('a', 80)])).toBe('a')
    expect(pickActiveSection([cand('', -100)])).toBeNull()
  })

  test('top 相同 → 取先出现的（稳定, 不随遍历顺序抖动）', () => {
    expect(pickActiveSection([cand('a', 50), cand('b', 50)])).toBe('a')
  })

  test('大列表仍取最靠上者', () => {
    const many = Array.from({ length: 50 }, (_, i) => cand(`s${i}`, 1000 - i * 10))
    expect(pickActiveSection(many)).toBe('s49')
  })
})
