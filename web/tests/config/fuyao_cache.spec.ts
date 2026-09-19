/**
 * FuyaoCachePanel 纯函数测试 — 直接打 web/src/views/config/fuyaoCache.ts
 * 的真实实现。禁止在 spec 里复刻组件逻辑 (复刻版曾随 defaults/overrides
 * 旧模型漂移, 给出虚假信心 — 第五轮审视 H2)。
 */
import { describe, expect, test } from 'vitest'
import {
  buildFuyaoTtlDiffs,
  formatSeconds,
  fuyaoToolsSnapshot,
  normalizeTtlSeconds,
} from '@/views/config/fuyaoCache'

describe('fuyaoCache — formatSeconds', () => {
  test('zero means realtime / no cache', () => {
    expect(formatSeconds(0)).toBe('实时 / 不缓存')
  })

  test('seconds under a minute', () => {
    expect(formatSeconds(1)).toBe('1 秒')
    expect(formatSeconds(30)).toBe('30 秒')
  })

  test('integer and fractional minutes', () => {
    expect(formatSeconds(60)).toBe('1 分钟')
    expect(formatSeconds(300)).toBe('5 分钟')
    expect(formatSeconds(90)).toBe('1.5 分钟')
  })

  test('integer and fractional hours', () => {
    expect(formatSeconds(3600)).toBe('1 小时')
    expect(formatSeconds(5400)).toBe('1.5 小时')
  })

  test('integer and fractional days', () => {
    expect(formatSeconds(86400)).toBe('1 天')
    expect(formatSeconds(86400 * 7)).toBe('7 天')
    expect(formatSeconds(86400 + 43200)).toBe('1.5 天')
  })
})

describe('fuyaoCache — buildFuyaoTtlDiffs', () => {
  test('no changes when snapshot equals working', () => {
    const map = { tool_a: 30, tool_b: 3600 }
    expect(buildFuyaoTtlDiffs(map, map)).toEqual([])
    expect(buildFuyaoTtlDiffs({}, {})).toEqual([])
  })

  test('new override appears as add with global-fallback before text', () => {
    expect(buildFuyaoTtlDiffs({}, { tool_a: 30 })).toEqual([
      {
        path: 'tools.tool_a',
        beforeText: '(未设置, 走全局)',
        afterText: '30s (30 秒)',
      },
    ])
  })

  test('realtime (0s) add renders the 实时 label', () => {
    expect(buildFuyaoTtlDiffs({}, { tool_a: 0 })).toEqual([
      {
        path: 'tools.tool_a',
        beforeText: '(未设置, 走全局)',
        afterText: '0s (实时 / 不缓存)',
      },
    ])
  })

  test('removed override appears as clear with cache.ttl fallback text', () => {
    expect(buildFuyaoTtlDiffs({ tool_a: 30 }, {})).toEqual([
      {
        path: 'tools.tool_a',
        beforeText: '30s (30 秒)',
        afterText: '(已清空, 回落全局 cache.ttl)',
      },
    ])
  })

  test('value change on existing override appears as modify', () => {
    const diffs = buildFuyaoTtlDiffs({ tool_a: 30 }, { tool_a: 60 })
    expect(diffs).toHaveLength(1)
    expect(diffs[0]).toEqual({
      path: 'tools.tool_a',
      beforeText: '30s (30 秒)',
      afterText: '60s (1 分钟)',
    })
  })

  test('unchanged tools are skipped while siblings diff', () => {
    const diffs = buildFuyaoTtlDiffs(
      { tool_a: 30, tool_b: 60 },
      { tool_a: 300, tool_b: 60 },
    )
    expect(diffs).toHaveLength(1)
    expect(diffs[0].path).toBe('tools.tool_a')
  })

  test('add, change and delete coexist in union insertion order', () => {
    // snapshot 顺序在前, 仅 working 新增的 key 追加在后
    const diffs = buildFuyaoTtlDiffs(
      { gone: 10, kept: 60 },
      { kept: 300, added: 0 },
    )
    expect(diffs.map((d) => d.path)).toEqual([
      'tools.gone',
      'tools.kept',
      'tools.added',
    ])
    expect(diffs[0].afterText).toBe('(已清空, 回落全局 cache.ttl)')
    expect(diffs[2].beforeText).toBe('(未设置, 走全局)')
  })
})

describe('fuyaoCache — normalizeTtlSeconds', () => {
  test('empty-ish / invalid input clears the override', () => {
    expect(normalizeTtlSeconds(undefined)).toBeUndefined()
    expect(normalizeTtlSeconds(null)).toBeUndefined()
    expect(normalizeTtlSeconds(Number.NaN)).toBeUndefined()
    expect(normalizeTtlSeconds(Number.POSITIVE_INFINITY)).toBeUndefined()
    expect(normalizeTtlSeconds(-1)).toBeUndefined()
  })

  test('finite non-negative values floor to integers, zero stays zero', () => {
    expect(normalizeTtlSeconds(0)).toBe(0)
    expect(normalizeTtlSeconds(60.9)).toBe(60)
    expect(normalizeTtlSeconds(604800)).toBe(604800)
  })
})

describe('fuyaoCache — fuyaoToolsSnapshot', () => {
  test('undefined input yields an empty map', () => {
    expect(fuyaoToolsSnapshot(undefined)).toEqual({})
  })

  test('keeps zero and non-negative finite numbers', () => {
    expect(fuyaoToolsSnapshot({ a: 0, b: 60, c: 1.5 })).toEqual({
      a: 0,
      b: 60,
      c: 1.5,
    })
  })

  test('drops negatives, infinities and non-numbers (dirty-config defense)', () => {
    expect(
      fuyaoToolsSnapshot({
        neg: -5,
        inf: Number.POSITIVE_INFINITY,
        str: '60',
        ok: 300,
      }),
    ).toEqual({ ok: 300 })
  })
})
