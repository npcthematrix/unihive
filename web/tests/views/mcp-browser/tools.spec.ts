import { describe, expect, it } from 'vitest'
import type { RawTool, RawToolsResponse } from '@/api/mcp-browser'
import {
  annotationFlags,
  applyToolFilter,
  extraKeys,
  formatBytes,
  formatToolJson,
  hasActiveFilters,
  hasAnnotations,
  hasMeta,
  hasOutputSchema,
  isRecord,
  jsonBytes,
  requiredParams,
  rowKeyOf,
  schemaProperties,
  summarizePayload,
  toolDescription,
  toolName,
  toolTitle,
} from '@/views/mcp-browser/tools'

/** 测试夹具: 上游返回的原始对象, 形状并不受我们控制。 */
function makeTool(overrides: RawTool = {}): RawTool {
  return { name: 'vendor_market_login', description: '登录行情', ...overrides }
}

/** 带 title/annotations/_meta 的上游实测形状 */
const ANNOTATED_TOOL: RawTool = {
  name: 'vendor_market_login',
  title: '登录行情服务器',
  description: '登录本地行情服务器',
  inputSchema: {
    type: 'object',
    properties: {
      server: { type: 'string', description: '服务器地址' },
      timeout: { type: 'integer', enum: [5, 10, 30] },
    },
    required: ['server'],
  },
  annotations: {
    readOnlyHint: false,
    destructiveHint: true,
    idempotentHint: false,
    openWorldHint: true,
  },
  _meta: { vendor: { name: 'Vendor' } },
}

/** fuyao 实测形状: 无 title、无 annotations, 但有 outputSchema */
const FUYAO_TOOL: RawTool = {
  name: 'a_share_quote',
  description: 'A 股行情',
  inputSchema: {
    type: 'object',
    properties: { code: { type: 'string' } },
    required: ['code'],
  },
  outputSchema: { type: 'object' },
}

describe('isRecord', () => {
  it('对象为真', () => {
    expect(isRecord({})).toBe(true)
  })

  it.each([null, undefined, 42, 'str', [], [1, 2]])('%s 为假', (value) => {
    expect(isRecord(value)).toBe(false)
  })
})

describe('安全读取', () => {
  it('字段缺失时返回空串而不是 undefined', () => {
    const bare: RawTool = {}

    expect(toolName(bare)).toBe('')
    expect(toolTitle(bare)).toBe('')
    expect(toolDescription(bare)).toBe('')
  })

  it('字段类型不对时降级成空串而不是把数字抛到模板上', () => {
    const weird: RawTool = { name: 123, title: { a: 1 }, description: ['x'] }

    expect(toolName(weird)).toBe('')
    expect(toolTitle(weird)).toBe('')
    expect(toolDescription(weird)).toBe('')
  })

  it('正常字段原样返回', () => {
    expect(toolName(ANNOTATED_TOOL)).toBe('vendor_market_login')
    expect(toolTitle(ANNOTATED_TOOL)).toBe('登录行情服务器')
    expect(toolDescription(FUYAO_TOOL)).toBe('A 股行情')
  })
})

describe('rowKeyOf', () => {
  it('有 name 时用 name', () => {
    expect(rowKeyOf(makeTool())).toBe('vendor_market_login')
  })

  it('无名行按对象身份发稳定 id, 多个无名行不会互相撞', () => {
    // 回归: el-table row-key 回调只收到 row 一个参数, 旧实现的 index
    // 恒为 undefined, 2 个以上无名工具全部坍缩成 __row_undefined。
    const a = {}
    const b = {}
    const keyA = rowKeyOf(a)
    expect(keyA).toMatch(/^__row_\d+$/)
    expect(rowKeyOf(a)).toBe(keyA) // 同一对象 → 同一 key (展开态不串)
    expect(rowKeyOf(b)).not.toBe(keyA)
  })

  it('name 是空串也走身份兜底 (空串当不了 key)', () => {
    const row = { name: '' }
    expect(rowKeyOf(row)).toMatch(/^__row_\d+$/)
    expect(rowKeyOf(row)).toBe(rowKeyOf(row))
  })
})

describe('requiredParams', () => {
  it('正常取到字符串数组', () => {
    expect(requiredParams(ANNOTATED_TOOL)).toEqual(['server'])
  })

  it.each([
    ['inputSchema 缺失', {}],
    ['inputSchema 是 null', { inputSchema: null }],
    ['inputSchema 是字符串', { inputSchema: 'nope' }],
    ['required 缺失', { inputSchema: { type: 'object' } }],
    ['required 是字符串', { inputSchema: { required: 'server' } }],
    ['required 是 null', { inputSchema: { required: null } }],
  ])('%s 时返回空数组', (_label, tool) => {
    expect(requiredParams(tool)).toEqual([])
  })

  it('过滤掉非字符串项', () => {
    expect(requiredParams({ inputSchema: { required: ['a', 1, null, 'b'] } })).toEqual(['a', 'b'])
  })
})

