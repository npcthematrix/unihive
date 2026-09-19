import { describe, expect, it, vi } from 'vitest'
import type {
  ImportToolChange,
  OpenapiImportPreview,
} from '@/api/config'
import {
  actionLabel,
  actionTagType,
  canApplyImport,
  changedFieldLabel,
  defaultSelection,
  downloadText,
  fragmentFilename,
  hasImportImpact,
  importImpactGroups,
  isAllSelected,
  isRevisionConflictError,
  isSelectableAction,
  isUpstreamFetchError,
  selectableChanges,
  toggleSelected,
} from '@/views/mcp-browser/openapiImport'

function change(
  name: string,
  action: ImportToolChange['action'],
  changed_fields: string[] = [],
): ImportToolChange {
  return { name, action, changed_fields }
}

function preview(
  changes: ImportToolChange[],
  extra: Partial<OpenapiImportPreview> = {},
): OpenapiImportPreview {
  return {
    changed: true,
    revision: 3,
    upstream: 'THS-LOCAL-GATEWAY',
    summary: { added: 1, changed: 1, unchanged: 1, removed: 0, unmapped: 1 },
    changes,
    unmapped: [{ name: 'fqgate_lonely', reason: 'no REST path' }],
    removed: [],
    selected: [],
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
    yaml_fragment: 'tools:\n',
    ...extra,
  }
}

const THREE = [
  change('fqgate_a', 'added'),
  change('fqgate_b', 'changed', ['spec', 'description']),
  change('fqgate_c', 'unchanged'),
]

describe('openapiImport selection helpers', () => {
  it('classifies selectable actions', () => {
    expect(isSelectableAction('added')).toBe(true)
    expect(isSelectableAction('changed')).toBe(true)
    expect(isSelectableAction('unchanged')).toBe(false)
  })

  it('selectableChanges keeps plan order and drops unchanged', () => {
    expect(selectableChanges(THREE).map((c) => c.name)).toEqual([
      'fqgate_a',
      'fqgate_b',
    ])
  })

  it('defaultSelection selects every added/changed row', () => {
    expect(defaultSelection(preview(THREE))).toEqual(['fqgate_a', 'fqgate_b'])
    expect(defaultSelection(null)).toEqual([])
  })

  it('toggleSelected adds/removes immutably and sorts', () => {
    const initial = ['fqgate_b']
    const added = toggleSelected(initial, 'fqgate_a')
    expect(added).toEqual(['fqgate_a', 'fqgate_b'])
    expect(initial).toEqual(['fqgate_b'])
    expect(toggleSelected(added, 'fqgate_a')).toEqual(['fqgate_b'])
  })

  it('isAllSelected requires every selectable row', () => {
    const p = preview(THREE)
    expect(isAllSelected(p, ['fqgate_a', 'fqgate_b'])).toBe(true)
    expect(isAllSelected(p, ['fqgate_a'])).toBe(false)
    expect(isAllSelected(p, [])).toBe(false)
    expect(isAllSelected(null, ['x'])).toBe(false)
  })
})

describe('canApplyImport', () => {
  it('requires a preview and non-empty selection', () => {
    expect(canApplyImport(null, ['fqgate_a'])).toBe(false)
    expect(canApplyImport(preview(THREE), [])).toBe(false)
  })

  it('accepts subset of selectable names', () => {
    expect(canApplyImport(preview(THREE), ['fqgate_a'])).toBe(true)
  })

  it('rejects unknown and unchanged-only names', () => {
    expect(canApplyImport(preview(THREE), ['fqgate_nope'])).toBe(false)
    expect(canApplyImport(preview(THREE), ['fqgate_c'])).toBe(false)
  })

  it('blocks when lint errors exist', () => {
    const p = preview(THREE, {
      lint_errors: [{ severity: 'error', code: 'M1', message: 'bad' }],
    })
    expect(canApplyImport(p, ['fqgate_a'])).toBe(false)
  })
})

describe('labels', () => {
  it('maps actions to Chinese labels and tag types', () => {
    expect(actionLabel('added')).toBe('新增')
    expect(actionLabel('changed')).toBe('契约变更')
    expect(actionLabel('unchanged')).toBe('无变化')
    expect(actionLabel('weird')).toBe('weird')
    expect(actionTagType('added')).toBe('success')
    expect(actionTagType('changed')).toBe('warning')
    expect(actionTagType('unchanged')).toBe('info')
  })

  it('translates signature fields, unknown passthrough', () => {
    expect(changedFieldLabel('spec')).toBe('参数契约')
    expect(changedFieldLabel('routing')).toBe('路由键')
    expect(changedFieldLabel('description')).toBe('描述')
    expect(changedFieldLabel('mapping')).toBe('上游映射')
    expect(changedFieldLabel('other')).toBe('other')
  })
})

describe('impact groups', () => {
  it('shows only non-empty tool groups', () => {
    const groups = importImpactGroups({
      tools_added: ['fqgate_a'],
      tools_removed: [],
      tools_republished: ['fqgate_b'],
      upstreams_attach: [],
      upstreams_reconnect: [],
      upstreams_detach: [],
    })
    expect(groups.map((g) => g.key)).toEqual(['tools_added', 'tools_republished'])
    expect(hasImportImpact({
      tools_added: [],
      tools_removed: [],
      tools_republished: [],
      upstreams_attach: [],
      upstreams_reconnect: [],
      upstreams_detach: [],
    })).toBe(false)
  })
})

describe('fragment filename / download', () => {
  it('builds a timestamped yaml filename', () => {
    const name = fragmentFilename(new Date(2026, 8, 18, 9, 7, 5))
    expect(name).toBe('openapi-import-tools-20260918-090705.yaml')
  })

  it('downloadText creates an object URL anchor and revokes it', () => {
    const click = vi.fn()
    const remove = vi.fn()
    const create = vi.fn(() => 'blob:mock')
    const revoke = vi.fn()
    const anchor = { click, remove, href: '', download: '' }
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    vi.spyOn(document, 'createElement').mockReturnValue(
      anchor as unknown as HTMLElement,
    )
    vi.spyOn(document.body, 'appendChild').mockImplementation(() => anchor as never)

    downloadText('f.yaml', 'tools: []')

    expect(create).toHaveBeenCalledOnce()
    expect(anchor.download).toBe('f.yaml')
    expect(click).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledOnce()
    expect(revoke).toHaveBeenCalledWith('blob:mock')
    vi.unstubAllGlobals()
  })
})

describe('error classification', () => {
  it('detects 409 revision conflicts', () => {
    expect(isRevisionConflictError({ response: { status: 409 } })).toBe(true)
    expect(isRevisionConflictError({ response: { status: 400 } })).toBe(false)
    expect(isRevisionConflictError(new Error('x'))).toBe(false)
    expect(isRevisionConflictError(null)).toBe(false)
  })

  it('detects 502 upstream fetch errors', () => {
    expect(isUpstreamFetchError({ response: { status: 502 } })).toBe(true)
    expect(isUpstreamFetchError({ response: { status: 503 } })).toBe(false)
  })
})
