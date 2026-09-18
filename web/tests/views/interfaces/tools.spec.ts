import { describe, expect, it } from 'vitest'
import type { ToolSpec } from '@/api/interfaces'
import {
  ALL_KEY,
  KNOWN_GROUP_ORDER,
  applyToolsFilter,
  bucketByGroup,
  buildGroupOrder,
  firstGroupWithMatch,
  hasActiveFilters,
  hasCache,
  hasNoParams,
  isParamPlaceholder,
  paramName,
  previewParamNames,
} from '@/views/interfaces/tools'

/** 测试夹具: 用最少字段满足类型, 避免每个用例重复 boilerplate。 */
function makeTool(overrides: Partial<ToolSpec> = {}): ToolSpec {
  return {
    name: 'tdx_get_quote',
    description: '获取行情',
    source: 'tdx_quant',
    dangerous: false,
    cache_ttl_key: null,
    cache_ttl_seconds: null,
    data_source_type: 'online',
    routing: null,
    chain: ['tdx_quant'],
    params: [],
    ...overrides,
  }
}

describe('isParamPlaceholder', () => {
  it('字符串 "?" 是占位', () => {
    expect(isParamPlaceholder('?')).toBe(true)
  })

  it('其他字符串不是占位', () => {
    expect(isParamPlaceholder('code')).toBe(false)
    expect(isParamPlaceholder('')).toBe(false)
  })

  it('对象参数不是占位 (不论 name)', () => {
    expect(isParamPlaceholder({ name: '?' })).toBe(false)
    expect(isParamPlaceholder({ name: 'code' })).toBe(false)
  })
})

describe('paramName', () => {
  it('字符串返回自身', () => {
    expect(paramName('code')).toBe('code')
  })

  it('对象返回 name 字段', () => {
    expect(paramName({ name: 'code', type: 'string' })).toBe('code')
  })

  it('对象 name 缺失返空串 (而 undefined)', () => {
    expect(paramName({ name: '' })).toBe('')
  })
})

describe('hasNoParams', () => {
  it('params 空数组 → true', () => {
    expect(hasNoParams(makeTool({ params: [] }))).toBe(true)
  })

  it('params 缺失 → true (走 tool.params || [])', () => {
    expect(hasNoParams(makeTool({ params: undefined as unknown as [] }))).toBe(true)
  })

  it('单元素 "?" 占位 → true', () => {
    expect(hasNoParams(makeTool({ params: ['?'] }))).toBe(true)
  })

  it('正常参数 → false', () => {
    expect(hasNoParams(makeTool({ params: ['code'] }))).toBe(false)
  })

  it('两个参数 (即使有一个 "?") → false', () => {
    expect(hasNoParams(makeTool({ params: ['?', 'code'] }))).toBe(false)
  })

  it('一个对象参数 → false (hasNoParams 只认字符串 "?" 占位)', () => {
    expect(hasNoParams(makeTool({ params: [{ name: 'code' }] }))).toBe(false)
  })
})

describe('hasCache', () => {
  it('cache_ttl_key 为 null → false', () => {
    expect(hasCache(makeTool({ cache_ttl_key: null }))).toBe(false)
  })

  it('cache_ttl_key 为空串 → false', () => {
    expect(hasCache(makeTool({ cache_ttl_key: '' }))).toBe(false)
  })

  it('cache_ttl_key 为 realtime_quote → false (实时不缓存)', () => {
    expect(hasCache(makeTool({ cache_ttl_key: 'realtime_quote' }))).toBe(false)
  })

  it('cache_ttl_key 为其他键 → true', () => {
    expect(hasCache(makeTool({ cache_ttl_key: 'historical' }))).toBe(true)
    expect(hasCache(makeTool({ cache_ttl_key: 'fundamentals' }))).toBe(true)
  })
})

