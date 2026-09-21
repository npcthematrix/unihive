<template>
  <div v-if="!hasError" class="error-boundary">
    <slot />
  </div>
  <div v-else class="error-fallback" role="alert">
    <div class="error-card">
      <div class="error-icon">⚠</div>
      <h2 class="error-title">页面渲染失败</h2>
      <p class="error-subtitle">{{ errorKind }}</p>
      <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
      <div class="error-actions">
        <el-button type="primary" @click="reload">重新加载</el-button>
        <el-button @click="goHome">回首页</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onErrorCaptured } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  bindRouteErrorReset,
  createErrorBoundaryState,
} from '@/composables/errorBoundary'

/**
 * 全局视图错误边界。
 *
 * 任何后代组件的 render / setup() 同步抛错 / 生命周期钩子异常都会被
 * onErrorCaptured 捕获, 显示降级 UI 而不是白屏。不会无限递归
 * (内部不渲染自己, return false 阻止继续向上传播)。
 *
 * 不捕获: 异步 Promise rejection、事件处理器里的 throw、setTimeout 回调 —
 * 这三类仍需业务层显式处理。
 *
 * 路由切换 (fullPath 变化) 后自动复位: 用户改点侧栏离开出错页面时,
 * fallback 卡片不应跨路由残留。
 */

const route = useRoute()
const router = useRouter()
const { hasError, errorKind, errorMessage, capture, reset } =
  createErrorBoundaryState()

onErrorCaptured((err: unknown) => {
  capture(err)
  // 故意不 console.error: 生产环境会泄露完整错误对象 (含堆栈/上游 source),
  // 用户已通过 UI fallback 看到错误类型 + 消息。开发期如需调试, 临时加回即可。
  return false
})

bindRouteErrorReset(() => route.fullPath, reset)

function reload(): void {
  // 重新挂载子树: 简单做是 router.go(0); 走 hash 模式下当前路由仍是同一处,
  // 直接刷新会重跑组件 setup, 适合"刚才的偶发错误"。
  window.location.reload()
}

function goHome(): void {
  reset()
  void router.replace({ name: 'guides' })
}
</script>

<style scoped>
.error-boundary {
  display: contents;
}

.error-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: 24px;
}

.error-card {
  max-width: 480px;
  width: 100%;
  padding: 28px 32px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.06);
  text-align: center;
}

.error-icon {
  font-size: 42px;
  color: var(--el-color-warning);
  margin-bottom: 12px;
  line-height: 1;
}

.error-title {
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 600;
}

.error-subtitle {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.error-message {
  margin: 0 0 20px;
  padding: 10px 14px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-regular);
  word-break: break-word;
  white-space: pre-wrap;
  text-align: left;
}

.error-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}
</style>
