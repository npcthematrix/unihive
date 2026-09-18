import { describe, expect, it } from 'vitest'
import {
  describeEnabledValue,
  describeTtlSeconds,
  formatOverlayValue,
  formatUpdatedAt,
  fuyaoToolOf,
  overlayKind,
  upstreamNameOf,
} from '@/views/config/overlays'

describe('overlayKind', () => {
  it('classifies upstream enabled paths (incl. dashed names) as hot toggles', () => {
    const info = overlayKind('upstreams.THS-LOCAL-GATEWAY.enabled')
    expect(info).toEqual({ kind: 'upstream-enabled', label: '上游开关', hot: true })
    expect(overlayKind('upstreams.fuyao_a_share.enabled').kind).toBe('upstream-enabled')
  })

  it('classifies fuyao tool TTL paths as hot', () => {
    expect(overlayKind('fuyao_cache.tools.get_a_share_history')).toEqual({
      kind: 'fuyao-ttl',
      label: 'FUYAO TTL',
      hot: true,
    })
  })

  it('classifies unknown paths as other (not hot)', () => {
    expect(overlayKind('cache.ttl.realtime_quote')).toEqual({
      kind: 'other',
      label: '其他',
      hot: false,
    })
  })

  it('does not misread near-miss paths', () => {
    expect(overlayKind('upstreams.a.enabled_extra').kind).toBe('other')
    expect(overlayKind('xupstreams.a.enabled').kind).toBe('other')
    expect(overlayKind('fuyao_cache.toolsx.t').kind).toBe('other')
  })
})

describe('upstreamNameOf / fuyaoToolOf', () => {
  it('extracts upstream name including dashes/underscores', () => {
    expect(upstreamNameOf('upstreams.THS-LOCAL-GATEWAY.enabled')).toBe('THS-LOCAL-GATEWAY')
    expect(upstreamNameOf('fuyao_cache.tools.get_x')).toBeNull()
    expect(upstreamNameOf('cache.ttl.x')).toBeNull()
  })

  it('extracts fuyao tool name', () => {
    expect(fuyaoToolOf('fuyao_cache.tools.get_a_share_history')).toBe(
      'get_a_share_history',
    )
    expect(fuyaoToolOf('upstreams.a.enabled')).toBeNull()
  })
})

describe('formatOverlayValue', () => {
  it('renders primitives plainly', () => {
    expect(formatOverlayValue(true)).toBe('true')
    expect(formatOverlayValue(false)).toBe('false')
    expect(formatOverlayValue(60)).toBe('60')
  })

  it('quotes strings and JSON-serializes objects', () => {
    expect(formatOverlayValue('http://x')).toBe('"http://x"')
    expect(formatOverlayValue({ a: 1 })).toBe('{"a":1}')
    expect(formatOverlayValue([1, 2])).toBe('[1,2]')
    expect(formatOverlayValue(null)).toBe('null')
  })
})

describe('describeEnabledValue', () => {
  it('maps booleans to Chinese state text', () => {
    expect(describeEnabledValue(true)).toBe('强制启用')
    expect(describeEnabledValue(false)).toBe('强制停用')
    expect(describeEnabledValue('x')).toBe('x')
  })
})

describe('describeTtlSeconds', () => {
  it('special-cases 0 as no-cache', () => {
    expect(describeTtlSeconds(0)).toBe('不缓存 (0s)')
  })

  it('humanizes seconds/minutes/hours/days', () => {
    expect(describeTtlSeconds(30)).toBe('30 秒')
    expect(describeTtlSeconds(60)).toBe('1 分钟')
    expect(describeTtlSeconds(90)).toBe('1.5 分钟')
    expect(describeTtlSeconds(3600)).toBe('1 小时')
    expect(describeTtlSeconds(86400)).toBe('1 天')
  })
})

describe('formatUpdatedAt', () => {
  it('formats ISO string to local compact time', () => {
    const out = formatUpdatedAt('2026-09-18T08:07:06+00:00')
    // 本地时区相关, 只断言日期结构存在
    expect(out).toMatch(/^2026-09-\d{2} \d{2}:\d{2}:\d{2}$/)
  })

  it('returns dash for empty and raw for unparseable', () => {
    expect(formatUpdatedAt('')).toBe('—')
    expect(formatUpdatedAt('not-a-date')).toBe('not-a-date')
  })
})
