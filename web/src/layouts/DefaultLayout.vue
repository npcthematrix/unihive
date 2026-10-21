<template>
  <el-container class="layout">
    <a href="#main-content" class="skip-link">跳到主内容</a>
    <el-aside
      :width="isCollapsed ? '64px' : '220px'"
      class="sidebar"
    >
      <div class="brand" :class="{ collapsed: isCollapsed }">
        <img class="brand-mark" :src="brandMark" alt="UniHive" />
        <transition name="brand-fade">
          <span v-if="!isCollapsed" class="brand-text">
            <span class="brand-name">
              <span class="brand-uni">Uni</span><span class="brand-hive">Hive</span>
            </span>
          </span>
        </transition>
      </div>
      <el-menu
        router
        :default-active="route.path"
        :collapse="isCollapsed"
        class="nav-menu"
        background-color="transparent"
        text-color="#475569"
        active-text-color="#2563EB"
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
          <div class="sf-mode" :class="{ open: isOpenMode }" :title="isOpenMode ? '未启用登录鉴权' : ''">
            <el-icon class="sf-mode-icon">
              <Unlock v-if="isOpenMode" />
              <UserFilled v-else />
            </el-icon>
            <span class="sf-mode-text">
              {{ isOpenMode ? '开放模式' : auth.username }}
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

        <el-tooltip
          :content="isCollapsed ? '展开侧栏' : '折叠侧栏'"
          placement="right"
        >
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
        </el-tooltip>
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
import { Fold, Expand, UserFilled, Unlock } from '@element-plus/icons-vue'
import { MENU } from '@/constants/menu'
import { useAuthStore } from '@/stores/auth'
import brandMark from '@/assets/unihive-logo.png'

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

/* 菜单项定义在 @/constants/menu —— 抽出去才能被单测覆盖 path 与路由的一致性
   (曾因菜单写 /l1-cache、实际路由是 /gateway-monitor 导致该项永不高亮)。 */

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
  /* 现代化浅色: 单一底色, 不再 180deg 渐变 (差异肉眼不可见, 纯冗余);
     与主内容区仅靠 1px slate 右边分隔; 品牌区下沿/脚区上沿的两道横线也减掉
     (2026-10-06 侧栏审视 M1 + L3), 整张侧栏作为一块面板出现。 */
  background: #FAFBFC;
  color: #0F172A;
  overflow-x: hidden;
  transition: width 0.2s ease;
  border-right: 1px solid #E2E8F0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 56px;
  padding: 0 20px;
}

/* H3 折叠态: brand 区只剩 logo, 需居中, padding 也归零让 logo 真正垂直/水平居中 */
.brand.collapsed {
  justify-content: center;
  padding: 0;
}

.brand-mark {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
}

.brand-text {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  /* L6 折叠态过渡: v-if 配合 <transition name="brand-fade">, 150ms ease */
  transition: opacity 150ms ease;
}

.brand-fade-enter-active,
.brand-fade-leave-active {
  transition: opacity 150ms ease;
}

.brand-fade-enter-from,
.brand-fade-leave-to {
  opacity: 0;
}

.brand-name {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.3px;
  white-space: nowrap;
  /* 优先 Space Grotesk, 本机未装则 fallback sans-serif (无需网络字体)。 */
  font-family: 'Space Grotesk', 'Inter', system-ui, -apple-system, sans-serif;
}

.brand-uni {
  /* 紫色 — H2 复用点: 与侧栏脚"开放模式"徽章同色, 形成"无鉴权警示"语义对 */
  color: #7c3aed;
}

.brand-hive {
  /* "Hive" 蜂巢系统 — 蓝系 */
  color: #2563EB;
}

.nav-menu {
  flex: 1;
  border-right: none;
  padding: 8px;
}

.nav-menu :deep(.el-menu-item) {
  display: flex !important;
  align-items: center;
  justify-content: flex-start !important;
  padding: 0 20px !important;
  margin: 0 !important;
  width: 100% !important;
  box-sizing: border-box;
  border-radius: 6px;
  height: 38px;
}

/* L1 图标统一: 显式 18px + svg 占位, 消除 EP 不同 icon 内部 padding 引起的视觉差 */
.nav-menu :deep(.el-menu-item .el-icon) {
  font-size: 18px;
  width: 18px;
  height: 18px;
  margin-right: 10px !important;
}

.nav-menu :deep(.el-menu-item.is-active) {
  /* 现代 SaaS: 极浅蓝底 + 主蓝文字, 不再叠两层不同蓝 */
  background: #EFF6FF;
  color: #2563EB;
  font-weight: 600;
}

.nav-menu :deep(.el-menu-item:hover) {
  background: #F1F5F9;
}

/* 侧栏脚区: 模式/用户 + 退出 + 折叠钮 */
.sidebar-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
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
  color: #64748B;
  flex-shrink: 0;
}

.sf-mode-text {
  font-size: 12px;
  color: #64748B;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* H2 紫色复用: 开放模式 (无鉴权) 用品牌紫与品牌区"Uni"前缀同色,
   在 220px 窄侧栏里形成"未鉴权 — 警示"语义。已登录态保持中性灰。 */
.sf-mode.open .sf-mode-icon,
.sf-mode.open .sf-mode-text {
  color: #7c3aed;
}

.sf-logout {
  color: #64748B;
  flex-shrink: 0;
}

.sf-logout:hover {
  color: #3B82F6;
}

.sf-collapse {
  color: #64748B;
  flex-shrink: 0;
}

.sf-collapse:hover {
  color: #3B82F6;
}

.main {
  background: var(--el-bg-color-page);
  padding: 16px;
  outline: none;
  /* 主内容区自身**不再滚动**: 滚动交给每个页面内部的 .view-body。
     之前 el-main 是 overflow:auto 且高度=整窗, 滚动条从窗口顶边拉到底边,
     页面标题和子标签都落在滚动行程里 —— 看上去"整个大页面在滚", 而不是
     "标签页内部在滚"。现在 el-main 只负责给出确定高度, 页面自己切分
     「固定页头 + 滚动正文」。 */
  overflow: hidden;
  display: flex;
  flex-direction: column;
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
