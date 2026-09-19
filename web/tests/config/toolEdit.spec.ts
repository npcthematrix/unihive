import { describe, expect, it } from 'vitest'
import type { ToolEntity } from '@/api/config'
import {
  buildToolPatch,
  contractFieldsInPatch,
  isContractField,
  isValidEnvVarName,
  isValidToolName,
  mappingFromForm,
  paramsFromForm,
  paramsToForm,
  toolFormFromEntity,
  type ToolEditForm,
} from '@/views/config/toolEdit'

function entity(overrides: Partial<ToolEntity> = {}): ToolEntity {
  return {
    name: 'a_one',
    routing: 'a_one',
    description: 'first',
    cache_ttl_key: 'historical',
    requires_env: null,
    publish_mode: 'default',
    mapping: { alpha: 'a_one' },
    spec: {
      name: 'a_one',
      routing: 'a_one',
      description: 'first',
      cache_ttl_key: 'historical',
      params: [
        { name: 'code', type: 'str', required: true, description: '代码' },
      ],
    },
    source_file: 'tools_alpha.yaml',
    position: 0,
    origin: 'builtin',
    updated_at: '2026-09-18T00:00:00+00:00',
    ...overrides,
  }
}

describe('字段分层', () => {
  it('isContractField 与后端 TOOL_CONTRACT_FIELDS 一致', () => {
    for (const f of ['name', 'routing', 'requires_env', 'mapping']) {
      expect(isContractField(f)).toBe(true)
    }
    for (const f of ['description', 'params', 'cache_ttl_key', 'publish_mode']) {
      expect(isContractField(f)).toBe(false)
    }
  })

  it('contractFieldsInPatch 按固定顺序返回触及字段', () => {
    expect(contractFieldsInPatch({ mapping: {}, name: 'x' })).toEqual([
      'name',
      'mapping',
    ])
    expect(contractFieldsInPatch({ description: 'd' })).toEqual([])
  })
})

describe('正则', () => {
  it('工具名: 字母开头, 字母数字下划线', () => {
    expect(isValidToolName('a_one')).toBe(true)
    expect(isValidToolName('A1')).toBe(true)
    expect(isValidToolName('1a')).toBe(false)
    expect(isValidToolName('_a')).toBe(false)
    expect(isValidToolName('a-b')).toBe(false)
    expect(isValidToolName('')).toBe(false)
  })

  it('环境变量名: 空串合法 (= 清除), 其余须合法标识符', () => {
    expect(isValidEnvVarName('')).toBe(true)
    expect(isValidEnvVarName('ALLOW_X')).toBe(true)
    expect(isValidEnvVarName('_A1')).toBe(true)
    expect(isValidEnvVarName('1A')).toBe(false)
    expect(isValidEnvVarName('A-B')).toBe(false)
  })
})

describe('params 表单转换', () => {
  it('裸字符串参数归一化为只有 name 的行', () => {
    const rows = paramsToForm(['code'])
    expect(rows).toEqual([
      { name: 'code', type: '', required: false, description: '', enumText: '', extra: {} },
    ])
  })

  it('对象参数保留全部字段, enum 序列化为 JSON 文本, 额外键进 extra', () => {
    const rows = paramsToForm([
      { name: 'p', type: 'int', required: false, description: 'd', enum: [1, 2], default: 0 },
    ])
    expect(rows[0].enumText).toBe('[1,2]')
    expect(rows[0].extra).toEqual({ default: 0 })
  })

  it('全空行被忽略', () => {
    const built = paramsFromForm([
      { name: '', type: '', required: false, description: '', enumText: '', extra: {} },
    ])
    expect(built.errors).toEqual([])
    expect(built.value).toEqual([])
  })

  it('非空但缺 name / 重名报错', () => {
    expect(
      paramsFromForm([
        { name: '', type: 'str', required: false, description: '', enumText: '', extra: {} },
      ]).errors,
    ).toHaveLength(1)
    const dup = paramsFromForm([
      { name: 'a', type: '', required: false, description: '', enumText: '', extra: {} },
      { name: 'a', type: '', required: false, description: '', enumText: '', extra: {} },
    ])
    expect(dup.errors.join()).toContain('重复')
  })

  it('enumText 非法 JSON / 非数组 / 含布尔值报错', () => {
    const row = (enumText: string) => ({
      name: 'p', type: '', required: false, description: '', enumText, extra: {},
    })
    expect(paramsFromForm([row('{a:1}')]).errors.length).toBe(1)
    expect(paramsFromForm([row('"x"')]).errors.length).toBe(1)
    expect(paramsFromForm([row('[true]')]).errors.length).toBe(1)
    expect(paramsFromForm([row('["a", 1]')]).value).toEqual([
      { name: 'p', required: false, enum: ['a', 1] },
    ])
  })

  it('构建结果省略空 type/description, 保留 extra 键', () => {
    const built = paramsFromForm([
      { name: 'p', type: '', required: true, description: '', enumText: '', extra: { default: 5 } },
    ])
    expect(built.value).toEqual([{ name: 'p', required: true, default: 5 }])
  })
})

