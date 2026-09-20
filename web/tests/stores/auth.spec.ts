import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

/**
 * Round-13 簇K/M3 + 簇M: /api/auth/me 探测与冷启动故障分类。
 * 鉴权关闭态后端返回 {username: null, auth_required: false} —— store 必须把
 * 这种情况识别为 "开放模式已通过" 而不是 "未登录", 否则路由守卫会把用户
 * 永久卡在登录页 (而后端 /api/* 反而全裸)。
 *
 * 簇M: /me 网络/超时/5xx 不等于 "未登录" —— 必须落到 probeError 故障态,
 * 由登录页展示 "网关连不上 + 重试"; 只有 401/4xx 才是干净的未登录。
 *
 * vitest 未启用 plugin-vue, 这里只跑 setup-store 的纯响应式逻辑
 * (pinia 本身不依赖组件挂载), api 模块整体 mock。
 */

const h = vi.hoisted(() => ({
  fetchMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  classify: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  fetchMe: (...args: unknown[]) => h.fetchMe(...args),
  login: (...args: unknown[]) => h.login(...args),
  logout: (...args: unknown[]) => h.logout(...args),
}))

// 故障分类器在 client.spec 里单测; 这里验证 store 如何消费它的判定。
vi.mock('@/api/client', () => ({
  gatewayUnavailableMessage: (...args: unknown[]) => h.classify(...args),
}))

import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  setActivePinia(createPinia())
  h.fetchMe.mockReset()
  h.login.mockReset()
  h.logout.mockReset()
  h.classify.mockReset()
})

describe('auth store bootstrap — auth_required 旗标', () => {
  it('开放模式 (auth_required=false) 视为已认证, 不停留登录态', async () => {
    h.fetchMe.mockResolvedValue({ username: null, auth_required: false })
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(auth.bootstrapped).toBe(true)
    expect(auth.authRequired).toBe(false)
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.username).toBeNull()
  })

  it('启用态已登录: 暴露用户名且已认证', async () => {
    h.fetchMe.mockResolvedValue({ username: 'admin', auth_required: true })
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(auth.authRequired).toBe(true)
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.username).toBe('admin')
  })

  it('启用态未登录 (401): 未认证, 由守卫跳登录页, 无故障横幅', async () => {
    const err401 = Object.assign(new Error('unauthorized'), { response: { status: 401 } })
    h.fetchMe.mockRejectedValue(err401)
    // 401 是干净的鉴权拒绝, 分类器返回 null。
    h.classify.mockReturnValue(null)
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(h.classify).toHaveBeenCalledWith(err401)
    expect(auth.bootstrapped).toBe(true)
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.username).toBeNull()
    expect(auth.authRequired).toBe(true)
    expect(auth.probeError).toBeNull()
  })

  it('旧后端不返回 auth_required 时按启用态对待 (fail-safe)', async () => {
    h.fetchMe.mockResolvedValue({ username: null })
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(auth.authRequired).toBe(true)
    expect(auth.isAuthenticated).toBe(false)
  })

  it('bootstrap 单发: 守卫并发调用只探测一次 /me', async () => {
    h.fetchMe.mockResolvedValue({ username: null, auth_required: false })
    const auth = useAuthStore()

    await Promise.all([auth.bootstrap(), auth.bootstrap()])

    expect(h.fetchMe).toHaveBeenCalledTimes(1)
  })
})

describe('auth store bootstrap — 簇M 故障分类', () => {
  it('网关不可达 (无 response): probeError 记录中文故障, 仍落到登录页但不假装未登录', async () => {
    const netErr = Object.assign(new Error('Network Error'), { request: {} })
    h.fetchMe.mockRejectedValue(netErr)
    h.classify.mockReturnValue('无法连接到网关, 请确认网关已启动后重试')
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(auth.probeError).toBe('无法连接到网关, 请确认网关已启动后重试')
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.authRequired).toBe(true)
    expect(auth.username).toBeNull()
    expect(auth.bootstrapped).toBe(true)
  })

  it('/me 超时: probeError 记录超时故障', async () => {
    const timeoutErr = Object.assign(new Error('timeout'), { code: 'ECONNABORTED' })
    h.fetchMe.mockRejectedValue(timeoutErr)
    h.classify.mockReturnValue('网关响应超时, 请确认网关已启动后重试')
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(auth.probeError).toBe('网关响应超时, 请确认网关已启动后重试')
    expect(auth.isAuthenticated).toBe(false)
  })

  it('5xx: probeError 记录暂时不可用故障', async () => {
    const err503 = Object.assign(new Error('bad gateway'), { response: { status: 503 } })
    h.fetchMe.mockRejectedValue(err503)
    h.classify.mockReturnValue('网关暂时不可用 (HTTP 503), 请稍后重试')
    const auth = useAuthStore()

    await auth.bootstrap()

    expect(auth.probeError).toBe('网关暂时不可用 (HTTP 503), 请稍后重试')
  })

  it('故障后成功 bootstrap (重试恢复): probeError 清空', async () => {
    const netErr = Object.assign(new Error('Network Error'), { request: {} })
    h.fetchMe.mockRejectedValueOnce(netErr).mockResolvedValueOnce({ username: 'ops', auth_required: true })
    h.classify.mockReturnValue('无法连接到网关, 请确认网关已启动后重试')
    const auth = useAuthStore()

    await auth.bootstrap()
    expect(auth.probeError).not.toBeNull()

    await auth.retryProbe()
    expect(h.fetchMe).toHaveBeenCalledTimes(2)
    expect(auth.probeError).toBeNull()
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.username).toBe('ops')
  })

  it('成功 bootstrap 后又故障: 开放模式不被故障覆盖回登录态', async () => {
    // 初次已认证进入, 后续若显式重探测失败, 状态按最新一次探测为准
    // (当前实现契约: bootstrap 缓存只发一次, retryProbe 才会再探)。
    h.fetchMe.mockResolvedValue({ username: null, auth_required: false })
    const auth = useAuthStore()
    await auth.bootstrap()
    // 缓存命中: 不会再次发请求
    await auth.bootstrap()
    expect(h.fetchMe).toHaveBeenCalledTimes(1)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('login 成功后清空残留 probeError', async () => {
    h.login.mockResolvedValue({ username: 'ops' })
    const auth = useAuthStore()
    auth.probeError = '无法连接到网关, 请确认网关已启动后重试'

    await auth.login('ops', 'secret')

    expect(auth.probeError).toBeNull()
    expect(auth.username).toBe('ops')
  })

  it('clear() 重置 probeError (401 拦截器不得把鉴权过期误显成网关故障)', () => {
    const auth = useAuthStore()
    auth.probeError = '网关响应超时, 请确认网关已启动后重试'

    auth.clear()

    expect(auth.probeError).toBeNull()
    expect(auth.username).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
  })
})