describe('schemaProperties', () => {
  it('投影带注解上游形状 (含 description 与 enum)', () => {
    expect(schemaProperties(ANNOTATED_TOOL)).toEqual([
      {
        name: 'server',
        type: 'string',
        required: true,
        description: '服务器地址',
        enumValues: [],
      },
      {
        name: 'timeout',
        type: 'integer',
        required: false,
        description: '',
        enumValues: ['5', '10', '30'],
      },
    ])
  })

  it('投影 fuyao 形状', () => {
    expect(schemaProperties(FUYAO_TOOL)).toEqual([
      { name: 'code', type: 'string', required: true, description: '', enumValues: [] },
    ])
  })

  it.each([
    ['inputSchema 缺失', {}],
    ['inputSchema 为 null', { inputSchema: null }],
    ['inputSchema 是字符串', { inputSchema: 'nope' }],
    ['properties 缺失', { inputSchema: { type: 'object' } }],
    ['properties 为 null', { inputSchema: { properties: null } }],
    ['properties 是数组', { inputSchema: { properties: [1, 2] } }],
    ['properties 是空对象', { inputSchema: { properties: {} } }],
  ])('%s 时返回空数组且不抛错', (_label, tool) => {
    expect(schemaProperties(tool)).toEqual([])
  })

  it('属性值是字符串时降级, 不抛错', () => {
    expect(schemaProperties({ inputSchema: { properties: { weird: 'just-a-string' } } })).toEqual([
      { name: 'weird', type: '', required: false, description: '', enumValues: [] },
    ])
  })

  it('不改写原始对象', () => {
    const tool = makeTool({ inputSchema: { properties: { a: { type: 'string' } } } })
    const before = JSON.stringify(tool)

    schemaProperties(tool)

    expect(JSON.stringify(tool)).toBe(before)
  })
})

describe('形状标记', () => {
  it('hasOutputSchema 只认对象', () => {
    expect(hasOutputSchema(FUYAO_TOOL)).toBe(true)
    expect(hasOutputSchema(ANNOTATED_TOOL)).toBe(false)
    expect(hasOutputSchema({ outputSchema: 'nope' })).toBe(false)
  })

  it('hasMeta 认任何非 null 值, null 与 undefined 都不算', () => {
    expect(hasMeta(ANNOTATED_TOOL)).toBe(true)
    expect(hasMeta({ _meta: {} })).toBe(true)
    expect(hasMeta({ _meta: null })).toBe(false)
    expect(hasMeta({})).toBe(false)
  })

  it('hasAnnotations 只认对象', () => {
    expect(hasAnnotations(ANNOTATED_TOOL)).toBe(true)
    expect(hasAnnotations(FUYAO_TOOL)).toBe(false)
    expect(hasAnnotations({ annotations: [] })).toBe(false)
  })
})

describe('extraKeys', () => {
  it('列出界面没专门渲染的字段并排序', () => {
    expect(extraKeys({ name: 'a', zeta: 1, alpha: 2 })).toEqual(['alpha', 'zeta'])
  })

  it('已渲染的字段一个都不报', () => {
    expect(extraKeys(ANNOTATED_TOOL)).toEqual([])
  })

  it('未知键即使值是 undefined 也算存在', () => {
    expect(extraKeys({ name: 'a', extra: undefined })).toEqual(['extra'])
  })
})

describe('annotationFlags', () => {
  it('实测 payload 给出 4 个白名单序标签', () => {
    expect(annotationFlags(ANNOTATED_TOOL)).toEqual([
      { key: 'readOnlyHint', label: '只读', value: false },
      { key: 'destructiveHint', label: '破坏性', value: true },
      { key: 'idempotentHint', label: '幂等', value: false },
      { key: 'openWorldHint', label: '开放域', value: true },
    ])
  })

  it('只出现一部分时只列出现的那部分', () => {
    expect(annotationFlags({ annotations: { readOnlyHint: true } })).toEqual([
      { key: 'readOnlyHint', label: '只读', value: true },
    ])
  })

  it('未知 key 排序后追加, 不丢上游的新 hint', () => {
    const flags = annotationFlags({ annotations: { readOnlyHint: true, zzHint: true, aaHint: 1 } })

    expect(flags.map((f) => f.key)).toEqual(['readOnlyHint', 'aaHint', 'zzHint'])
    expect(flags[2]).toEqual({ key: 'zzHint', label: 'zzHint', value: true })
  })

  it.each([
    ['缺失', {}],
    ['是 null', { annotations: null }],
    ['是数组', { annotations: [] }],
    ['是字符串', { annotations: 'nope' }],
  ])('annotations %s 时返回空数组', (_label, tool) => {
    expect(annotationFlags(tool)).toEqual([])
  })

  it('非布尔值按真值收敛 (上游可能发 0/1)', () => {
    expect(annotationFlags({ annotations: { readOnlyHint: 0 } })[0].value).toBe(false)
    expect(annotationFlags({ annotations: { readOnlyHint: 1 } })[0].value).toBe(true)
  })
})

