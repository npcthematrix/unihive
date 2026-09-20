import { describe, expect, it } from 'vitest'
import { nextTick, ref } from 'vue'

/**
 * Round-13 簇O: ErrorBoundary 的状态机与 "路由切换自动复位" 抽成
 * 纯 composable 单测 (无 plugin-vue, 不挂组件)。
 * 旧实现只在点 "回首页" 时清 hasError: 用户改点侧栏导航,
 * fallback 卡片会跨路由残留 —— 必须 watch fullPath 复位。
 */
import {
  bindRouteErrorReset,
  createErrorBoundaryState,
} from '@/composables/errorBoundary'

describe('createErrorBoundaryState.capture', () => {
  it('Error 实例: hasError + name + message', () => {
    const s = createErrorBoundaryState()
    s.capture(Object.assign(new Error('boom'), { name: 'TypeError' }))
    expect(s.hasError.value).toBe(true)
    expect(s.errorKind.value).toBe('TypeError')
    expect(s.errorMessage.value).toBe('boom')
  })

  it('无名 Error → kind 回落 Error', () => {
    const s = createErrorBoundaryState()
    s.capture(new Error('x'))
    expect(s.errorKind.value).toBe('Error')
  })

  it('字符串抛出 → 当消息展示', () => {
    const s = createErrorBoundaryState()
    s.capture('something broke')
    expect(s.hasError.value).toBe(true)
    expect(s.errorKind.value).toBe('Error')
    expect(s.errorMessage.value).toBe('something broke')
  })

  it('null / 对象 / 数字 → 未知错误占位, 不抛', () => {
    for (const v of [null, undefined, { x: 1 }, 42]) {
      const s = createErrorBoundaryState()
      s.capture(v)
      expect(s.hasError.value).toBe(true)
      expect(s.errorKind.value).toBe('Error')
      expect(s.errorMessage.value).toBe('未知错误')
    }
  })

  it('reset 清空全部状态 (可再次捕获)', () => {
    const s = createErrorBoundaryState()
    s.capture(new Error('boom'))
    s.reset()
    expect(s.hasError.value).toBe(false)
    expect(s.errorKind.value).toBe('')
    expect(s.errorMessage.value).toBe('')

    s.capture(new Error('again'))
    expect(s.hasError.value).toBe(true)
    expect(s.errorMessage.value).toBe('again')
  })
})

describe('bindRouteErrorReset — 路由切换自动复位', () => {
  it('fullPath 变化时调用 reset; 初始绑定不触发', async () => {
    const s = createErrorBoundaryState()
    const fullPath = ref('/status')
    s.capture(new Error('boom'))

    bindRouteErrorReset(fullPath, s.reset)
    await nextTick()
    expect(s.hasError.value).toBe(true)

    fullPath.value = '/config'
    await nextTick()
    expect(s.hasError.value).toBe(false)
    expect(s.errorMessage.value).toBe('')
  })

  it('连续多次导航都能复位 (新页面又炸也能正常展示 fallback)', async () => {
    const s = createErrorBoundaryState()
    const fullPath = ref('/status')
    bindRouteErrorReset(fullPath, s.reset)

    s.capture(new Error('e1'))
    fullPath.value = '/a'
    await nextTick()
    expect(s.hasError.value).toBe(false)

    s.capture(new Error('e2'))
    expect(s.hasError.value).toBe(true)
    fullPath.value = '/b'
    await nextTick()
    expect(s.hasError.value).toBe(false)
    expect(s.errorMessage.value).toBe('')
  })

  it('接受 getter 形式的路由源', async () => {
    const s = createErrorBoundaryState()
    const inner = ref('/status')
    bindRouteErrorReset(() => inner.value, s.reset)
    s.capture(new Error('boom'))
    inner.value = '/omni'
    await nextTick()
    expect(s.hasError.value).toBe(false)
  })
})
