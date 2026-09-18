/**
 * fuyaoEndpointOf — gateway tool → FUYAO endpoint 派生 (2026-09-18 H2 回归)。
 * 旧实现是恒返 undefined 的占位, 导致 FuyaoCachePanel 4 个折叠区全部
 * 显示"0 个工具"、批量设置/清空静默 no-op。
 */
import { describe, expect, test } from 'vitest'
import { fuyaoEndpointOf } from '@/api/config'

const mapping: Record<string, Record<string, string>> = {
  // 单 fuyao backer
  a_share_prices_snapshot: { fuyao_ashare: 'get_a_share_prices_snapshot' },
  meta_tickers_list: { fuyao_meta: 'get_tickers_list' },
  fund_nav: { fuyao_fund: 'get_fund_nav' },
  index_kline: { fuyao_index: 'get_index_kline' },
  // 多 backer 降级链: fuyao + 非 fuyao
  shared_with_tdx: { tdx_quant: 'x', fuyao_ashare: 'get_shared' },
  // 多 fuyao backer: 按 ENDPOINT_KEYS 固定顺序 (ashare→index→meta→fund)
  multi_fuyao: { fuyao_fund: 'a', fuyao_index: 'b' },
  // 纯非 fuyao 工具
  mootdx_only: { mootdx2: 'get_quote' },
}

describe('fuyaoEndpointOf', () => {
  test('4 个 endpoint 各自命中', () => {
    expect(fuyaoEndpointOf('a_share_prices_snapshot', mapping)).toBe('fuyao_ashare')
    expect(fuyaoEndpointOf('index_kline', mapping)).toBe('fuyao_index')
    expect(fuyaoEndpointOf('meta_tickers_list', mapping)).toBe('fuyao_meta')
    expect(fuyaoEndpointOf('fund_nav', mapping)).toBe('fuyao_fund')
  })

  test('降级链含非 fuyao 上游时仍识别 fuyao backer', () => {
    expect(fuyaoEndpointOf('shared_with_tdx', mapping)).toBe('fuyao_ashare')
  })

  test('多个 fuyao backer 时按固定顺序取第一个 (index 在 fund 前)', () => {
    expect(fuyaoEndpointOf('multi_fuyao', mapping)).toBe('fuyao_index')
  })

  test('无 fuyao backer / 未知工具 → undefined', () => {
    expect(fuyaoEndpointOf('mootdx_only', mapping)).toBeUndefined()
    expect(fuyaoEndpointOf('does_not_exist', mapping)).toBeUndefined()
  })

  test('mapping 缺失/null → undefined (面板不渲染该工具)', () => {
    expect(fuyaoEndpointOf('a_share_prices_snapshot', undefined)).toBeUndefined()
    expect(fuyaoEndpointOf('a_share_prices_snapshot', null)).toBeUndefined()
    expect(fuyaoEndpointOf('a_share_prices_snapshot')).toBeUndefined()
  })
})
