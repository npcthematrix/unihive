import { describe, expect, test } from 'vitest'
import { isUpstreamEnabled, toggleLabel } from '../../src/views/config/upstreams'

describe('isUpstreamEnabled', () => {
  test('enabled === true 才视为启用', () => {
    expect(isUpstreamEnabled({ enabled: true })).toBe(true)
    expect(isUpstreamEnabled({ enabled: false })).toBe(false)
  })

  test('字段缺失视为禁用 (与后端 cfg.get("enabled", False) 一致)', () => {
    expect(isUpstreamEnabled({})).toBe(false)
    expect(isUpstreamEnabled({ type: 'http' })).toBe(false)
  })

  test('非对象/空值安全降级为禁用', () => {
    expect(isUpstreamEnabled(null)).toBe(false)
    expect(isUpstreamEnabled(undefined)).toBe(false)
    expect(isUpstreamEnabled('true')).toBe(false)
    expect(isUpstreamEnabled(1)).toBe(false)
  })
})

describe('toggleLabel', () => {
  test('当前启用 → 按钮动作为禁用; 当前停用 → 启用', () => {
    expect(toggleLabel(true)).toBe('禁用')
    expect(toggleLabel(false)).toBe('启用')
  })
})
