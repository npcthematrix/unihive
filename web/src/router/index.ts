import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { resolveAuthRoute } from './authGuard'

// hash 模式: Starlette 服务端零改动, 现有 cookie 中间件/静态挂载不变。
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    redirect: { name: 'guides' },
    children: [
      {
        path: 'status',
        name: 'status',
        component: () => import('@/views/StatusView.vue'),
        meta: { title: '上游数据Provider状态' },
      },
      {
        path: 'l1-cache',
        name: 'l1-cache',
        component: () => import('@/views/L1CacheView.vue'),
        meta: { title: '网关缓存监控' },
      },
      {
        path: 'interfaces',
        name: 'interfaces',
        component: () => import('@/views/InterfacesView.vue'),
        meta: { title: 'MCP APIs' },
      },
      {
        path: 'config',
        name: 'config',
        component: () => import('@/views/ConfigView.vue'),
        meta: { title: '配置信息' },
      },
      {
        path: 'omni',
        name: 'omni',
        component: () => import('@/views/OmniView.vue'),
        meta: { title: 'OMNI板块管理' },
      },
      {
        path: 'mcp-browser',
        name: 'mcp-browser',
        component: () => import('@/views/McpBrowserView.vue'),
        meta: { title: 'MCP 浏览器' },
      },
      // 旧的专用页面被 MCP 浏览器取代。保留为一条重定向而不是 404: 一行成本,
      // 保住已有书签, 且正好落回 THS-LOCAL 上游 —— 旧页面的内容, 只是这次是全的。
      {
        path: 'ths-local',
        redirect: { name: 'mcp-browser', query: { upstream: 'THS-LOCAL-GATEWAY' } },
      },
      {
        path: 'guides',
        name: 'guides',
        component: () => import('@/views/GuidesView.vue'),
        meta: { title: '接入指南' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: { name: 'guides' } },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 唯一的鉴权规则: 无会话 → 登录页; 登录后所有页面可访问。无角色/按钮权限。
// 决策逻辑抽到 authGuard (纯函数, 单测覆盖 redirect 回跳与开放重定向防护)。
router.beforeEach((to) => resolveAuthRoute(to, useAuthStore()))
