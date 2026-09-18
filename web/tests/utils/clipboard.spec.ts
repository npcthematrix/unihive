import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 全局 mock element-plus 的 ElMessage, 不引入实际组件 (避免 happy-dom 复杂度)
vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}))

import { ElMessage } from 'element-plus'
import { copyText } from '@/utils/clipboard'

describe('copyText', () => {
  // 用 vi.stubGlobal 注入 navigator.clipboard 替身, 用后还原。
  const originalClipboard = navigator.clipboard

  beforeEach(() => {
    vi.mocked(ElMessage.success).mockClear()
    vi.mocked(ElMessage.error).mockClear()
  })

  afterEach(() => {
    // 还原 (避免污染其他 spec)
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      writable: true,
      configurable: true,
    })
  })

  function stubClipboard(impl: (text: string) => Promise<void>) {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(impl) },
      writable: true,
      configurable: true,
    })
  }

  it('成功复制 → ElMessage.success + return true', async () => {
    stubClipboard(async () => undefined)
    const ok = await copyText('hello', '已复制')
    expect(ok).toBe(true)
    expect(ElMessage.success).toHaveBeenCalledWith('已复制')
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('复制抛错 → ElMessage.error + return false', async () => {
    stubClipboard(async () => {
      throw new Error('NotAllowedError')
    })
    const ok = await copyText('hello', '已复制')
    expect(ok).toBe(false)
    expect(ElMessage.error).toHaveBeenCalledWith('复制失败, 请手动选择文本复制')
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('successMsg 为空 → 不弹 success (但仍 return true)', async () => {
    stubClipboard(async () => undefined)
    const ok = await copyText('hello', '')
    expect(ok).toBe(true)
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('successMsg 为默认 → 弹默认消息', async () => {
    stubClipboard(async () => undefined)
    await copyText('hello')
    expect(ElMessage.success).toHaveBeenCalledWith('已复制到剪贴板')
  })

  it('navigator.clipboard 缺失 → catch 走 error 分支', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      writable: true,
      configurable: true,
    })
    const ok = await copyText('hello', '已复制')
    expect(ok).toBe(false)
    expect(ElMessage.error).toHaveBeenCalledWith('复制失败, 请手动选择文本复制')
  })

  it('writeText 收到的 text 参数正确传递', async () => {
    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    })
    await copyText('multi\nline\ntext', 'ok')
    expect(writeText).toHaveBeenCalledWith('multi\nline\ntext')
  })
})
