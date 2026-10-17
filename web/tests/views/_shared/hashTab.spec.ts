import { describe, expect, test } from 'vitest'
import { parseHashTab, nextTabKey } from '@/views/_shared/hashTab'

describe('parseHashTab', () => {
  const keys = ['overview', 'gateway', 'pipeline'] as const

  test('命中合法值原样返回', () => {
    expect(parseHashTab('pipeline', keys, 'overview')).toBe('pipeline')
  })

  test('数组 query 取第一个元素', () => {
    expect(parseHashTab(['gateway', 'pipeline'], keys, 'overview')).toBe('gateway')
  })

  test('非法值 / 非字符串一律回退', () => {
    expect(parseHashTab('__proto__', keys, 'overview')).toBe('overview')
    expect(parseHashTab(undefined, keys, 'overview')).toBe('overview')
    expect(parseHashTab(42, keys, 'overview')).toBe('overview')
  })
})

describe('nextTabKey — WAI-ARIA roving tabindex 导航', () => {
  const keys = ['a', 'b', 'c'] as const

  test('ArrowRight 前进, 末位循环回首位', () => {
    expect(nextTabKey(keys, 'a', 'ArrowRight')).toBe('b')
    expect(nextTabKey(keys, 'c', 'ArrowRight')).toBe('a')
  })

  test('ArrowLeft 后退, 首位循环到末位', () => {
    expect(nextTabKey(keys, 'b', 'ArrowLeft')).toBe('a')
    expect(nextTabKey(keys, 'a', 'ArrowLeft')).toBe('c')
  })

  test('Home / End 跳首尾', () => {
    expect(nextTabKey(keys, 'c', 'Home')).toBe('a')
    expect(nextTabKey(keys, 'a', 'End')).toBe('c')
  })

  test('非导航键返回 null (调用方不拦截)', () => {
    expect(nextTabKey(keys, 'a', 'Enter')).toBeNull()
    expect(nextTabKey(keys, 'a', 'Tab')).toBeNull()
    expect(nextTabKey(keys, 'a', 'ArrowUp')).toBeNull()
  })

  test('current 不在 keys 中返回 null (防御未知状态)', () => {
    expect(nextTabKey(keys, 'z' as (typeof keys)[number], 'ArrowRight')).toBeNull()
  })
})
