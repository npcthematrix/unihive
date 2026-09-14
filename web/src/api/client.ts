import axios, { AxiosError } from 'axios'
import { useAuthStore } from '@/stores/auth'
import { router } from '@/router'

/**
 * 全局 axios 实例。
 *
 * 鉴权载体是服务端 SessionMiddleware 种的同源签名 cookie (unihive_console),
 * 不是 localStorage 里的 token —— 因此必须 withCredentials, 且无需手动
 * 注入 Authorization 头。Pinia 里只保存用户名 (会话的展示信息)。
 */
export const http = axios.create({
  baseURL: '/',
  timeout: 15_000,
  withCredentials: true,
})

// 401 = 会话缺失/过期: 清掉前端会话状态并回登录页 (登录接口自身的 401
// 交给登录页展示"用户名或密码错误", 不在此跳转)。
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    const url = error.config?.url ?? ''
    if (status === 401 && !url.includes('/api/auth/login')) {
      const auth = useAuthStore()
      auth.clear()
      const current = router.currentRoute.value
      if (current.name !== 'login') {
        void router.replace({
          name: 'login',
          query: current.fullPath !== '/' ? { redirect: current.fullPath } : undefined,
        })
      }
    }
    return Promise.reject(error)
  },
)

/** 从后端错误体提取可展示消息 (后端统一 {error} / {detail} / {message})。 */
export function errorMessage(error: unknown, fallback = '请求失败'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { error?: string; detail?: string; message?: string } | undefined
    return data?.error || data?.detail || data?.message || error.message || fallback
  }
  if (error instanceof Error) return error.message
  return fallback
}
