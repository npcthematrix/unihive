import { describe, expect, it } from 'vitest'
import type {
  FactoryTemplateStatus,
  UpgradeConflict,
  UpgradeImpact,
  UpgradePreview,
} from '@/api/config'
import {
  actionLabel,
  actionTagType,
  canApplyUpgrade,
  conflictKey,
  decideAll,
  formatConflictValue,
  impactGroups,
  hasImpact,
  isDbStatus,
  isResolution,
  kindLabel,
  meaningfulChanges,
  pendingConflicts,
  withResolution,
} from '@/views/config/updateCenter'

function conflict(over: Partial<UpgradeConflict> = {}): UpgradeConflict {
  return {
    kind: 'tool',
    name: 'a_one',
    path: 'description',
    conflict: 'both_changed',
    resolution: 'keep_cur',
    old: 'factory v1',
    cur: 'user desc',
    new: 'factory v2',
    ...over,
  }
}

const C1_KEY = 'tool:a_one:description'
const C2_KEY = 'tool:a_one:spec.description'

function preview(over: Partial<UpgradePreview> = {}): UpgradePreview {
  return {
    changed: true,
    revision: 3,
    old_version: 'a'.repeat(64),
    new_version: 'b'.repeat(64),
    template_changed: true,
    summary: { added: 0, removed: 0, updated: 1, kept_user: 0, conflicts: 2 },
    sections: [],
    upstreams_changes: [],
    tools_changes: [],
    conflicts: [],
    resolutions_applied: [],
    lint_errors: [],
    lint_warnings: [],
    impact: {
      tools_added: [],
      tools_removed: [],
      tools_republished: [],
      upstreams_attach: [],
      upstreams_reconnect: [],
      upstreams_detach: [],
    },
    ...over,
  }
}

describe('conflictKey', () => {
  it('拼出与后端同构的 kind:name:path 键', () => {
    expect(conflictKey(conflict())).toBe('tool:a_one:description')
    expect(conflictKey({ kind: 'upstream', name: 'beta', path: 'config.api_key' })).toBe(
      'upstream:beta:config.api_key',
    )
  })
})

describe('isResolution', () => {
  it('只接受两个合法裁决值', () => {
    expect(isResolution('keep_cur')).toBe(true)
    expect(isResolution('accept_new')).toBe(true)
    expect(isResolution('accept_old')).toBe(false)
    expect(isResolution(undefined)).toBe(false)
    expect(isResolution('')).toBe(false)
  })
})

describe('pendingConflicts', () => {
  const conflicts = [
    conflict(),
    conflict({ path: 'spec.description', resolution: 'accept_new' }),
  ]

  it('空裁决表: 全部未裁决 (apply 前置门控要求逐条确认)', () => {
    expect(pendingConflicts(conflicts, {})).toHaveLength(2)
  })

  it('裁决值非法时仍视为未裁决', () => {
    const pending = pendingConflicts(conflicts, {
      [C1_KEY]: 'accept_new',
      [C2_KEY]: 'accept_old' as unknown as 'keep_cur',
    })
    expect(pending.map((c) => c.path)).toEqual(['spec.description'])
  })

  it('显式 keep_cur 也算已裁决', () => {
    const pending = pendingConflicts(conflicts, {
      [C1_KEY]: 'keep_cur',
      [C2_KEY]: 'keep_cur',
    })
    expect(pending).toHaveLength(0)
  })
})

describe('withResolution / decideAll', () => {
  it('withResolution 不可变更新', () => {
    const original = { [C1_KEY]: 'keep_cur' as const }
    const next = withResolution(original, C1_KEY, 'accept_new')
    expect(next[C1_KEY]).toBe('accept_new')
    expect(original[C1_KEY]).toBe('keep_cur')
  })

  it('值未变时返回原引用 (避免无谓重渲染)', () => {
    const original = { [C1_KEY]: 'keep_cur' as const }
    expect(withResolution(original, C1_KEY, 'keep_cur')).toBe(original)
  })

  it('decideAll 批量裁决覆盖既有选择', () => {
    const conflicts = [conflict(), conflict({ path: 'spec.description' })]
    const next = decideAll(conflicts, 'accept_new')
    expect(Object.keys(next)).toHaveLength(2)
    expect(Object.values(next).every((v) => v === 'accept_new')).toBe(true)
  })
})

