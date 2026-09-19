import { describe, expect, it } from 'vitest'
import type { UpstreamEntity } from '@/api/config'
import {
  connectionConfigToText,
  isUpstreamChanged,
  parseConnectionConfig,
  plaintextSecretPaths,
} from '@/views/config/upstreamEdit'

function entity(overrides: Partial<UpstreamEntity> = {}): UpstreamEntity {
  return {
    name: 'beta',
    type: 'http',
    enabled: false,
    config: { base_url: 'http://x', api_key: '${BETA_KEY}' },
    origin: 'builtin',
    updated_at: '2026-09-18T00:00:00+00:00',
    ...overrides,
  }
}

describe('connectionConfigToText', () => {
  it('实体 dict → 2 空格缩进 JSON 文本', () => {
    expect(connectionConfigToText({ a: 1 })).toBe('{\n  "a": 1\n}')
  })
})

describe('parseConnectionConfig', () => {
  it('空文本 → 空对象', () => {
    expect(parseConnectionConfig('   ').value).toEqual({})
  })

  it('合法对象解析', () => {
    expect(parseConnectionConfig('{"base_url":"http://y"}').value).toEqual({
      base_url: 'http://y',
    })
  })

  it('非法 JSON / 数组 / 标量报错', () => {
    expect(parseConnectionConfig('{a:1}').errors.length).toBe(1)
    expect(parseConnectionConfig('[1]').errors).toContain(
      '连接块必须是 JSON 对象 ({ ... })',
    )
    expect(parseConnectionConfig('"x"').errors.length).toBe(1)
  })

  it('禁止列键 enabled/type', () => {
    const errors = parseConnectionConfig('{"enabled":true}').errors
    expect(errors.join()).toContain('enabled')
    expect(parseConnectionConfig('{"type":"http"}').errors.join()).toContain('type')
  })

  it('${VAR} 占位符密钥放行', () => {
    const parsed = parseConnectionConfig(
      '{"api_key":"${BETA_KEY}","env":{"token":"${T}"}}',
    )
    expect(parsed.errors).toEqual([])
  })
})

describe('plaintextSecretPaths', () => {
  it('疑似密钥键的非占位字符串被递归标出', () => {
    expect(plaintextSecretPaths({ api_key: 'sk-raw' })).toEqual(['api_key'])
    expect(
      plaintextSecretPaths({ env: { RHTHS_TOKEN: 'raw' } }),
    ).toEqual(['env.RHTHS_TOKEN'])
    expect(plaintextSecretPaths({ list: [{ secret: 'x' }] })).toEqual([
      'list[0].secret',
    ])
  })

  it('占位符与非疑似键不报', () => {
    expect(plaintextSecretPaths({ api_key: '${K}', base_url: 'http://x' })).toEqual([])
  })
})

describe('isUpstreamChanged', () => {
  it('type 变化即变更', () => {
    expect(isUpstreamChanged(entity(), entity().config, 'omni')).toBe(true)
  })

  it('键序不同但内容相同不算变更', () => {
    const ent = entity({ config: { a: 1, b: 2 } })
    expect(isUpstreamChanged(ent, { b: 2, a: 1 }, 'http')).toBe(false)
  })

  it('值变化算变更', () => {
    expect(
      isUpstreamChanged(entity(), { base_url: 'http://y', api_key: '${BETA_KEY}' }, 'http'),
    ).toBe(true)
  })
})
