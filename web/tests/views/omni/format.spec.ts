import { describe, expect, test } from 'vitest'
import {
  BOARD_TYPE_KEYS,
  SOURCE_KEYS,
  buildQueryDesc,
  displayBoardType,
  displaySource,
  displayStatus,
  formatDate,
  formatDuration,
  formatTime,
  statusIcon,
  statusTagType,
} from '@/views/omni/format'

describe('displaySource / displayBoardType', () => {
  test('数据库存储值与内部别名都映射到同一中文名', () => {
    expect(displaySource('fuyao')).toBe('同花顺')
    expect(displaySource('THS')).toBe('同花顺')
    expect(displaySource('tqquant')).toBe('TDX-QUANT')
    expect(displaySource('TDX')).toBe('TDX-QUANT')
    expect(displaySource('tdxquant')).toBe('TDX-QUANT')
  })

  test('未知值原样返回, 空值回落 "-"', () => {
    expect(displaySource('new-source')).toBe('new-source')
    expect(displaySource('')).toBe('-')
    expect(displaySource(null)).toBe('-')
    expect(displayBoardType(undefined)).toBe('-')
  })

  test('板块类型键都有中文名', () => {
    for (const k of BOARD_TYPE_KEYS) {
      expect(displayBoardType(k)).not.toBe(k)
    }
    for (const k of SOURCE_KEYS) {
      expect(displaySource(k)).not.toBe(k)
    }
  })
})

describe('displayStatus', () => {
  test('后端英文枚举 → 中文', () => {
    expect(displayStatus('success')).toBe('成功')
    expect(displayStatus('failed')).toBe('失败')
    expect(displayStatus('running')).toBe('进行中')
  })

  test('未知状态原样返回（不吞掉后端新增枚举）', () => {
    expect(displayStatus('canceled')).toBe('canceled')
  })

  test('空值回落 "-"', () => {
    expect(displayStatus('')).toBe('-')
    expect(displayStatus(undefined)).toBe('-')
    expect(displayStatus(null)).toBe('-')
  })
})

describe('statusTagType / statusIcon', () => {
  test('三种已知状态各自有独立 tag type 与非空图标前缀', () => {
    expect(statusTagType('success')).toBe('success')
    expect(statusTagType('running')).toBe('warning')
    expect(statusTagType('failed')).toBe('danger')
    expect(statusTagType('whatever')).toBe('info')
    for (const s of ['success', 'running', 'failed']) {
      expect(statusIcon(s).trim().length).toBeGreaterThan(0)
    }
  })
})

describe('buildQueryDesc', () => {
  test('无任何筛选 → "(无筛选)"', () => {
    expect(buildQueryDesc({})).toBe('(无筛选)')
    expect(buildQueryDesc({ source: '', boardType: '', keyword: '' })).toBe('(无筛选)')
    expect(buildQueryDesc({ keyword: '   ' })).toBe('(无筛选)')
  })

  test('按 数据源 → 类型 → 关键词 顺序拼接, 值走中文展示', () => {
    expect(
      buildQueryDesc({ source: 'fuyao', boardType: 'industry', keyword: '半导体' }),
    ).toBe('数据源=同花顺, 类型=行业板块, 关键词="半导体"')
  })

  test('关键词两端空白被裁掉', () => {
    expect(buildQueryDesc({ keyword: '  光伏  ' })).toBe('关键词="光伏"')
  })

  test('只给部分筛选时不产生空段', () => {
    expect(buildQueryDesc({ boardType: 'concept' })).toBe('类型=概念板块')
    expect(buildQueryDesc({ source: 'tqquant' })).toBe('数据源=TDX-QUANT')
  })

  test('读的是传入对象 —— 与输入框草稿解耦（HIGH-2 回归点）', () => {
    const committed = { source: 'fuyao', boardType: '', keyword: '银行' }
    const draft = { source: '', boardType: 'concept', keyword: '证券' }
    expect(buildQueryDesc(committed)).toBe('数据源=同花顺, 关键词="银行"')
    expect(buildQueryDesc(draft)).toBe('类型=概念板块, 关键词="证券"')
    expect(buildQueryDesc(committed)).not.toBe(buildQueryDesc(draft))
  })
})

describe('时间解析（后端存 UTC 无时区串）', () => {
  test('formatTime 按 UTC 解析后转本地', () => {
    const out = formatTime('2026-10-17 04:00:00')
    const expected = new Date(Date.UTC(2026, 9, 17, 4, 0, 0))
    const p = (n: number) => String(n).padStart(2, '0')
    expect(out).toBe(
      `${expected.getFullYear()}-${p(expected.getMonth() + 1)}-${p(expected.getDate())} ` +
        `${p(expected.getHours())}:${p(expected.getMinutes())}:${p(expected.getSeconds())}`,
    )
  })

  test('formatTime 空值 → "-"; 不可解析串原样截断', () => {
    expect(formatTime('')).toBe('-')
    expect(formatTime(null)).toBe('-')
    expect(formatTime('garbage')).toBe('garbage')
  })

  test('formatDate 取本地日期', () => {
    expect(formatDate('2026-10-17 04:00:00')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(formatDate('')).toBe('-')
  })

  test('formatDuration 与时区无关; 倒序/缺失返回 null', () => {
    expect(formatDuration('2026-10-17 04:00:00', '2026-10-17 04:00:12')).toBe('12.0s')
    expect(formatDuration('2026-10-17 04:00:12', '2026-10-17 04:00:00')).toBeNull()
    expect(formatDuration(undefined, '2026-10-17 04:00:00')).toBeNull()
    expect(formatDuration('2026-10-17 04:00:00', undefined)).toBeNull()
  })
})
