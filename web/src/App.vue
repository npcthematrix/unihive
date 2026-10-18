<template>
  <!-- 初次导航的路由守卫在等 /me 探测 (最长 5s, 见 BOOTSTRAP_PROBE_TIMEOUT_MS),
       此期间 router-view 什么都不渲染 —— 用与 index.html 内联标记**完全同款**
       的启动门接住, 保证从 HTML 到 Vue 全程无白屏、也无视觉跳变。
       两份样式必须同步: index.html 里是 #app-boot, 这里是 scoped .app-boot,
       配色 / 尺寸 / 间距逐项对齐 (旧版两边分别是蓝灰渐变与藏青径向渐变、
       30px 白色与 22px 青色转圈, 交接时会明显闪一下)。 -->
  <div v-if="!auth.bootstrapped" class="app-boot">
    <img class="boot-mark" :src="bootMark" alt="" aria-hidden="true" />
    <div class="boot-wordmark">UniHive</div>
    <div class="boot-tagline">MCP · AGGREGATE</div>
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

// public/ 下的静态资源不经模块打包, 必须自己拼 base —— 生产构建挂在
// /static/console/ 下, 写死 '/favicon.svg' 会 404。
const bootMark = `${import.meta.env.BASE_URL}favicon.svg`
</script>

<style scoped>
.app-boot {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
  align-items: center;
  justify-content: center;
  background: radial-gradient(ellipse at top, #1e293b 0%, #0f172a 100%);
  color: rgba(255, 255, 255, 0.82);
  font-family: system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', sans-serif;
  font-size: 14px;
}

.boot-mark {
  width: 64px;
  height: 64px;
  filter: drop-shadow(0 4px 16px rgba(59, 130, 246, 0.35));
}

.boot-wordmark {
  font-family: 'Space Grotesk', 'Inter', system-ui, sans-serif;
  font-weight: 600;
  font-size: 22px;
  letter-spacing: -0.3px;
  background: linear-gradient(135deg, #e2e8f0 0%, #94a3b8 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.boot-tagline {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 2.4px;
  color: rgba(148, 163, 184, 0.7);
  margin-top: -8px;
}

.boot-spinner {
  width: 22px;
  height: 22px;
  margin-top: 6px;
  border: 2px solid rgba(255, 255, 255, 0.14);
  border-top-color: rgba(147, 197, 253, 0.9);
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
