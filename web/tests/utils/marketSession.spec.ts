import { describe, expect, it } from 'vitest'
import {
  formatCountdown,
  marketSession,
  type MarketPhase,
} from '@/utils/market-session'

/**
 * 构造上海墙钟对应的绝对时刻: SH H:M = UTC (H-8):M。
 * 2026-09-21 = 周一; 2026-09-19 = 周六。
 */
function shanghai(day: number, h: number, m = 0): Date {
  return new Date(Date.UTC(2026, 8, day, h - 8, m, 0))
}

function expectPhase(now: Date, phase: MarketPhase) {
  expect(marketSession(now).phase).toBe(phase)
}

describe('marketSession 时段判定', () => {
  it('盘前 (<9:30): 短档缓存到 9:30', () => {
    const info = marketSession(shanghai(21, 8, 0))
    expectPhase(shanghai(21, 8, 0), 'pre_open')
    expect(info.shortTtlActive).toBe(false)
    expect(info.boundaryLabel).toBe('9:30 开盘')
    expect(info.secondsToBoundary).toBeCloseTo(5400, 0)
  })

  it('9:30 整点进入上午盘', () => {
    const info = marketSession(shanghai(21, 9, 30))
    expect(info.phase).toBe('morning')
    expect(info.shortTtlActive).toBe(true)
  })

  it('上午盘: 短档按配置值, 边界为 11:30', () => {
    const info = marketSession(shanghai(21, 10, 0))
    expect(info.phase).toBe('morning')
    expect(info.range).toBe('9:30–11:30')
    expect(info.boundaryLabel).toBe('11:30 午休')
    expect(info.secondsToBoundary).toBeCloseTo(5400, 0)
  })

  it('11:30 整点进入午间休市', () => {
    const info = marketSession(shanghai(21, 11, 30))
    expect(info.phase).toBe('lunch')
    expect(info.secondsToBoundary).toBeCloseTo(5400, 0)
  })

  it('午休: 短档缓存到 13:00', () => {
    const info = marketSession(shanghai(21, 12, 30))
    expect(info.phase).toBe('lunch')
    expect(info.boundaryLabel).toBe('13:00 开盘')
    expect(info.secondsToBoundary).toBeCloseTo(1800, 0)
  })

  it('13:00 整点进入下午盘', () => {
    expectPhase(shanghai(21, 13, 0), 'afternoon')
  })

  it('下午盘: 边界 15:00', () => {
    const info = marketSession(shanghai(21, 14, 0))
    expect(info.phase).toBe('afternoon')
    expect(info.shortTtlActive).toBe(true)
    expect(info.secondsToBoundary).toBeCloseTo(3600, 0)
  })

  it('15:00 整点进入收盘后, 缓存到次日零点', () => {
    const info = marketSession(shanghai(21, 15, 0))
    expect(info.phase).toBe('closed')
    expect(info.boundaryLabel).toBe('次日零点')
    expect(info.secondsToBoundary).toBeCloseTo(32400, 0)
  })

  it('收盘后 20:00: 距次日零点 4h', () => {
    const info = marketSession(shanghai(21, 20, 0))
    expect(info.phase).toBe('closed')
    expect(info.secondsToBoundary).toBeCloseTo(14400, 0)
  })

  it('周六: 周末档, 逐日续期到周日零点', () => {
    const info = marketSession(shanghai(19, 10, 0))
    expect(info.phase).toBe('weekend')
    expect(info.shortTtlActive).toBe(false)
    expect(info.boundaryLabel).toBe('次日零点')
    // 周六 10:00 → 周日 00:00 = 14h
    expect(info.secondsToBoundary).toBeCloseTo(50400, 0)
  })

  it('周日 23:00: 续期到周一零点', () => {
    // 2026-09-20 = 周日
    const info = marketSession(shanghai(20, 23, 0))
    expect(info.phase).toBe('weekend')
    expect(info.secondsToBoundary).toBeCloseTo(3600, 0)
  })

  it('秒数永远非负', () => {
    const info = marketSession(shanghai(21, 23, 59))
    expect(info.secondsToBoundary).toBeGreaterThanOrEqual(0)
  })
})

describe('formatCountdown', () => {
  it('秒级', () => {
    expect(formatCountdown(45)).toBe('45s')
  })

  it('分钟+秒零填充', () => {
    expect(formatCountdown(90)).toBe('1m30s')
  })

  it('小时级', () => {
    // 3725s = 1h02m05s → 1h02m
    expect(formatCountdown(3725)).toBe('1h02m')
  })

  it('负值兜底为 0s', () => {
    expect(formatCountdown(-10)).toBe('0s')
  })
})
