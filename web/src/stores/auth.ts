import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'

/**
 * 鉴权 store —— 按需求只保存用户名 (会话本体是服务端签名 cookie,
 * 由浏览器在同源请求上自动携带)。刷新页面后通过 /api/auth/me 恢复。
 */
export const useAuthStore = defineStore('auth', () => {
  const username = ref<string | null>(null)
  const bootstrapped = ref(false)
  let bootstrapPromise: Promise<void> | null = null

  const isAuthenticated = computed(() => username.value !== null)

  async function login(name: string, password: string): Promise<void> {
    const data = await authApi.login({ username: name, password })
    username.value = data.username
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
    bootstrapped.value = true
    bootstrapPromise = null
  }

  /** 路由守卫调用: 整个应用生命周期只探测一次 /me (401 也算探测完成)。 */
  function bootstrap(): Promise<void> {
    if (bootstrapPromise) return bootstrapPromise
    bootstrapPromise = authApi
      .fetchMe()
      .then((data) => {
        username.value = data.username
      })
      .catch(() => {
        username.value = null
      })
      .finally(() => {
        bootstrapped.value = true
      })
    return bootstrapPromise
  }

  return {
    username,
    bootstrapped,
    isAuthenticated,
    login,
    logout,
    clear,
    bootstrap,
  }
})
