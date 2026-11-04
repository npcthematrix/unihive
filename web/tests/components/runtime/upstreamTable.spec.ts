import { describe, expect, test } from 'vitest'
import {
  STATUS_ORDER,
  TYPE_TEXT,
  rowStatus,
  sortUpstreamRows,
  typeText,
  upstreamEmptyText,
  type UpstreamSortKey,
} from '@/components/runtime/upstreamTable'
import { KNOWN_UPSTREAM_TYPES } from '@/views/config/upstreamEdit'
import type { UpstreamRow, UpstreamStatusValue } from '@/api/status'

function makeRow(name: string, over: Partial<UpstreamRow> = {}): UpstreamRow {
  return {
    name,
    enabled: true,
    type: 'http',
    description: '',
    status: 'online',
    latency_ms: null,
    last_error: null,
    ...over,
  }
}

const names = (rows: UpstreamRow[]): string[] => rows.map((r) => r.name)

describe('upstreamTable — rowStatus (H3): 展示状态以 enabled 为准', () => {
  test('关掉的 HTTP 上游即使后端探到 online 也判 disabled', () => {
    expect(rowStatus(makeRow('u', { enabled: false, status: 'online' }))).toBe('disabled')
  })

  test('启用的上游透传后端状态', () => {
    expect(rowStatus(makeRow('u', { status: 'degraded' }))).toBe('degraded')
  })
})

describe('upstreamTable — typeText (T-B4): 与后端 type 词汇对齐', () => {
  test('config_loader 接受的每个 type 都有短标签, 不再把原始 id 打进徽标', () => {
    for (const t of KNOWN_UPSTREAM_TYPES) {
      expect(TYPE_TEXT[t], `缺少 ${t} 的展示名`).toBeTruthy()
      expect(typeText(t)).toBe(TYPE_TEXT[t])
    }
  })

  test('旧码的 stdio 是死键 (不在后端词汇里), 已移除', () => {
    expect(TYPE_TEXT).not.toHaveProperty('stdio')
    expect(KNOWN_UPSTREAM_TYPES).not.toContain('stdio')
  })

  test('未收录的 type 原样透出 (前向兼容), 空值回退 -', () => {
    expect(typeText('brand_new_upstream')).toBe('brand_new_upstream')
    expect(typeText('')).toBe('-')
  })
})

describe('upstreamTable — sortUpstreamRows', () => {
  test('key 为 null 时复位到传入顺序 (T-M2 的复位分支)', () => {
    const rows = [makeRow('c'), makeRow('a'), makeRow('b')]
    expect(sortUpstreamRows(rows, null, true)).toBe(rows)
  })

  test('name 升/降序', () => {
    const rows = [makeRow('c'), makeRow('a'), makeRow('b')]
    expect(names(sortUpstreamRows(rows, 'name', true))).toEqual(['a', 'b', 'c'])
    expect(names(sortUpstreamRows(rows, 'name', false))).toEqual(['c', 'b', 'a'])
  })

  test('status 按健康度权重排, 不看裸 status 而看 rowStatus', () => {
    const rows = [
      makeRow('off', { status: 'offline' }),
      makeRow('dis', { enabled: false, status: 'online' }),
      makeRow('ok', { status: 'online' }),
      makeRow('unk', { status: 'unknown' }),
    ]
    expect(names(sortUpstreamRows(rows, 'status', true))).toEqual(['ok', 'off', 'dis', 'unk'])
    expect(names(sortUpstreamRows(rows, 'status', false))).toEqual(['unk', 'dis', 'off', 'ok'])
  })

  test('未收录状态排最后而不是排最前', () => {
    const rows = [
      makeRow('weird', { status: 'brand_new' as UpstreamStatusValue }),
      makeRow('off', { status: 'offline' }),
    ]
    expect(names(sortUpstreamRows(rows, 'status', true))).toEqual(['off', 'weird'])
    expect(STATUS_ORDER).not.toHaveProperty('brand_new')
  })

  describe('回归 (T-L1): latency_ms 为 null 是「未测量」, 不是最小值', () => {
    const rows = [
      makeRow('unmeasured', { latency_ms: null }),
      makeRow('fastest', { latency_ms: 0 }),
      makeRow('slow', { latency_ms: 1200 }),
    ]

    test('升序时未测量行排末尾 (旧码折成 -1, 会排在 0ms 之前)', () => {
      expect(names(sortUpstreamRows(rows, 'latency_ms', true)))
        .toEqual(['fastest', 'slow', 'unmeasured'])
    })

    test('降序时未测量行仍在末尾 (方向只作用于真实数值)', () => {
      expect(names(sortUpstreamRows(rows, 'latency_ms', false)))
        .toEqual(['slow', 'fastest', 'unmeasured'])
    })

    test('全部未测量时保持原顺序', () => {
      const allNull = [makeRow('b', { latency_ms: null }), makeRow('a', { latency_ms: null })]
      expect(names(sortUpstreamRows(allNull, 'latency_ms', true))).toEqual(['b', 'a'])
      expect(names(sortUpstreamRows(allNull, 'latency_ms', false))).toEqual(['b', 'a'])
    })
  })

  test('不改动传入数组', () => {
    const rows = [makeRow('b'), makeRow('a')]
    sortUpstreamRows(rows, 'name', true)
    expect(names(rows)).toEqual(['b', 'a'])
  })
})

describe('upstreamTable — upstreamEmptyText (T-M1): 空态分四态', () => {
  const base = { rowCount: 0, filteredCount: 0, loading: false, everLoaded: true }

  test('有行但被过滤光 → 暂无匹配', () => {
    expect(upstreamEmptyText({ ...base, rowCount: 3, filteredCount: 0 }))
      .toBe('暂无匹配的上游')
  })

  test('加载中优先于其它态', () => {
    expect(upstreamEmptyText({ ...base, loading: true })).toBe('正在加载上游状态…')
    expect(upstreamEmptyText({ ...base, loading: true, everLoaded: false }))
      .toBe('正在加载上游状态…')
  })

  test('从未加载成功 → 不当成「没配上游」', () => {
    expect(upstreamEmptyText({ ...base, everLoaded: false })).toContain('尚未取到上游状态')
  })

  test('加载成功且确实零上游 → 未配置', () => {
    expect(upstreamEmptyText(base)).toBe('未配置任何上游')
  })

  test('过滤态优先于加载态 (已有快照时刷新不该说成没匹配)', () => {
    expect(upstreamEmptyText({ ...base, rowCount: 3, filteredCount: 0, loading: true }))
      .toBe('暂无匹配的上游')
  })
})

describe('upstreamTable — 排序键白名单', () => {
  test('三个可排序列都是行上真实字段名 (L11)', () => {
    const keys: UpstreamSortKey[] = ['name', 'status', 'latency_ms']
    const row = makeRow('u') as unknown as Record<string, unknown>
    for (const k of keys) expect(k in row).toBe(true)
  })
})
