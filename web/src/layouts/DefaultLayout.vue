<template>
  <el-container class="layout">
    <a href="#main-content" class="skip-link">跳到主内容</a>
    <el-aside
      :width="isCollapsed ? '64px' : '220px'"
      class="sidebar"
    >
      <div class="brand">
        <img class="brand-mark" :src="brandMark" alt="UniHive" />
        <span v-show="!isCollapsed" class="brand-name">UniHive</span>
      </div>
      <el-menu
        router
        :default-active="route.path"
        :collapse="isCollapsed"
        class="nav-menu"
        background-color="transparent"
        text-color="rgba(255,255,255,0.72)"
        active-text-color="#ffffff"
      >
        <el-menu-item
          v-for="item in MENU"
          :key="item.path"
          :index="item.path"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>
            <span>{{ item.title }}</span>
          </template>
        </el-menu-item>
      </el-menu>

      <!-- 侧栏脚区: 模式/用户 + 退出 + 折叠钮 (顶栏已移除) -->
      <div class="sidebar-footer" :class="{ collapsed: isCollapsed }">
        <template v-if="!isCollapsed">
          <div class="sf-mode" :title="isOpenMode ? '未启用登录鉴权' : ''">
            <el-icon class="sf-mode-icon">
              <Unlock v-if="isOpenMode" />
              <UserFilled v-else />
            </el-icon>
            <span class="sf-mode-text">
              {{ isOpenMode ? '开放模式 · 未启用登录' : auth.username }}
            </span>
          </div>
          <el-button
            v-if="!isOpenMode"
            text
            size="small"
            class="sf-logout"
            :loading="loggingOut"
            @click="confirmLogout"
          >退出登录</el-button>
        </template>

        <el-button
          text
          class="sf-collapse"
          :aria-label="isCollapsed ? '展开侧栏' : '折叠侧栏'"
          @click="toggleCollapsed"
        >
          <el-icon :size="18">
            <Expand v-if="isCollapsed" />
            <Fold v-else />
          </el-icon>
        </el-button>
      </div>
    </el-aside>

    <el-container>
      <el-main id="main-content" class="main" tabindex="-1">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Connection, Setting, Grid, Files, Fold, Expand, DataLine, UserFilled, Unlock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import brandMark from '@/assets/unihive-mark-dark.svg'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const loggingOut = ref(false)

// 鉴权关闭态 (后端 /api/auth/me 返回 auth_required=false): 无会话可退,
// 顶栏隐藏退出按钮, 用户名位显示模式标记。
const isOpenMode = computed(() => auth.authRequired === false)

/** 侧栏折叠态。 < 768px 默认折叠, 桌面默认展开。
 *  - 仅在窄屏上"自动"折叠到 icon-only; 用户手动切换后会记住自身选择
 *    (isCollapsedByUser 标志), 直到下次窄屏触发才回到自动。 */
const COLLAPSE_BREAKPOINT = 768
const isCollapsed = ref(false)
const isCollapsedByUser = ref(false)

function syncCollapsedByViewport(): void {
  const narrow = typeof window !== 'undefined' && window.innerWidth < COLLAPSE_BREAKPOINT
  if (narrow) {
    isCollapsed.value = true
  } else if (!isCollapsedByUser.value) {
    isCollapsed.value = false
  }
}

function onResize(): void {
  syncCollapsedByViewport()
}

function toggleCollapsed(): void {
  isCollapsed.value = !isCollapsed.value
  // 用户手动切换: 在桌面宽度下记住选择, 避免 resize 抖动
  if (typeof window !== 'undefined' && window.innerWidth >= COLLAPSE_BREAKPOINT) {
    isCollapsedByUser.value = isCollapsed.value
  }
}

// 注意: 不要在路由切换时自动展开/折叠。窄屏的收敛由挂载 + resize 的
// syncCollapsedByViewport 保证 (旧实现在移动端导航后自动展开到 220px,
// 内联非 overlay 布局会把主内容挤到 ~150px; 当前页标题顶栏已有, 折叠态
// 菜单项有 EP tooltip)。手动 toggle 是用户显式动作, 导航不应打断。