describe('mapping 表单转换', () => {
  it('全空行忽略; 只填一侧报错; 重名报错', () => {
    expect(mappingFromForm([{ upstream: '', method: '' }]).value).toEqual({})
    expect(mappingFromForm([{ upstream: 'a', method: '' }]).errors).toHaveLength(1)
    const dup = mappingFromForm([
      { upstream: 'a', method: 'm1' },
      { upstream: 'a', method: 'm2' },
    ])
    expect(dup.errors).toHaveLength(1)
  })

  it('正常构建', () => {
    expect(
      mappingFromForm([
        { upstream: 'alpha', method: 'a_one' },
        { upstream: 'beta', method: 'a_one_b' },
      ]).value,
    ).toEqual({ alpha: 'a_one', beta: 'a_one_b' })
  })
})

describe('buildToolPatch', () => {
  it('表单未改 → 空 patch (调用方按 no-op 处理)', () => {
    const ent = entity()
    const built = buildToolPatch(toolFormFromEntity(ent), ent)
    expect(built.errors).toEqual([])
    expect(built.patch).toEqual({})
    expect(built.contractFields).toEqual([])
  })

  it('仅改描述 → 描述层 patch, 无契约字段', () => {
    const ent = entity()
    const form: ToolEditForm = { ...toolFormFromEntity(ent), description: '新描述' }
    const built = buildToolPatch(form, ent)
    expect(built.patch).toEqual({ description: '新描述' })
    expect(built.contractFields).toEqual([])
  })

  it('改 cache_ttl_key / hidden / 参数都属于描述层', () => {
    const ent = entity()
    const form = toolFormFromEntity(ent)
    form.cacheTtlKey = 'fundamentals'
    form.publishHidden = true
    form.params[0].description = '改参数描述'
    const built = buildToolPatch(form, ent)
    expect(built.patch.cache_ttl_key).toBe('fundamentals')
    expect(built.patch.publish_mode).toBe('hidden')
    expect(built.patch.params).toBeDefined()
    expect(built.contractFields).toEqual([])
  })

  it('清空 cache_ttl_key 与 requires_env 下发 null', () => {
    const ent = entity({ requires_env: 'ALLOW_X' })
    const spec = { ...ent.spec, requires_env: 'ALLOW_X' }
    const ent2 = { ...ent, spec }
    const form = toolFormFromEntity(ent2)
    form.requiresEnv = ''
    form.cacheTtlKey = ''
    const built = buildToolPatch(form, ent2)
    expect(built.patch.requires_env).toBeNull()
    expect(built.patch.cache_ttl_key).toBeNull()
  })

  it('改名 + 改 mapping → 契约字段按固定顺序', () => {
    const ent = entity()
    const form = toolFormFromEntity(ent)
    form.name = 'a_one_v2'
    form.mapping = [{ upstream: 'beta', method: 'other' }]
    const built = buildToolPatch(form, ent)
    expect(built.patch.name).toBe('a_one_v2')
    expect(built.patch.mapping).toEqual({ beta: 'other' })
    expect(built.contractFields).toEqual(['name', 'mapping'])
  })

  it('键序不同的 mapping 不产生 patch', () => {
    const ent = entity({ mapping: { a: 'm', b: 'n' } })
    const form = toolFormFromEntity(ent)
    form.mapping = [{ upstream: 'b', method: 'n' }, { upstream: 'a', method: 'm' }]
    expect(buildToolPatch(form, ent).patch).toEqual({})
  })

  it('实体 params 为裸字符串, 表单未动 → 不产生 params patch', () => {
    const ent = entity({
      spec: { name: 'a_one', routing: 'a_one', params: ['code'] },
    })
    const form = toolFormFromEntity(ent)
    expect(buildToolPatch(form, ent).patch).toEqual({})
  })

  it('表单有形态错误 → errors 非空且不下发 patch', () => {
    const ent = entity()
    const form = toolFormFromEntity(ent)
    form.name = '1-bad'
    form.routing = ''
    const built = buildToolPatch(form, ent)
    expect(built.errors.length).toBeGreaterThanOrEqual(2)
    expect(built.patch).toEqual({})
  })
})
