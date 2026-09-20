import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'
import { gatewayUnavailableMessage } from '@/api/client'

/**
 * 鉴权 store —— 按需求只保存用户名 (会话本体是服务端签名 cookie,
 * 由浏览器在同源请求上自动携带)。刷新页面后通过 /api/auth/me 恢复。
 *
 * authRequired 区分两种后端形态 (Round-13 M3):
 * - true  = 鉴权启用, isAuthenticated 取决于 username;
 * - false = 鉴权关闭态 (后端 auth_required=false), 视为已认证,
 *   路由守卫不得把用户卡在登录页;
 * - null  = bootstrap 尚未完成。
 *
 * probeError (Round-13 簇M): /me 探测遇到 "网关到不了" 类故障
 * (断网/超时/5xx) 时的中文消息。这与 401 未登录严格区分 ——
 * 故障态下用户落在登录页看到的是 "网关连不上 + 重试",
 * 而不是被静默当成登出。401/4xx 时 probeError 为 null。
 */
export const useAuthStore = defineStore('auth', () => {
  const username = ref<string | null>(null)
  const authRequired = ref<boolean | null>(null)
  const bootstrapped = ref(false)
  const probeError = ref<string | null>(null)
  let bootstrapPromise: Promise<void> | null = null

  const isAuthenticated = computed(
    () => authRequired.value === false || username.value !== null,
  )

  async function login(name: string, password: string): Promise<void> {
    const data = await authApi.login({ username: name, password })
    username.value = data.username
    authRequired.value = true
    probeError.value = null
    bootstrapped.value = true
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clear()
    }
  }

  function clear(): void {
    username.value = null
    // 401 拦截器触发 clear: 既然存在受保护端点, 必然是启用态。
    authRequired.value = true
    // 这是鉴权失效, 不是网关故障 —— 不得残留故障横幅。
    probeError.value = null
    bootstrapped.value = true
    bootstrapPromise = null
  }

  /** 路由守卫调用: 整个应用生命周期只探测一次 /me (401 也算探测完成)。 */
  function bootstrap(): Promise<void> {
    if (bootstrapPromise) return bootstrapPromise
    bootstrapPromise = authApi
      .fetchMe()
      .then((data) => {
        // 旧后端不返回 auth_required 时按启用态对待 (fail-safe)。
        authRequired.value = data.auth_required ?? true
        username.value = data.username
        probeError.value = null
      })
      .catch((error: unknown) => {
        // 区分传输故障与鉴权拒绝: 网关不可达时保留故障消息供登录页
        // 展示重试入口; 401/4xx 分类为 null, 按干净的未登录处理。
        probeError.value = gatewayUnavailableMessage(error)
        authRequired.value = true
        username.value = null
      })
      .finally(() => {
        bootstrapped.value = true
      })
    return bootstrapPromise
  }

  /** 登录页 "重试" 按钮: 丢弃上次探测结果重新拉 /me。
   *  成功恢复时路由跳转由调用方 (LoginView) 处理。 */
  function retryProbe(): Promise<void> {
    bootstrapPromise = null
    return bootstrap()
  }

  return {
    username,
    authRequired,
    bootstrapped,
    probeError,
    isAuthenticated,
    login,
    logout,
    clear,
    bootstrap,
    retryProbe,
  }
})
