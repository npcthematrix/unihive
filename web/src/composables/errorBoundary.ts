import { ref, watch, type Ref, type WatchSource } from 'vue'

/**
 * Round-13 簇O: ErrorBoundary 的状态机抽成纯 composable,
 * 便于在无 plugin-vue 的 vitest 环境下单测 (组件无法挂载)。
 */

export interface ErrorBoundaryState {
  hasError: Ref<boolean>
  errorKind: Ref<string>
  errorMessage: Ref<string>
  /** 捕获一个子树抛出的错误 (Error / 字符串 / 任意 throw 值)。 */
  capture: (error: unknown) => void
  /** 复位到正常渲染态。 */
  reset: () => void
}

export function createErrorBoundaryState(): ErrorBoundaryState {
  const hasError = ref(false)
  const errorKind = ref('')
  const errorMessage = ref('')

  function capture(error: unknown): void {
    hasError.value = true
    if (error instanceof Error) {
      errorKind.value = error.name || 'Error'
      errorMessage.value = error.message
    } else if (typeof error === 'string') {
      errorKind.value = 'Error'
      errorMessage.value = error
    } else {
      // null / 对象 / 数字等非标准 throw 值也要有兜底展示, 不能白屏。
      errorKind.value = 'Error'
      errorMessage.value = '未知错误'
    }
  }

  function reset(): void {
    hasError.value = false
    errorKind.value = ''
    errorMessage.value = ''
  }

  return { hasError, errorKind, errorMessage, capture, reset }
}

/**
 * 路由切换后自动复位错误边界: 用户点侧栏离开出错页面时,
 * fallback 卡片不应跨路由残留。初始绑定时不触发。
 *
 * 接受 ref 或 getter 形式的 fullPath 源。
 */
export function bindRouteErrorReset(
  fullPath: WatchSource<string>,
  reset: () => void,
): void {
  watch(fullPath, () => {
    reset()
  })
}
