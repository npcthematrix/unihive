import { describe, expect, it } from 'vitest'
import { escapeRegExp } from '@/utils/text'

describe('escapeRegExp', () => {
  it('空串 → 空串', () => {
    expect(escapeRegExp('')).toBe('')
  })

  it('普通字符串不变', () => {
    expect(escapeRegExp('hello world')).toBe('hello world')
  })

  it('转义正则元字符', () => {
    expect(escapeRegExp('a.b')).toBe('a\\.b')
    expect(escapeRegExp('a*b')).toBe('a\\*b')
    expect(escapeRegExp('a+b')).toBe('a\\+b')
    expect(escapeRegExp('a?b')).toBe('a\\?b')
    expect(escapeRegExp('(a)')).toBe('\\(a\\)')
    expect(escapeRegExp('[a-z]')).toBe('\\[a\\-z\\]')
    expect(escapeRegExp('{2,3}')).toBe('\\{2,3\\}')
    expect(escapeRegExp('^abc$')).toBe('\\^abc\\$')
    expect(escapeRegExp('a|b')).toBe('a\\|b')
    expect(escapeRegExp('a\\b')).toBe('a\\\\b')
  })

  it('转义后能正确作为正则编译并匹配', () => {
    const pattern = new RegExp(escapeRegExp('user.name'))
    expect(pattern.test('user.name')).toBe(true)
    expect(pattern.test('userXname')).toBe(false) // . 被转义, 不再匹配任意字符
  })

  it('混合 — 普通 + 元字符 (空格不在 regex 元字符集, 不转义)', () => {
    // 源函数字符集 /[\\^$.*+?()[\]{}|\-]/g:
    //   - 含 `+` (在 [^*+?] 内)
    //   - 含连字符 `-` (在末尾)
    //   - 不含空格
    // 故空格保留, `.` `*` `+` 都加反斜杠。
    expect(escapeRegExp('SH.* +suffix')).toBe('SH\\.\\* \\+suffix')
  })
})