describe('previewParamNames', () => {
  it('空参数 → []', () => {
    expect(previewParamNames(makeTool({ params: [] }))).toEqual([])
  })

  it('字符串参数 → 名字数组', () => {
    expect(previewParamNames(makeTool({ params: ['code', 'date'] }))).toEqual(['code', 'date'])
  })

  it('过滤 "?" 占位', () => {
    expect(previewParamNames(makeTool({ params: ['?', 'code'] }))).toEqual(['code'])
  })

  it('对象参数 → 取 name', () => {
    expect(
      previewParamNames(makeTool({ params: [{ name: 'code', type: 'string' }] })),
    ).toEqual(['code'])
  })

  it('空 name 过滤掉', () => {
    expect(previewParamNames(makeTool({ params: [{ name: '' }, 'code'] }))).toEqual(['code'])
  })

  it('混合 — 字符串、对象、占位、空名混在一起', () => {
    expect(
      previewParamNames(
        makeTool({
          params: ['?', 'code', { name: '' }, { name: 'date' }, 'extra'],
        }),
      ),
    ).toEqual(['code', 'date', 'extra'])
  })
})

describe('hasActiveFilters', () => {
  const emptyToggles = { dangerous: false, cache: false, noparam: false }

  it('全空 → false', () => {
    expect(hasActiveFilters('', emptyToggles, '')).toBe(false)
  })

  it('仅 query → true', () => {
    expect(hasActiveFilters('sh', emptyToggles, '')).toBe(true)
  })

  it('仅 toggle → true', () => {
    expect(hasActiveFilters('', { ...emptyToggles, dangerous: true }, '')).toBe(true)
  })

  it('仅 dataSource → true', () => {
    expect(hasActiveFilters('', emptyToggles, 'online')).toBe(true)
  })

  it('空 query (whitespace) 仍 truthy → true (源函数用 Boolean 不 trim)', () => {
    // 注: applyToolsFilter 内部会 trim, 但 hasActiveFilters 仅用 Boolean 判断。
    // 这是有意为之 — 调用方在 query 为 whitespace 时通常已经判定无意义,
    // 但语义一致性以 Boolean 为准。锁住现有行为避免无意识重构。
    expect(hasActiveFilters('   ', emptyToggles, '')).toBe(true)
  })
})

