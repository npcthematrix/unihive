<template>
  <!-- 初次导航的路由守卫在等 /me 探测 (最长 5s, 见 BOOTSTRAP_PROBE_TIMEOUT_MS),
       此期间 router-view 什么都不渲染 —— 用与 index.html 内联标记同款的
       启动门接住, 保证从 HTML 到 Vue 全程无白屏。 -->
  <div v-if="!auth.bootstrapped" class="app-boot">
    <div class="boot-spinner" role="status" aria-label="启动中"></div>
    <div>正在启动 UniHive Console…</div>
  </div>
  <ErrorBoundary v-else>
    <router-view />
  </ErrorBoundary>
</template>

<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
import ErrorBoundary from '@/components/ErrorBoundary.vue'

// 守卫会在首次导航时调用 bootstrap(); 这里只消费状态, 不重复探测。
const auth = useAuthStore()
</script>

<style scoped>
.app-boot {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1d2733 0%, #2c3e55 55%, #3a506b 100%);
  color: rgba(255, 255, 255, 0.82);
  font-family: system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', sans-serif;
  font-size: 14px;
}

.boot-spinner {
  width: 30px;
  height: 30px;
  border: 3px solid rgba(255, 255, 255, 0.18);
  border-top-color: rgba(255, 255, 255, 0.88);
  border-radius: 50%;
  animation: app-boot-spin 0.8s linear infinite;
}

@keyframes app-boot-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .boot-spinner {
    animation: none;
  }
}
</style>
