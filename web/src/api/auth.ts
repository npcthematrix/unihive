import { http } from './client'

export interface LoginPayload {
  username: string
  password: string
}

export async function login(payload: LoginPayload): Promise<{ username: string }> {
  const { data } = await http.post<{ username: string }>('/api/auth/login', payload)
  return data
}

export async function logout(): Promise<void> {
  await http.post('/api/auth/logout')
}

/**
 * /api/auth/me 载荷。
 * - 鉴权启用态: 已登录 {username, auth_required: true}; 未登录由中间件返 401。
 * - 鉴权关闭态 (未配置任何 UNIHIVE_CONSOLE_*): {username: null, auth_required: false}。
 * 旧后端可能不带 auth_required, store 按 true (启用态) fail-safe 处理。
 */
export interface MeResponse {
  username: string | null
  auth_required?: boolean
}

/**
 * 冷启动探测超时。普通 GET 是 15s 超时 + 1 次重试 (最坏 ~30.5s);
 * 网关没启动时这会让登录页/白屏卡半分钟。探测只判活, 5s 快速失败,
 * 且 __noRetry 关闭退避重试 —— 失败由 store 分类成故障态 + 手动重试。
 */
export const BOOTSTRAP_PROBE_TIMEOUT_MS = 5_000

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await http.get<MeResponse>('/api/auth/me', {
    timeout: BOOTSTRAP_PROBE_TIMEOUT_MS,
    __noRetry: true,
  })
  return data
}
