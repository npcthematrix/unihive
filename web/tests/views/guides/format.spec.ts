import { describe, expect, test } from 'vitest'
import {
  AI_SECTIONS,
  GUIDE_TAB_KEYS,
  GUIDE_TAB_TITLES,
  HUMAN_SECTIONS,
  sectionsForTab,
  type GuideTabKey,
} from '@/views/guides/format'

describe('子标签常量', () => {
  test('两个子标签都有中文标题', () => {
    expect(GUIDE_TAB_KEYS).toEqual(['human', 'ai'])
    for (const key of GUIDE_TAB_KEYS) {
      expect(GUIDE_TAB_TITLES[key]).toBeTruthy()
      expect(GUIDE_TAB_TITLES[key]).not.toBe(key)
    }
  })
})

describe('章节表', () => {
  const all = [...HUMAN_SECTIONS, ...AI_SECTIONS]

  test('两个面板都有章节', () => {
    expect(HUMAN_SECTIONS.length).toBeGreaterThan(0)
    expect(AI_SECTIONS.length).toBeGreaterThan(0)
  })

  test('章节 id 全局唯一 —— 重名会让 getElementById 取到别的面板的元素', () => {
    const ids = all.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  test('每节都有 id 与非空标题', () => {
    for (const s of all) {
      expect(s.id).toMatch(/^sec-[a-z0-9-]+$/)
      expect(s.title.trim().length).toBeGreaterThan(0)
    }
  })

  test('标题不重复 —— 导航上出现两个同名项会无法区分', () => {
    for (const list of [HUMAN_SECTIONS, AI_SECTIONS]) {
      const titles = list.map((s) => s.title)
      expect(new Set(titles).size).toBe(titles.length)
    }
  })
})

describe('sectionsForTab', () => {
  test('按 tab 返回对应章节表（同一引用, 便于 computed 稳定比较）', () => {
    expect(sectionsForTab('human')).toBe(HUMAN_SECTIONS)
    expect(sectionsForTab('ai')).toBe(AI_SECTIONS)
  })

  test('未知 key 回落到人工接入', () => {
    expect(sectionsForTab('nope' as GuideTabKey)).toBe(HUMAN_SECTIONS)
  })

  test('每张表的首节就是 scrollspy 的初始高亮项', () => {
    for (const key of GUIDE_TAB_KEYS) {
      expect(sectionsForTab(key)[0]?.id).toBeTruthy()
    }
  })
})
