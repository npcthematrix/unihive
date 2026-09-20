import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * Round-13 簇M: 冷启动 /me 探测契约。
 * 网关没启动时, 普通 GET 最坏要 15s 超时 + 1 次重试退避 ≈ 30.5s
 * 才落故障态 —— 用户看到的就是半分钟白屏。/me 必须挂短超时且
 * 标记 __noRetry, 把最坏冷启动时间压到 ~5s。
 */

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  http: {
    get: (...args: unknown[]) => h.get(...args),
    post: (...args: unknown[]) => h.post(...args),
  },
}))

import { BOOTSTRAP_PROBE_TIMEOUT_MS, fetchMe, login, logout } from '@/api/auth'

beforeEach(() => {
  h.get.mockReset()
  h.post.mockReset()
})

describe('fetchMe — 冷启动探测', () => {
  it('GET /api/auth/me 带短超时 + __noRetry (最坏 ~5s, 不吃 15s+重试)', async () => {
    h.get.mockResolvedValue({ data: { username: null, auth_required: false } })

    await fetchMe()

    expect(h.get).toHaveBeenCalledTimes(1)
    const [url, opts] = h.get.mock.calls[0]
    expect(url).toBe('/api/auth/me')
    expect(opts).toMatchObject({ timeout: BOOTSTRAP_PROBE_TIMEOUT_MS, __noRetry: true })
    expect(BOOTSTRAP_PROBE_TIMEOUT_MS).toBe(5_000)
  })

  it('透传响应体 (开放/登录态/旧后端三种形态由 store 解释)', async () => {
    h.get.mockResolvedValueOnce({ data: { username: 'ops', auth_required: true } })
    await expect(fetchMe()).resolves.toEqual({ username: 'ops', auth_required: true })

    h.get.mockResolvedValueOnce({ data: { username: null } })
    await expect(fetchMe()).resolves.toEqual({ username: null })
  })
})

describe('login / logout 路径不变', () => {
  it('login POST 载荷', async () => {
    h.post.mockResolvedValue({ data: { username: 'ops' } })
    await login({ username: 'ops', password: 'x' })
    expect(h.post).toHaveBeenCalledWith('/api/auth/login', { username: 'ops', password: 'x' })
  })

  it('logout POST', async () => {
    h.post.mockResolvedValue({})
    await logout()
    expect(h.post).toHaveBeenCalledWith('/api/auth/logout')
  })
})
