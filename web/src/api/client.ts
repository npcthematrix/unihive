import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
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

// ===== 5xx / 网络错误指数退避重试 =====
// 触发条件 (与 401 拦截互补, 不冲突):
//   - 幂等方法 (GET/HEAD)
//   - 5xx (502/503/504 等网关瞬时错误)
//   - timeout (ECONNABORTED)
//   - 无 response 的网络错误 (断网/DNS 失败)
// 不重试:
//   - 用户/AbortController 主动取消 (CanceledError, code=ERR_CANCELED):
//     用已 aborted 的 signal 重发只会立刻再失败, 还会把取消伪装成
//     "网关连不上", 污染所有调用方的 name === 'CanceledError' 守卫
//   - POST/PUT/PATCH/DELETE 等非幂等方法 (重试可能双跑同步、重复落盘
//     或把已成功的写操作撞成 409)
//   - 401 (鉴权失败, 走下面拦截器跳登录)
//   - 其他 4xx (客户端错误, 重试无意义)
//
// 实现: 通过覆盖请求 config.adapter 复用 axios 自己的调度链,
// 1 次重试 (再失败就 reject), 退避 500ms 后 setTimeout 重发。
// retryCount 挂 config 上 (非模块全局), 避免并发请求互相干扰。

const MAX_RETRIES = 1
const RETRY_DELAY_MS = 500

const IDEMPOTENT_METHODS = new Set(['GET', 'HEAD'])

/** 请求是否被 AbortController/用户主动取消 (axios CanceledError)。
 *  结构判断, 不 instanceof 具体类 (CanceledError 是 AxiosError 子类,
 *  且测试环境的 axios mock 不导出该类)。 */
export function isCanceledError(error: unknown): boolean {
  if (axios.isCancel?.(error) === true) return true
  const e = error as { code?: string; name?: string } | null | undefined
  return e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError'
}

/** axios 内部用 InternalAxiosRequestConfig 走完整 dispatch; 公开 API 用 AxiosRequestConfig 即可。 */
declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 自增重试计数 (从 0 开始); 第一次请求未设值。 */
    __retryCount?: number
    /** 显式禁止重试 (冷启动 /me 短超时探测: 快速失败进故障态, 不要 30s 白屏)。 */
    __noRetry?: boolean
  }

  export interface InternalAxiosRequestConfig {
    /** 自增重试计数 (从 0 开始); 第一次请求未设值。 */
    __retryCount?: number
    /** 显式禁止重试。 */
    __noRetry?: boolean
  }
}

/**
 * 取请求 URL 的纯 pathname (剥掉 query/hash)。
 * axios config.url 一般是相对路径 ('/api/auth/me?_=123'), 用当前页
 * 作为 base 交给 URL 解析; 解析失败时退回手工截断, 绝不让分类抛错。
 */
