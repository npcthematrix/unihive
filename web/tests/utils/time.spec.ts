import { describe, expect, it } from 'vitest'
import { formatHMS } from '@/utils/time'

describe('formatHMS', () => {
  // 用 new Date(year, month, ...) 构造 (LOCAL 时区), .getTime() 转 epoch, 然后
  // formatHMS 读回 LOCAL 时区 → 应该原样输出。这样测试与运行环境 TZ 解耦。

  it('把 epoch 毫秒格式化为 HH:MM:SS 24h 本地时区', () => {
    const d = new Date(2026, 8, 15, 14, 23, 7) // 2026-09-15 14:23:07 LOCAL
    expect(formatHMS(d.getTime())).toBe('14:23:07')
  })

  it('用零填充个位时/分/秒', () => {
    const d = new Date(2026, 0, 1, 1, 2, 3)
    expect(formatHMS(d.getTime())).toBe('01:02:03')
  })

  it('午夜 00:00:00', () => {
    const d = new Date(2026, 0, 1, 0, 0, 0)
    expect(formatHMS(d.getTime())).toBe('00:00:00')
  })

  it('接受非整点时间 (毫秒被忽略)', () => {
    const d = new Date(2026, 8, 15, 14, 23, 7)
    d.setMilliseconds(456)
    expect(formatHMS(d.getTime())).toBe('14:23:07')
  })
})
