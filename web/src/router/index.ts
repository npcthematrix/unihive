import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { pageTitle } from '@/utils/pageTitle'
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
      // 旧「上游数据PROVIDERs」页已合并进「网关运行监控 → 上游总览」。
      {
        path: 'status',
        redirect: { name: 'gateway-monitor' },
      },
      // 旧路由名/路径保留为重定向, 保住已有书签。
      {
        path: 'l1-cache',
        redirect: { name: 'gateway-monitor' },
      },
      {
        path: 'gateway-monitor',
        name: 'gateway-monitor',
        component: () => import('@/views/GatewayMonitorView.vue'),
        meta: { title: '网关运行监控' },
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

/**
 * 每条路由都声明了 meta.title, 但此前没有任何地方消费它 —— 六个页面的浏览器
 * 标签标题全是 "UniHive Console", 多开标签时无法区分, 历史记录也一样。
 * 统一在此落标题 (afterEach 拿到的是重定向后的最终路由)。
 */
router.afterEach((to) => {
  document.title = pageTitle(to.meta.title)
})