describe('applyToolsFilter', () => {
  const t1 = makeTool({ name: 't1', description: '行情', source: 'tdx_quant' })
  const t2 = makeTool({
    name: 't2',
    description: '板块',
    source: 'mootdx2',
    dangerous: true,
    cache_ttl_key: 'historical',
  })
  const t3 = makeTool({
    name: 't3',
    description: '纯本地',
    source: 'omni',
    cache_ttl_key: 'fundamentals',
    data_source_type: 'offline',
  })
  const t4 = makeTool({
    name: 't4',
    description: '实时',
    source: 'tdx_quant',
    cache_ttl_key: 'realtime_quote',
    data_source_type: 'online',
  })

  it('无过滤 → 全部返回', () => {
    const out = applyToolsFilter([t1, t2, t3, t4], '', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toEqual([t1, t2, t3, t4])
  })

  it('query 命中 name', () => {
    const out = applyToolsFilter([t1, t2, t3], 't2', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toEqual([t2])
  })

  it('query 命中 description', () => {
    const out = applyToolsFilter([t1, t2, t3], '板块', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toEqual([t2])
  })

  it('query 命中 source', () => {
    const out = applyToolsFilter([t1, t2, t3], 'omni', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toEqual([t3])
  })

  it('query 大小写不敏感', () => {
    const out = applyToolsFilter([t1, t2], 'T1', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toEqual([t1])
  })

  it('query 修剪空白', () => {
    const out = applyToolsFilter([t1, t2], '  t1  ', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toEqual([t1])
  })

  it('query 无匹配 → []', () => {
    expect(applyToolsFilter([t1, t2], 'zzz', { dangerous: false, cache: false, noparam: false }, '')).toEqual([])
  })

  it('dangerous toggle 只保留 dangerous=true', () => {
    const out = applyToolsFilter([t1, t2, t3], '', { dangerous: true, cache: false, noparam: false }, '')
    expect(out).toEqual([t2])
  })

  it('cache toggle 排除 realtime_quote', () => {
    const out = applyToolsFilter([t1, t2, t3, t4], '', { dangerous: false, cache: true, noparam: false }, '')
    // t1 cache=null, t4 realtime_quote → 都不算; t2/t3 命中
    expect(out).toEqual([t2, t3])
  })

  it('noparam toggle 只保留无参', () => {
    const withParams = makeTool({ name: 'p', params: ['code'] })
    const out = applyToolsFilter([t1, withParams], '', { dangerous: false, cache: false, noparam: true }, '')
    expect(out).toEqual([t1])
  })

  it('dataSource 过滤', () => {
    const out = applyToolsFilter([t1, t2, t3], '', { dangerous: false, cache: false, noparam: false }, 'online')
    // t1=online, t2=online, t3=offline
    expect(out).toEqual([t1, t2])
  })

  it('组合 — query + dangerous', () => {
    const out = applyToolsFilter([t1, t2, t3], '板块', { dangerous: true, cache: false, noparam: false }, '')
    expect(out).toEqual([t2])
  })

  it('组合 — 空 dataSource 字符串不过滤 (与 truthy 检查一致)', () => {
    const out = applyToolsFilter([t1, t2], '', { dangerous: false, cache: false, noparam: false }, '')
    expect(out).toHaveLength(2)
  })
})

describe('bucketByGroup', () => {
  it('空列表 → 空 Map', () => {
    expect(bucketByGroup([]).size).toBe(0)
  })

  it('按 source 分桶', () => {
    const t1 = makeTool({ name: 't1', source: 'tdx_quant' })
    const t2 = makeTool({ name: 't2', source: 'mootdx2' })
    const t3 = makeTool({ name: 't3', source: 'tdx_quant' })
    const map = bucketByGroup([t1, t2, t3])
    expect(map.get('tdx_quant')).toEqual([t1, t3])
    expect(map.get('mootdx2')).toEqual([t2])
    expect(map.size).toBe(2)
  })

  it('source 缺失 → 落到空字符串桶', () => {
    const t1 = makeTool({ name: 't1', source: '' })
    const map = bucketByGroup([t1])
    expect(map.get('')).toEqual([t1])
  })
})

describe('buildGroupOrder', () => {
  it('已知顺序在前', () => {
    expect(buildGroupOrder(['mootdx2', 'tdx_quant'])).toEqual(['tdx_quant', 'mootdx2'])
  })

  it('未知 source 跟在已知后', () => {
    expect(
      buildGroupOrder(['omni', 'mootdx2', 'unknown_x', 'tdx_quant']),
    ).toEqual(['tdx_quant', 'mootdx2', 'omni', 'unknown_x'])
  })

  it('空输入 → []', () => {
    expect(buildGroupOrder([])).toEqual([])
  })

  it('全是未知 → 保持原顺序', () => {
    expect(buildGroupOrder(['a', 'b', 'c'])).toEqual(['a', 'b', 'c'])
  })

  it('KNOWN_GROUP_ORDER 是只读数组', () => {
    expect(KNOWN_GROUP_ORDER).toContain('tdx_quant')
    expect(KNOWN_GROUP_ORDER).toContain('mootdx2')
    expect(KNOWN_GROUP_ORDER).toContain('fuyao_ashare')
  })
})

describe('firstGroupWithMatch', () => {
  it('空 buckets + 空 order → ALL_KEY', () => {
    expect(firstGroupWithMatch(new Map(), [])).toBe(ALL_KEY)
  })

  it('buckets 全空 → ALL_KEY', () => {
    const buckets = new Map([['tdx_quant', [] as ToolSpec[]]])
    expect(firstGroupWithMatch(buckets, ['tdx_quant'])).toBe(ALL_KEY)
  })

  it('返回第一个非空桶的 key', () => {
    const buckets = new Map([
      ['mootdx2', [] as ToolSpec[]],
      ['tdx_quant', [makeTool()]],
      ['fuyao_ashare', [makeTool()]],
    ])
    expect(firstGroupWithMatch(buckets, ['mootdx2', 'tdx_quant', 'fuyao_ashare'])).toBe('tdx_quant')
  })

  it('order 中存在但 buckets 缺该 key → 跳过 (buckets.get 返 undefined → [])', () => {
    const buckets = new Map([['tdx_quant', [makeTool()]]])
    expect(firstGroupWithMatch(buckets, ['mootdx2', 'tdx_quant'])).toBe('tdx_quant')
  })

  it('ALL_KEY 是 "__all__"', () => {
    expect(ALL_KEY).toBe('__all__')
  })
})