export function requestPath(url: string): string {
  try {
    return new URL(url, window.location.href).pathname
  } catch {
    return url.split(/[?#]/, 1)[0]
  }
}

function shouldRetry(error: AxiosError): boolean {
  // 主动取消绝不重试 —— 必须排在最前 (CanceledError 也带 request 字段,
  // 否则会落入"无 response 的网络错误"分支)。
  if (isCanceledError(error)) return false
  // 调用方显式标记的快速失败请求 (冷启动 /me 探测)。
  if (error.config?.__noRetry) return false
  // 仅幂等方法可安全重试; 写操作重试可能双跑同步/重复提交/撞 409。
  const method = (error.config?.method ?? 'get').toUpperCase()
  if (!IDEMPOTENT_METHODS.has(method)) return false
  const status = error.response?.status
  // 5xx
  if (status !== undefined && status >= 500 && status < 600) return true
  // timeout
  if (error.code === 'ECONNABORTED') return true
  // network error (没拿到 response)
  if (!error.response && error.request) return true
  return false
}

/** 暴露给单测用: 判断一个错误是否应触发重试。 */
export const _shouldRetry = shouldRetry

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 复用 http 实例调度链重发请求 (会再次经过所有 interceptor, 含 401 + 自身重试)。
 *  上限靠 __retryCount 守门, 超 MAX_RETRIES 即 reject, 不会无限递归。 */
function retryRequest(config: InternalAxiosRequestConfig): Promise<unknown> {
  const nextCount = (config.__retryCount ?? 0) + 1
  if (nextCount > MAX_RETRIES) {
    // 达到上限, 把原始 error 透出去 (本轮 reject 的 error)
    return Promise.reject(new Error('retry exhausted'))
  }
  const retried: InternalAxiosRequestConfig = {
    ...config,
    __retryCount: nextCount,
  }
  // http.request 会再次走 http 实例的所有 interceptor — 重试成功时直接 resolve;
  // 仍失败时再次进 error handler, 此时 nextCount > MAX_RETRIES, 不会再重试。
  return sleep(RETRY_DELAY_MS).then(() => http.request(retried))
}

// 401 = 会话缺失/过期: 清掉前端会话状态并回登录页 (登录接口自身的 401
// 交给登录页展示"用户名或密码错误", 不在此跳转)。
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    const url = error.config?.url ?? ''

    // 重试在 401 拦截前: 401 不重试 (避免重复认证),
    // 重试仅对 transient 错误生效。
    const config = error.config as InternalAxiosRequestConfig | undefined
    if (config && shouldRetry(error)) {
      return retryRequest(config)
    }

    if (status === 401) {
      // /api/auth/login 自身的 401: 登录页展示 "用户名或密码错误", 不清不跳。
      // /api/auth/me 的 401: 冷启动探测的正常 "未登录", 清状态但不主动
      // replace —— 初次导航的路由守卫还在跑, 重复 replace 会造成双重导航,
      // 守卫/bootstrap 自己会落到登录页。
      // 按 pathname 精确匹配: includes() 会把 /api/auth/loginExtra、
      // /api/auth/members 误当登录/探测端点, 漏掉清状态与跳登录。
      const isLoginCall = requestPath(url) === '/api/auth/login'
      const isMeProbe = requestPath(url) === '/api/auth/me'
      if (!isLoginCall) {
        const auth = useAuthStore()
        auth.clear()
      }
      if (!isLoginCall && !isMeProbe) {
        const current = router.currentRoute.value
        if (current.name !== 'login') {
          void router.replace({
            name: 'login',
            query: current.fullPath !== '/' ? { redirect: current.fullPath } : undefined,
          })
        }
      }
    }
    return Promise.reject(error)
  },
)

/**
 * 网关不可达类故障 (冷启动/导航期的传输与 5xx 错误) 的中文分类。
 * 返回 null 表示这不是 "网关到不了": 401/4xx 是业务或鉴权问题,
 * 主动取消是用户意图, 非 axios 错误由调用方另行处理。
 */
export function gatewayUnavailableMessage(error: unknown): string | null {
  if (!axios.isAxiosError(error)) return null
  if (isCanceledError(error)) return null
  const status = error.response?.status
  if (status !== undefined && status >= 500 && status < 600) {
    return `网关暂时不可用 (HTTP ${status}), 请稍后重试`
  }
  if (error.code === 'ECONNABORTED') {
    return '网关响应超时, 请确认网关已启动后重试'
  }
  if (!error.response) {
    return '无法连接到网关, 请确认网关已启动后重试'
  }
  return null
}

/** 从后端错误体提取可展示消息 (后端统一 {error} / {detail} / {message})。 */
export function errorMessage(error: unknown, fallback = '请求失败'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { error?: string; detail?: string; message?: string } | undefined
    // 无业务错误体时, 把 5xx/超时/断网翻译成中文, 不裸显 axios 英文 message。
    return (
      data?.error ||
      data?.detail ||
      data?.message ||
      gatewayUnavailableMessage(error) ||
      error.message ||
      fallback
    )
  }
  if (error instanceof Error) {
    // 重试耗尽是 axios 拦截器在 retryRequest 上限抛出的内部 Error, 不该
    // 直接展示给用户 (技术术语)。翻译成"无法连接到网关, 请确认网关已启动"。
    if (error.message === 'retry exhausted') {
      return '无法连接到网关, 请确认网关已启动后重试'
    }
    return error.message
  }
  return fallback
}
