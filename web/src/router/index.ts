import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

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
    redirect: { name: 'status' },
    children: [
      {
        path: 'status',
        name: 'status',
        component: () => import('@/views/StatusView.vue'),
        meta: { title: '上游状态' },
      },
      {
        path: 'l1-cache',
        name: 'l1-cache',
        component: () => import('@/views/L1CacheView.vue'),
        meta: { title: 'L1 缓存指标' },
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
        path: 'ths-local',
        name: 'ths-local',
        component: () => import('@/views/ThsLocalView.vue'),
        meta: { title: 'THS Local 网关' },
      },
      {
        path: 'guides',
        name: 'guides',
        component: () => import('@/views/GuidesView.vue'),
        meta: { title: '接入指南' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: { name: 'status' } },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 唯一的鉴权规则: 无会话 → 登录页; 登录后所有页面可访问。无角色/按钮权限。
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.name === 'login') {
    if (!auth.bootstrapped) await auth.bootstrap()
    return auth.isAuthenticated ? { name: 'status' } : true
  }

  await auth.bootstrap()
  if (!auth.isAuthenticated) {
    return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  }
  return true
})