describe('hasActiveFilters', () => {
  const off = { meta: false, outputSchema: false, annotations: false }

  it('全 False 且无 query 时为假', () => {
    expect(hasActiveFilters('', off)).toBe(false)
    expect(hasActiveFilters('   ', off)).toBe(false)
  })

  it('任一开关打开即为真', () => {
    expect(hasActiveFilters('', { ...off, meta: true })).toBe(true)
  })

  it('有 query 即为真', () => {
    expect(hasActiveFilters('quote', off)).toBe(true)
  })
})

describe('applyToolFilter', () => {
  const off = { meta: false, outputSchema: false, annotations: false }
  const all = [ANNOTATED_TOOL, FUYAO_TOOL]

  it('无筛选时原样返回全部', () => {
    expect(applyToolFilter(all, '', off)).toHaveLength(2)
  })

  it('按 name 搜索且大小写不敏感', () => {
    expect(applyToolFilter(all, 'VENDOR', off)).toEqual([ANNOTATED_TOOL])
  })

  it('按 title 搜索', () => {
    expect(applyToolFilter(all, '登录行情服务器', off)).toEqual([ANNOTATED_TOOL])
  })

  it('按 description 搜索', () => {
    expect(applyToolFilter(all, 'A 股', off)).toEqual([FUYAO_TOOL])
  })

  it('没有 description 的工具在按 description 搜索时不炸', () => {
    const bare: RawTool = { name: 'bare' }

    expect(applyToolFilter([bare], '行情', off)).toEqual([])
    expect(applyToolFilter([bare], 'bare', off)).toEqual([bare])
  })

  it('query 两端空白被忽略', () => {
    expect(applyToolFilter(all, '  vendor  ', off)).toEqual([ANNOTATED_TOOL])
  })

  it('开关按与逻辑叠加', () => {
    expect(applyToolFilter(all, '', { ...off, meta: true })).toEqual([ANNOTATED_TOOL])
    expect(applyToolFilter(all, '', { ...off, outputSchema: true })).toEqual([FUYAO_TOOL])
    expect(applyToolFilter(all, '', { ...off, annotations: true })).toEqual([ANNOTATED_TOOL])
    expect(applyToolFilter(all, '', { ...off, meta: true, outputSchema: true })).toEqual([])
  })

  it('搜索与开关同时生效', () => {
    expect(applyToolFilter(all, 'vendor', { ...off, outputSchema: true })).toEqual([])
    expect(applyToolFilter(all, 'vendor', { ...off, meta: true })).toEqual([ANNOTATED_TOOL])
  })

  it('不改写输入数组', () => {
    const input = [...all]

    applyToolFilter(input, 'vendor', off)

    expect(input).toEqual(all)
  })
})

describe('formatToolJson', () => {
  it('可被 JSON.parse 回原对象 (证明视图不丢信息)', () => {
    expect(JSON.parse(formatToolJson(ANNOTATED_TOOL))).toEqual(ANNOTATED_TOOL)
  })

  it('是缩进过的多行文本', () => {
    expect(formatToolJson({ a: 1 })).toBe('{\n  "a": 1\n}')
  })

  it('畸形输入返回 undefined 而不是抛错', () => {
    expect(formatToolJson({ fn: undefined } as RawTool)).toBe('{}')
  })
})

describe('jsonBytes', () => {
  it('中文描述按 UTF-8 计, 必须大于 text.length', () => {
    const text = formatToolJson(ANNOTATED_TOOL)

    expect(jsonBytes(text)).toBeGreaterThan(text.length)
  })

  it('纯 ASCII 时与 length 一致', () => {
    expect(jsonBytes('abc')).toBe(3)
  })

  it('与 TextEncoder 的结果一致', () => {
    const text = '登录行情 vendor'

    expect(jsonBytes(text)).toBe(new TextEncoder().encode(text).length)
  })
})

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [512, '512 B'],
    [1023, '1023 B'],
    [1024, '1.0 KB'],
    [158844, '155.1 KB'],
    [1024 * 1024, '1.0 MB'],
    [3 * 1024 * 1024, '3.0 MB'],
  ])('%i → %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected)
  })
})

describe('summarizePayload', () => {
  function makeResponse(overrides: Partial<RawToolsResponse> = {}): RawToolsResponse {
    return {
      upstream: 'vendor-gateway',
      timestamp: 1758000000,
      ok: true,
      error: null,
      errorKind: null,
      pages: 3,
      truncated: false,
      truncatedReason: null,
      count: 87,
      bytes: 158844,
      tools: [],
      ...overrides,
    }
  }

  it('拼出 工具数 · 页数 · 体积', () => {
    expect(summarizePayload(makeResponse())).toBe('87 个工具 · 3 页 · 155.1 KB')
  })

  it('单页上游如实显示 1 页', () => {
    expect(summarizePayload(makeResponse({ count: 2, pages: 1, bytes: 700 }))).toBe(
      '2 个工具 · 1 页 · 700 B',
    )
  })

  it('空态也能拼出不误导的文案', () => {
    expect(summarizePayload(makeResponse({ count: 0, pages: 1, bytes: 42 }))).toBe(
      '0 个工具 · 1 页 · 42 B',
    )
  })
})
