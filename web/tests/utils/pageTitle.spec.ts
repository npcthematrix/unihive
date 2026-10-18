import { describe, expect, it } from 'vitest'
import { APP_TITLE, pageTitle } from '@/utils/pageTitle'

describe('pageTitle', () => {
  it('有页面标题时拼成 "页面 · 应用名"', () => {
    expect(pageTitle('配置信息')).toBe('配置信息 · UniHive Console')
  })

  it('登录页也带前缀 (与其他页一致)', () => {
    expect(pageTitle('登录')).toBe('登录 · UniHive Console')
  })

  it('缺失/非字符串标题退回应用名', () => {
    for (const v of [undefined, null, '', '   ', 42, ['配置信息']]) {
      expect(pageTitle(v)).toBe(APP_TITLE)
    }
  })

  it('标题两侧空白被裁掉', () => {
    expect(pageTitle('  网关运行监控  ')).toBe('网关运行监控 · UniHive Console')
  })
})