describe('canApplyUpgrade', () => {
  const conflicts = [conflict(), conflict({ path: 'spec.description' })]

  it('无 preview 不可应用', () => {
    expect(canApplyUpgrade(null, {})).toBe(false)
  })

  it('changed=false (已是最新) 不可应用', () => {
    expect(canApplyUpgrade(preview({ changed: false }), {})).toBe(false)
  })

  it('有未裁决冲突不可应用', () => {
    const p = preview({ conflicts })
    expect(canApplyUpgrade(p, { [C1_KEY]: 'keep_cur' })).toBe(false)
  })

  it('lint error 存在不可应用, 即使冲突全裁决', () => {
    const p = preview({
      conflicts,
      lint_errors: [{ severity: 'error', code: 'M001', message: 'bad' }],
    })
    expect(canApplyUpgrade(p, decideAll(conflicts, 'keep_cur'))).toBe(false)
  })

  it('冲突全裁决且无 lint error 才可应用', () => {
    const p = preview({ conflicts })
    expect(canApplyUpgrade(p, decideAll(conflicts, 'keep_cur'))).toBe(true)
  })
})

describe('actionLabel / actionTagType / kindLabel', () => {
  it('已知处置给中文标签', () => {
    expect(actionLabel('added')).toBe('新增')
    expect(actionLabel('removed')).toBe('删除')
    expect(actionLabel('updated')).toBe('更新')
    expect(actionLabel('kept_user')).toBe('保留用户改动')
    expect(actionLabel('unchanged')).toBe('无变化')
  })

  it('未知 action 原样透传', () => {
    expect(actionLabel('future_action')).toBe('future_action')
  })

  it('tag type 与处置语义对应', () => {
    expect(actionTagType('added')).toBe('success')
    expect(actionTagType('removed')).toBe('danger')
    expect(actionTagType('updated')).toBe('primary')
    expect(actionTagType('kept_user')).toBe('warning')
    expect(actionTagType('unchanged')).toBe('info')
  })

  it('实体类别中文标签', () => {
    expect(kindLabel('section')).toBe('配置段')
    expect(kindLabel('upstream')).toBe('上游')
    expect(kindLabel('tool')).toBe('工具')
    expect(kindLabel('future')).toBe('future')
  })
})

describe('meaningfulChanges', () => {
  it('过滤掉 unchanged 行', () => {
    const rows = meaningfulChanges([
      { kind: 'tool', name: 'a', action: 'updated', fields: ['x'], conflicts: [] },
      { kind: 'tool', name: 'b', action: 'unchanged', fields: [], conflicts: [] },
      { kind: 'tool', name: 'c', action: 'added', fields: [], conflicts: [] },
    ])
    expect(rows.map((r) => r.name)).toEqual(['a', 'c'])
  })
})

describe('formatConflictValue', () => {
  it('null/undefined 显示为 —', () => {
    expect(formatConflictValue(null)).toBe('—')
    expect(formatConflictValue(undefined)).toBe('—')
  })

  it('对象缩进 JSON, 字符串原样', () => {
    expect(formatConflictValue('x')).toBe('x')
    expect(formatConflictValue(42)).toBe('42')
    expect(formatConflictValue({ a: 1 })).toBe('{\n  "a": 1\n}')
    expect(formatConflictValue([1, 2])).toBe('[\n  1,\n  2\n]')
  })

  it('循环引用对象回落到 String', () => {
    const cyclic: Record<string, unknown> = {}
    cyclic.self = cyclic
    expect(formatConflictValue(cyclic)).toBe('[object Object]')
  })
})

describe('impactGroups / hasImpact', () => {
  const empty: UpgradeImpact = {
    tools_added: [],
    tools_removed: [],
    tools_republished: [],
    upstreams_attach: [],
    upstreams_reconnect: [],
    upstreams_detach: [],
  }

  it('全空时无展示行', () => {
    expect(impactGroups(empty)).toHaveLength(0)
    expect(hasImpact(empty)).toBe(false)
  })

  it('只保留非空清单且顺序固定 (工具在上, 连接在后)', () => {
    const impact: UpgradeImpact = {
      ...empty,
      tools_added: ['c_three'],
      tools_republished: ['a_one'],
      upstreams_detach: ['beta'],
    }
    const groups = impactGroups(impact)
    expect(groups.map((g) => g.key)).toEqual([
      'tools_added',
      'tools_republished',
      'upstreams_detach',
    ])
    expect(groups[0].tone).toBe('success')
    expect(groups[1].tone).toBe('warning')
    expect(groups[2].tone).toBe('danger')
    expect(hasImpact(impact)).toBe(true)
  })
})

describe('isDbStatus', () => {
  it('区分 db / yaml 两种响应', () => {
    const db: FactoryTemplateStatus = {
      source: 'db',
      revision: 0,
      seeded_version: 'a',
      current_version: 'b',
      template_changed: true,
    }
    expect(isDbStatus(db)).toBe(true)
    expect(isDbStatus({ source: 'yaml' })).toBe(false)
    expect(isDbStatus(null)).toBe(false)
  })
})
