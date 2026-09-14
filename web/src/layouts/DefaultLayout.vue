<template>
  <el-container class="layout">
    <el-aside width="220px" class="sidebar">
      <div class="brand">
        <span class="brand-mark">UH</span>
        <span class="brand-name">UniHive</span>
      </div>
      <el-menu
        router
        :default-active="route.path"
        class="nav-menu"
        background-color="transparent"
        text-color="rgba(255,255,255,0.72)"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/guides">
          <el-icon><Document /></el-icon>
          <span>接入指南</span>
        </el-menu-item>
        <el-menu-item index="/status">
          <el-icon><Monitor /></el-icon>
          <span>上游状态</span>
        </el-menu-item>
        <el-menu-item index="/interfaces">
          <el-icon><Connection /></el-icon>
          <span>MCP APIs</span>
        </el-menu-item>
        <el-menu-item index="/config">
          <el-icon><Setting /></el-icon>
          <span>配置信息</span>
        </el-menu-item>
        <el-menu-item index="/omni">
          <el-icon><Grid /></el-icon>
          <span>OMNI板块管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="topbar" height="52px">
        <span class="page-title">{{ route.meta.title ?? '' }}</span>
        <div class="topbar-right">
          <el-icon class="user-icon"><UserFilled /></el-icon>
          <span class="username">{{ auth.username }}</span>
          <el-divider direction="vertical" />
          <el-button text size="small" :loading="loggingOut" @click="onLogout">
            退出登录
          </el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const loggingOut = ref(false)

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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  background: var(--el-color-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-name {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.3px;
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

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}

.page-title {
  font-size: 15px;
  font-weight: 600;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--el-text-color-regular);
}

.user-icon {
  color: var(--el-text-color-secondary);
}

.username {
  font-size: 13px;
}

.main {
  background: var(--el-bg-color-page);
  padding: 16px;
}
</style>
