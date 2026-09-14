/**
 * FuyaoCachePanel 纯函数 + diff 计算测试。
 *
 * formatSeconds 与 diff 计算目前是组件内部 computed / function, 此处
 * 通过同一份输入断言其行为, 避免测试 setup 引入 happy-dom。
 */
import { describe, expect, test } from 'vitest'

/** 复刻 FuyaoCachePanel.vue 的 formatSeconds (避免 import 单文件组件的 happy-dom 依赖)。 */
function formatSeconds(s: number): string {
  if (s === 0) return '实时 / 不缓存'
  if (s < 60) return `${s} 秒`
  if (s < 3600) {
    const m = s / 60
    return `${Number.isInteger(m) ? m : m.toFixed(1)} 分钟`
  }
  if (s < 86400) {
    const h = s / 3600
    return `${Number.isInteger(h) ? h : h.toFixed(1)} 小时`
  }
  const d = s / 86400
  return `${Number.isInteger(d) ? d : d.toFixed(1)} 天`
}

/** 复刻 FuyaoCachePanel.vue 的 diff 计算 (snapshot vs working)。 */
interface OverrideRow {
  tool: string
  seconds: number
}
interface Draft {
  defaults: Record<string, number>
  overrides: Record<string, OverrideRow[]>
}
interface DiffEntry {
  path: string
  beforeText: string
  afterText: string
}

const ENDPOINT_ORDER = ['fuyao_ashare', 'fuyao_index', 'fuyao_meta', 'fuyao_fund'] as const

function computeDiffs(snapshot: Draft, working: Draft): DiffEntry[] {
  const out: DiffEntry[] = []
  for (const ep of ENDPOINT_ORDER) {
    const beforeDef = snapshot.defaults[ep]
    const afterDef = working.defaults[ep]
    if (beforeDef !== afterDef) {
      out.push({
        path: `${ep}.default`,
        beforeText: `${beforeDef}s (${formatSeconds(beforeDef)})`,
        afterText: `${afterDef}s (${formatSeconds(afterDef)})`,
      })
    }
    const beforeOv = new Map(snapshot.overrides[ep].map((r) => [r.tool, r.seconds]))
    const afterOv = new Map(working.overrides[ep].map((r) => [r.tool, r.seconds]))
    const allTools = new Set([...beforeOv.keys(), ...afterOv.keys()])
    for (const tool of allTools) {
      const b = beforeOv.get(tool)
      const a = afterOv.get(tool)
      if (b === undefined && a !== undefined) {
        out.push({
          path: `${ep}.override.${tool}`,
          beforeText: '(未设置)',
          afterText: `${a}s (${formatSeconds(a)})`,
        })
      } else if (b !== undefined && a === undefined) {
        out.push({
          path: `${ep}.override.${tool}`,
          beforeText: `${b}s (${formatSeconds(b)})`,
          afterText: '(已删除, 回落 default)',
        })
      } else if (b !== undefined && a !== undefined && b !== a) {
        out.push({
          path: `${ep}.override.${tool}`,
          beforeText: `${b}s (${formatSeconds(b)})`,
          afterText: `${a}s (${formatSeconds(a)})`,
        })
      }
    }
  }
  return out
}

function emptyDraft(): Draft {
  return {
    defaults: { fuyao_ashare: 0, fuyao_index: 0, fuyao_meta: 0, fuyao_fund: 0 },
    overrides: { fuyao_ashare: [], fuyao_index: [], fuyao_meta: [], fuyao_fund: [] },
  }
}

describe('FuyaoCachePanel — formatSeconds', () => {
  test('zero returns "实时 / 不缓存"', () => {
    expect(formatSeconds(0)).toBe('实时 / 不缓存')
  })

  test('seconds under a minute', () => {
    expect(formatSeconds(30)).toBe('30 秒')
  })

  test('integer minutes', () => {
    expect(formatSeconds(60)).toBe('1 分钟')
    expect(formatSeconds(300)).toBe('5 分钟')
  })

  test('fractional minutes', () => {
    expect(formatSeconds(90)).toBe('1.5 分钟')
  })

  test('integer hours', () => {
    expect(formatSeconds(3600)).toBe('1 小时')
  })

  test('days', () => {
    expect(formatSeconds(86400)).toBe('1 天')
    expect(formatSeconds(86400 * 7)).toBe('7 天')
  })

  test('fractional days', () => {
    expect(formatSeconds(86400 + 43200)).toBe('1.5 天')
  })
})

describe('FuyaoCachePanel — computeDiffs', () => {
  test('no changes when snapshot equals working', () => {
    const d = emptyDraft()
    d.defaults.fuyao_ashare = 60
    d.overrides.fuyao_ashare = [{ tool: 'tool_a', seconds: 30 }]
    expect(computeDiffs(d, d)).toEqual([])
  })

  test('default change produces one diff entry', () => {
    const before = emptyDraft()
    const after = emptyDraft()
    before.defaults.fuyao_ashare = 300
    after.defaults.fuyao_ashare = 600
    const diffs = computeDiffs(before, after)
    expect(diffs).toHaveLength(1)
    expect(diffs[0]).toMatchObject({ path: 'fuyao_ashare.default' })
  })

  test('new override appears as add', () => {
    const before = emptyDraft()
    const after = emptyDraft()
    after.overrides.fuyao_ashare = [{ tool: 'tool_a', seconds: 30 }]
    const diffs = computeDiffs(before, after)
    expect(diffs).toEqual([
      {
        path: 'fuyao_ashare.override.tool_a',
        beforeText: '(未设置)',
        afterText: '30s (30 秒)',
      },
    ])
  })

  test('removed override appears as delete', () => {
    const before = emptyDraft()
    const after = emptyDraft()
    before.overrides.fuyao_ashare = [{ tool: 'tool_a', seconds: 30 }]
    const diffs = computeDiffs(before, after)
    expect(diffs).toEqual([
      {
        path: 'fuyao_ashare.override.tool_a',
        beforeText: '30s (30 秒)',
        afterText: '(已删除, 回落 default)',
      },
    ])
  })

  test('value change on existing override appears as modify', () => {
    const before = emptyDraft()
    const after = emptyDraft()
    before.overrides.fuyao_ashare = [{ tool: 'tool_a', seconds: 30 }]
    after.overrides.fuyao_ashare = [{ tool: 'tool_a', seconds: 60 }]
    const diffs = computeDiffs(before, after)
    expect(diffs).toHaveLength(1)
    expect(diffs[0].beforeText).toBe('30s (30 秒)')
    expect(diffs[0].afterText).toBe('60s (1 分钟)')
  })

  test('multiple endpoints diffs are aggregated', () => {
    const before = emptyDraft()
    const after = emptyDraft()
    before.defaults.fuyao_ashare = 300
    before.defaults.fuyao_index = 3600
    after.defaults.fuyao_ashare = 600
    after.defaults.fuyao_index = 3600  // 相同
    const diffs = computeDiffs(before, after)
    expect(diffs).toHaveLength(1)
    expect(diffs[0].path).toBe('fuyao_ashare.default')
  })
})
