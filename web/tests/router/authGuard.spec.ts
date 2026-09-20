import { describe, expect, it, vi } from 'vitest'

/**
 * Round-13 簇O: 路由守卫决策抽成纯函数。
 * 重点修复: 已登录用户打开 /login?redirect=/config 时, 旧守卫无视
 * redirect 一律回 /status, 深链失效。同时 redirect 必须防开放重定向。
 */
import { isSafeRedirect, resolveAuthRoute } from '@/router/authGuard'

function authState(over: Partial<{ bootstrapped: boolean; isAuthenticated: boolean }> = {}) {
  return {
    bootstrapped: over.bootstrapped ?? true,
    isAuthenticated: over.isAuthenticated ?? false,
    bootstrap: vi.fn().mockResolvedValue(undefined),
  }
}

function to(partial: Record<string, unknown>): Record<string, unknown> {
  return { fullPath: '/login', query: {}, ...partial }
}

describe('resolveAuthRoute — /login 分支', () => {
  it('未 bootstrapped 先探测, 未认证 → 放行到登录页', async () => {
    const auth = authState({ bootstrapped: false, isAuthenticated: false })
    const decision = await resolveAuthRoute(to({ name: 'login' }), auth)
    expect(auth.bootstrap).toHaveBeenCalledTimes(1)
    expect(decision).toBe(true)
  })

  it('已 bootstrapped 不重复探测', async () => {
    const auth = authState({ bootstrapped: true, isAuthenticated: false })
    await resolveAuthRoute(to({ name: 'login' }), auth)
    expect(auth.bootstrap).not.toHaveBeenCalled()
  })

  it('已认证 + 无 redirect → 默认回 status', async () => {
    const auth = authState({ isAuthenticated: true })
    const decision = await resolveAuthRoute(to({ name: 'login' }), auth)
    expect(decision).toEqual({ name: 'status' })
  })

  it('已认证 + 合法 redirect (/config?tab=proxy) → 回跳目标', async () => {
    const auth = authState({ isAuthenticated: true })
    const decision = await resolveAuthRoute(
      to({ name: 'login', query: { redirect: '/config?tab=proxy' } }),
      auth,
    )
    expect(decision).toEqual({ path: '/config?tab=proxy' })
  })

  it('协议相对地址 //evil.com → 拒绝, 回 status (防开放重定向)', async () => {
    const auth = authState({ isAuthenticated: true })
    const decision = await resolveAuthRoute(
      to({ name: 'login', query: { redirect: '//evil.com/x' } }),
      auth,
    )
    expect(decision).toEqual({ name: 'status' })
  })

  it('绝对 URL https://evil.com → 拒绝, 回 status', async () => {
    const auth = authState({ isAuthenticated: true })
    const decision = await resolveAuthRoute(
      to({ name: 'login', query: { redirect: 'https://evil.com' } }),
      auth,
    )
    expect(decision).toEqual({ name: 'status' })
  })

  it('redirect=/login → 回 status (避免守卫自跳循环)', async () => {
    const auth = authState({ isAuthenticated: true })
    const decision = await resolveAuthRoute(
      to({ name: 'login', query: { redirect: '/login' } }),
      auth,
    )
    expect(decision).toEqual({ name: 'status' })
  })

  it('redirect 含反斜杠/控制字符 → 回 status', async () => {
    const auth = authState({ isAuthenticated: true })
    for (const redirect of ['/\\evil.com', '/config\n', '/a\tb']) {
      const decision = await resolveAuthRoute(
        to({ name: 'login', query: { redirect } }),
        auth,
      )
      expect(decision).toEqual({ name: 'status' })
    }
  })

  it('redirect 非字符串 (数组/数字) → 回 status', async () => {
    const auth = authState({ isAuthenticated: true })
    for (const redirect of [['/config'], 42, null]) {
      const decision = await resolveAuthRoute(
        to({ name: 'login', query: { redirect } }),
        auth,
      )
      expect(decision).toEqual({ name: 'status' })
    }
  })
})

describe('resolveAuthRoute — 受保护路由分支', () => {
  it('未认证访问 /config → 跳登录并保留 redirect', async () => {
    const auth = authState({ bootstrapped: false, isAuthenticated: false })
    const decision = await resolveAuthRoute(
      to({ name: 'config', fullPath: '/config', query: {} }),
      auth,
    )
    expect(decision).toEqual({ name: 'login', query: { redirect: '/config' } })
  })

  it('未认证访问子路径 → redirect 保留完整 fullPath', async () => {
    const auth = authState({ isAuthenticated: false })
    const decision = await resolveAuthRoute(
      to({ name: 'interfaces', fullPath: '/interfaces?upstream=x', query: {} }),
      auth,
    )
    expect(decision).toEqual({
      name: 'login',
      query: { redirect: '/interfaces?upstream=x' },
    })
  })

  it('未认证访问根路径 → 不带 query', async () => {
    const auth = authState({ bootstrapped: false, isAuthenticated: false })
    const decision = await resolveAuthRoute(
      to({ name: 'status', fullPath: '/', query: {} }),
      auth,
    )
    expect(decision).toEqual({ name: 'login', query: undefined })
  })

  it('已认证 → 放行', async () => {
    const auth = authState({ isAuthenticated: true })
    const decision = await resolveAuthRoute(
      to({ name: 'config', fullPath: '/config', query: {} }),
      auth,
    )
    expect(decision).toBe(true)
  })
})

describe('isSafeRedirect', () => {
  it('/path 与带 query 的站内路径安全', () => {
    expect(isSafeRedirect('/config')).toBe(true)
    expect(isSafeRedirect('/config?tab=proxy')).toBe(true)
    // percent-encoded 斜杠在 query 里是合法数据, 不解码不触发协议相对
    expect(isSafeRedirect('/config?from=%2F%2Fevil.com')).toBe(true)
  })
  it('// 协议相对 / 绝对 URL / 空串 / 非斜杠开头不安全', () => {
    expect(isSafeRedirect('//evil.com')).toBe(false)
    expect(isSafeRedirect('https://evil.com')).toBe(false)
    expect(isSafeRedirect('status')).toBe(false)
    expect(isSafeRedirect('')).toBe(false)
  })
  it('反斜杠 / 前置空白 / ASCII 控制字符不安全 (HTML5 history 下浏览器会规范化反斜杠)', () => {
    expect(isSafeRedirect('/\\evil.com')).toBe(false)
    expect(isSafeRedirect(' //evil.com')).toBe(false)
    expect(isSafeRedirect('/config\n')).toBe(false)
    expect(isSafeRedirect('/config\tab')).toBe(false)
    expect(isSafeRedirect('/a\rb')).toBe(false)
  })
})
