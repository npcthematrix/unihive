import { describe, expect, it } from 'vitest'
import type { ConfigEntities } from '@/api/config'
import {
  formatUpdatedAt,
  isDbEntities,
  originLabel,
  originTagType,
  shortTemplateVersion,
  userModifiedCount,
} from '@/views/config/entities'

function dbEntities(overrides: Partial<Extract<ConfigEntities, { source: 'db' }>> = {}): ConfigEntities {
  return {
    source: 'db',
    revision: 3,
    template_version: 'abcdef0123456789',
    template_imported_at: '2026-09-18T01:02:03+00:00',
    upstreams: [
      { name: 'a', type: 'http', enabled: true, origin: 'builtin', updated_at: 't1' },
      { name: 'b', type: 'stdio', enabled: false, origin: 'user', updated_at: 't2' },
    ],
    sections: [
      { section: 'cache', origin: 'builtin', updated_at: 't1' },
      { section: 'fuyao_cache', origin: 'user', updated_at: 't3' },
    ],
    tools: {
      total: 10,
      by_origin: { builtin: 8, user: 2 },
    },
    ...overrides,
  }
}

describe('originLabel / originTagType', () => {
  it('maps known origins and passes unknown ones through', () => {
    expect(originLabel('builtin')).toBe('出厂模板')
    expect(originLabel('user')).toBe('用户修改')
    expect(originLabel('future-origin')).toBe('future-origin')
  })

  it('only builtin gets the neutral info tag; everything else warns', () => {
    expect(originTagType('builtin')).toBe('info')
    expect(originTagType('user')).toBe('warning')
    expect(originTagType('whatever')).toBe('warning')
  })
})

describe('shortTemplateVersion', () => {
  it('truncates long hashes to 12 chars by default', () => {
    expect(shortTemplateVersion('abcdef0123456789')).toBe('abcdef012345')
  })

  it('keeps short values, honors custom length, and nullish to dash', () => {
    expect(shortTemplateVersion('abc')).toBe('abc')
    expect(shortTemplateVersion('abcdef0123456789', 7)).toBe('abcdef0')
    expect(shortTemplateVersion(null)).toBe('—')
    expect(shortTemplateVersion(undefined)).toBe('—')
  })
})

describe('formatUpdatedAt', () => {
  it('formats ISO string to local compact time', () => {
    expect(formatUpdatedAt('2026-09-18T08:07:06+00:00')).toMatch(
      /^2026-09-\d{2} \d{2}:\d{2}:\d{2}$/,
    )
  })

  it('returns dash for empty and raw for unparseable', () => {
    expect(formatUpdatedAt('')).toBe('—')
    expect(formatUpdatedAt(null)).toBe('—')
    expect(formatUpdatedAt(undefined)).toBe('—')
    expect(formatUpdatedAt('not-a-date')).toBe('not-a-date')
  })
})

describe('isDbEntities', () => {
  it('narrows db entities and rejects yaml/null', () => {
    expect(isDbEntities(dbEntities())).toBe(true)
    expect(isDbEntities({ source: 'yaml' })).toBe(false)
    expect(isDbEntities(null)).toBe(false)
    expect(isDbEntities(undefined)).toBe(false)
  })
})

describe('userModifiedCount', () => {
  it('sums non-builtin upstreams, sections and tool origin groups', () => {
    // 1 upstream (b) + 1 section (fuyao_cache) + 2 tools
    expect(userModifiedCount(dbEntities())).toBe(4)
  })

  it('is zero when everything is builtin or in yaml mode', () => {
    const allBuiltin = dbEntities({
      upstreams: [
        { name: 'a', type: 'http', enabled: true, origin: 'builtin', updated_at: 't' },
      ],
      sections: [{ section: 'cache', origin: 'builtin', updated_at: 't' }],
      tools: { total: 5, by_origin: { builtin: 5 } },
    })
    expect(userModifiedCount(allBuiltin)).toBe(0)
    expect(userModifiedCount({ source: 'yaml' })).toBe(0)
    expect(userModifiedCount(null)).toBe(0)
  })

  it('counts only tool origin groups other than builtin', () => {
    const entities = dbEntities({
      upstreams: [],
      sections: [],
      tools: { total: 12, by_origin: { builtin: 9, user: 2, imported: 1 } },
    })
    expect(userModifiedCount(entities)).toBe(3)
  })
})