const MENU = [
  { path: '/guides', title: '接入指南', icon: Document },
  { path: '/l1-cache', title: '网关运行监控', icon: DataLine },
  { path: '/interfaces', title: 'MCP APIs', icon: Connection },
  { path: '/config', title: '配置信息', icon: Setting },
  { path: '/omni', title: 'OMNI板块管理', icon: Grid },
  { path: '/mcp-browser', title: 'MCP 浏览器', icon: Files },
] as const

async function onLogout(): Promise<void> {
  loggingOut.value = true
  try {
    await auth.logout()
    await router.replace({ name: 'login' })
  } catch {
    // 即使后端调用失败 (会话已过期) 也确保本地登出并跳登录页
    auth.clear()
    await router.replace({ name: 'login' })
  } finally {
    loggingOut.value = false
    ElMessage.success('已退出登录')
  }
}

/** 退出前确认 — 防误点丢会话。 */
async function confirmLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确认退出当前登录会话?', '退出确认', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await onLogout()
  } catch {
    // ElMessageBox 取消时也走 reject, 这里静默吞掉 (与 UX 一致: 用户主动取消)。
  }
}

onMounted(() => {
  syncCollapsedByViewport()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', onResize)
  }
})

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', onResize)
  }
})
</script>

<style scoped>
.layout {
  height: 100%;
}

.sidebar {
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #1d2733 0%, #141c26 100%);
  color: #fff;
  overflow-x: hidden;
  transition: width 0.2s ease;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 52px;
  padding: 0 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.brand-mark {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  /* 深色侧栏上给一点投影, 标体更浮出来 */
  filter: drop-shadow(0 1px 3px rgba(0, 0, 0, 0.45));
}

.brand-name {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.2px;
  white-space: nowrap;
  /* 优先 Space Grotesk, 本机未装则 fallback sans-serif (无需网络字体)。 */
  font-family: 'Space Grotesk', 'Inter', system-ui, -apple-system, sans-serif;
  background: linear-gradient(135deg, #e2e8f0 0%, #cbd5e1 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.nav-menu {
  flex: 1;
  border-right: none;
  padding: 8px;
}

.nav-menu :deep(.el-menu-item) {
  border-radius: 6px;
  margin: 2px 0;
  height: 42px;
}

.nav-menu :deep(.el-menu-item.is-active) {
  background: rgba(64, 158, 255, 0.18);
}

.nav-menu :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.06);
}

/* 侧栏脚区: 模式/用户 + 退出 + 折叠钮 */
.sidebar-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.sidebar-footer.collapsed {
  justify-content: center;
  padding: 10px 0;
}

.sf-mode {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.sf-mode-icon {
  color: rgba(255, 255, 255, 0.55);
  flex-shrink: 0;
}

.sf-mode-text {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.72);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sf-logout {
  color: rgba(255, 255, 255, 0.72);
  flex-shrink: 0;
}

.sf-logout:hover {
  color: #fff;
}

.sf-collapse {
  color: rgba(255, 255, 255, 0.72);
  flex-shrink: 0;
}

.sf-collapse:hover {
  color: #fff;
}

.main {
  background: var(--el-bg-color-page);
  padding: 16px;
  outline: none;
}

/* 键盘用户 Tab 进站点时跳过侧栏导航, 直接到主内容。 平时视觉隐藏,
   聚焦时回到屏幕顶部正中显眼展示。 */
.skip-link {
  position: absolute;
  left: -9999px;
  top: 0;
  z-index: 9999;
  padding: 8px 14px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  border-radius: 0 0 6px 0;
  text-decoration: none;
}

.skip-link:focus,
.skip-link:focus-visible {
  left: 0;
  outline: 2px solid #fff;
  outline-offset: -4px;
}
</style>
