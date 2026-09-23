import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * client.ts 注入拦截器到 axios.create() 返回的实例。
 * 401 拦截 + 5xx 重试测试用真实模块 (不 mock client.ts 自身),
 * 因为拦截器逻辑只在模块加载时挂一次, 这层行为最值得保护。
 *
 * vi.mock factory 会 hoisted 到顶, 顶层 const 不能直接引用;
 * 我们把"测试句柄"放回 factory 内的 vi.hoisted 闭包中。
 *
 * Vitest 5: 模块求值阶段 (import 时 axios.create / response.use) 产生的
 * mock.calls 不体现在测试侧句柄上 (模块图与测试 worker 分 realm)。
 * 因此根 beforeEach 先 resetModules(), 再在测试上下文内动态 import
 * '@/api/client', 模块加载期的调用即被正常记录; 所有 axios mock fn
 * 必须放在 h 里跨 factory 重跑共享, 顶层 stale 的 `axios` 句柄才仍有效。
 */

const h = vi.hoisted(() => {
  const mockAuthClear = vi.fn()
  const mockRouterReplace = vi.fn()
  const mockRouterCurrentRoute = { value: { name: 'status', fullPath: '/status' } }
  const mockUseResponse = vi.fn()
  const mockAxiosInstance = {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: mockUseResponse },
    },
  }
  const mockIsCancel = vi.fn(() => false)
  const mockCreate = vi.fn(() => mockAxiosInstance)
  const mockIsAxiosError = vi.fn()
  return {
    mockAuthClear,
    mockRouterReplace,
    mockRouterCurrentRoute,
    mockUseResponse,
    mockAxiosInstance,
    mockIsCancel,
    mockCreate,
    mockIsAxiosError,
  }
})

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    clear: h.mockAuthClear,
  }),
}))

vi.mock('@/router', () => ({
  router: {
    currentRoute: h.mockRouterCurrentRoute,
    replace: h.mockRouterReplace,
  },
}))

vi.mock('axios', () => ({
  default: {
    create: h.mockCreate,
    isAxiosError: h.mockIsAxiosError,
    isCancel: h.mockIsCancel,
  },
}))

import axios from 'axios'

type ClientExports = typeof import('@/api/client')
let http: ClientExports['http']
let _shouldRetry: ClientExports['_shouldRetry']
let errorMessage: ClientExports['errorMessage']
let gatewayUnavailableMessage: ClientExports['gatewayUnavailableMessage']
let isCanceledError: ClientExports['isCanceledError']

// 每个用例重新加载 client 模块 (见文件头 Vitest 5 realm 说明)
beforeEach(async () => {
  vi.clearAllMocks()
  vi.resetModules()
  const mod = await import('@/api/client')
  http = mod.http
  _shouldRetry = mod._shouldRetry
  errorMessage = mod.errorMessage
  gatewayUnavailableMessage = mod.gatewayUnavailableMessage
  isCanceledError = mod.isCanceledError
})

describe('http 实例 + 401 拦截器', () => {
  beforeEach(() => {
    // 注: 不要 mockClear(mockUseResponse) — 会清掉 mock.calls,
    // 后续 errorHandler 取不到注入的回调。
    h.mockAuthClear.mockClear()
    h.mockRouterReplace.mockClear()
    h.mockRouterCurrentRoute.value = { name: 'status', fullPath: '/status' }
  })

  it('axios.create 被调用 (baseURL / timeout / withCredentials)', () => {
    expect(axios.create).toHaveBeenCalledWith({
      baseURL: '/',
      timeout: 15_000,
      withCredentials: true,
    })
  })

  it('http 实例导出', () => {
    expect(http).toBe(h.mockAxiosInstance)
  })

  it('response.use 挂了一个拦截器 (success passthrough + error handler)', () => {
    expect(h.mockUseResponse).toHaveBeenCalledTimes(1)
    const [success, error] = h.mockUseResponse.mock.calls[0]
    expect(typeof success).toBe('function')
    expect(typeof error).toBe('function')
    const fakeResponse = { data: 'ok' }
    expect(success(fakeResponse)).toBe(fakeResponse)
  })

  describe('401 拦截逻辑', () => {
    function trigger401(url: string) {
      const errorHandler = h.mockUseResponse.mock.calls[0][1]
      const err = {
        response: { status: 401 },
        config: { url },
      }
      return errorHandler(err)
    }

    it('401 + 非登录接口 + 在 status 路由 → auth.clear + router.replace("/login?redirect=/status")', () => {
      return trigger401('/api/status').catch((e: unknown) => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).toHaveBeenCalledWith({
          name: 'login',
          query: { redirect: '/status' },
        })
        expect(e).toBeDefined()
      })
    })

    it('401 + 已经在 login 路由 → 不替换路由 (避免循环)', () => {
      h.mockRouterCurrentRoute.value = { name: 'login', fullPath: '/login' }
      return trigger401('/api/status').catch(() => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).not.toHaveBeenCalled()
      })
    })

    it('401 + URL 是 /api/auth/login → 不清 store, 不跳路由 (登录自身错误)', () => {
      return trigger401('/api/auth/login').catch(() => {
        expect(h.mockAuthClear).not.toHaveBeenCalled()
        expect(h.mockRouterReplace).not.toHaveBeenCalled()
      })
    })

    it('401 + URL 是 /api/auth/me → 清会话但不替换路由 (bootstrap 自己分类)', () => {
      // 冷启动探测 /me 返回 401 时, 路由守卫本身还在初次导航中,
      // 拦截器再 router.replace(login) 会与守卫的重定向双重导航。
      // clear() 仍要执行 (服务端明确拒绝), 跳转交给守卫/bootstrap。
      return trigger401('/api/auth/me').catch(() => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).not.toHaveBeenCalled()
      })
    })

    it('401 + URL 是 /api/auth/me?带 query → 仍按探测豁免 (按 pathname 精确匹配)', () => {
      return trigger401('/api/auth/me?_=' + Date.now()).catch(() => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).not.toHaveBeenCalled()
      })
    })

    it('401 + URL 仅前缀相似 /api/auth/members → 不当作 /me 豁免, 正常清会话跳转', () => {
      // 旧实现 url.includes("/api/auth/me") 会误吞这个假设端点
      return trigger401('/api/auth/members').catch(() => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).toHaveBeenCalledTimes(1)
      })
    })

    it('401 + URL 仅前缀相似 /api/auth/loginExtra → 不当作 login 豁免', () => {
      return trigger401('/api/auth/loginExtra').catch(() => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).toHaveBeenCalledTimes(1)
      })
    })

    it('401 + 当前路径是 "/" → 不带 redirect 参数', () => {
      h.mockRouterCurrentRoute.value = { name: 'status', fullPath: '/' }
      return trigger401('/api/status').catch(() => {
        expect(h.mockRouterReplace).toHaveBeenCalledWith({
          name: 'login',
          query: undefined,
        })
      })
    })

    it('非 401 (4xx) → 不清 store, 不跳路由, 仍 reject', () => {
      const errorHandler = h.mockUseResponse.mock.calls[0][1]
      const err = {
        response: { status: 400 },
        config: { url: '/api/status' },
      }
      return errorHandler(err).catch((e: unknown) => {
        expect(h.mockAuthClear).not.toHaveBeenCalled()
        expect(h.mockRouterReplace).not.toHaveBeenCalled()
        expect(e).toBe(err)
      })
    })

    it('非 401 (404) → 不清 store, 不跳路由', () => {
      const errorHandler = h.mockUseResponse.mock.calls[0][1]
      const err = {
        response: { status: 404 },
        config: { url: '/api/missing' },
      }
      return errorHandler(err).catch(() => {
        expect(h.mockAuthClear).not.toHaveBeenCalled()
        expect(h.mockRouterReplace).not.toHaveBeenCalled()
      })
    })

    it('config.url 缺失 → 不抛, 走空 url 路径', () => {
      const errorHandler = h.mockUseResponse.mock.calls[0][1]
      const err = { response: { status: 401 }, config: undefined }
      return errorHandler(err).catch(() => {
        expect(h.mockAuthClear).toHaveBeenCalledTimes(1)
        expect(h.mockRouterReplace).toHaveBeenCalledTimes(1)
      })
    })

    it('redirect 保留子路径 (e.g. /config?tab=proxy)', () => {
      h.mockRouterCurrentRoute.value = {
        name: 'config',
        fullPath: '/config?tab=proxy',
      }
      return trigger401('/api/config').catch(() => {
        expect(h.mockRouterReplace).toHaveBeenCalledWith({
          name: 'login',
          query: { redirect: '/config?tab=proxy' },
        })
      })
    })
  })
})

describe('errorMessage', () => {
  function makeAxiosLike(data: unknown, status = 500, message = 'Network Error'): unknown {
    return {
      isAxiosError: true,
      response: { status, data },
      message,
    }
  }

  let isAxiosErrorMock: ReturnType<typeof vi.mocked<typeof axios.isAxiosError>>

  beforeEach(() => {
    isAxiosErrorMock = vi.mocked(axios.isAxiosError)
    isAxiosErrorMock.mockImplementation(
      (e: unknown): e is import('axios').AxiosError =>
        Boolean((e as { isAxiosError?: boolean })?.isAxiosError),
    )
  })

  afterEach(() => {
    isAxiosErrorMock.mockReset()
  })

  it('axios error + data.error → 优先取 error', () => {
    expect(errorMessage(makeAxiosLike({ error: '服务端错误' }))).toBe('服务端错误')
  })

  it('axios error + 仅 data.detail → 取 detail', () => {
    expect(errorMessage(makeAxiosLike({ detail: '详情错误' }))).toBe('详情错误')
  })

  it('axios error + 仅 data.message → 取 message', () => {
    expect(errorMessage(makeAxiosLike({ message: '消息错误' }))).toBe('消息错误')
  })

  it('axios error + data 三字段都有 → 取 error (优先级最高)', () => {
    expect(errorMessage(makeAxiosLike({ error: 'e', detail: 'd', message: 'm' }))).toBe('e')
  })

  it('axios error + data 为 null + 5xx → 翻译成网关不可用中文', () => {
    // Round-13 簇M: 裸英文 "Network Error" 不得回显给用户;
    // 拿不到业务错误体时按状态码/传输错误分类成中文。
    expect(errorMessage(makeAxiosLike(null))).toBe('网关暂时不可用 (HTTP 500), 请稍后重试')
  })

  it('axios error + data 字段全无 + 5xx → 翻译成网关不可用中文', () => {
    expect(errorMessage(makeAxiosLike({}))).toBe('网关暂时不可用 (HTTP 500), 请稍后重试')
  })

  it('axios error + 全空字符串字段 + 5xx → 翻译成网关不可用中文', () => {
    expect(errorMessage(makeAxiosLike({ error: '', detail: '', message: '' }))).toBe(
      '网关暂时不可用 (HTTP 500), 请稍后重试',
    )
  })

  it('axios error 但 response 缺失 (网关未启动) → 翻译成无法连接中文', () => {
    const e = { isAxiosError: true, message: 'no response', request: {} }
    expect(errorMessage(e)).toBe('无法连接到网关, 请确认网关已启动后重试')
  })

  it('axios timeout (ECONNABORTED) → 翻译成响应超时中文', () => {
    const e = { isAxiosError: true, code: 'ECONNABORTED', message: 'timeout of 5000ms exceeded' }
    expect(errorMessage(e)).toBe('网关响应超时, 请确认网关已启动后重试')
  })

  it('axios error + 4xx + 无业务错误体 → 回落到 error.message (不谎称网关挂了)', () => {
    const e = { isAxiosError: true, response: { status: 400 }, message: 'bad request' }
    expect(errorMessage(e)).toBe('bad request')
  })

  it('普通 Error → 取 .message', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom')
  })

  it('retry exhausted (axios 拦截器内部错误) → 翻译成可读消息', () => {
    // 登录页 POST /api/auth/login 在网关未启动时, 拦截器重试 1 次后仍失败
    // 会 reject 出 new Error('retry exhausted'), 必须翻译成"无法连接到网关"
    expect(errorMessage(new Error('retry exhausted'))).toBe(
      '无法连接到网关, 请确认网关已启动后重试',
    )
  })

  it('retry exhausted + 自定义 fallback → 仍优先用翻译消息', () => {
    // 用户传 fallback 不应覆盖内置翻译, 因为这是已知错误
    expect(errorMessage(new Error('retry exhausted'), '备用')).toBe(
      '无法连接到网关, 请确认网关已启动后重试',
    )
  })

  it('其他内部 Error.message 不被错误翻译', () => {
    expect(errorMessage(new Error('timeout of 30ms exceeded'))).toBe(
      'timeout of 30ms exceeded',
    )
  })

  it('非 Error 非 axios error → fallback', () => {
    expect(errorMessage('字符串')).toBe('请求失败')
    expect(errorMessage(null)).toBe('请求失败')
    expect(errorMessage(undefined)).toBe('请求失败')
    expect(errorMessage(42)).toBe('请求失败')
  })

  it('自定义 fallback 参数', () => {
    expect(errorMessage('字符串', '自定义兜底')).toBe('自定义兜底')
  })

  it('非 axios error → 不尝试读 response.data', () => {
    // 防止有人误传一个普通对象, 我们应走 Error 分支或 fallback, 不应读 .response.data
    const weird = { response: { data: { error: 'X' } } } // 没有 isAxiosError 标志
    expect(errorMessage(weird)).toBe('请求失败')
  })
})

/**
 * gatewayUnavailableMessage — Round-13 簇M: 冷启动 /me 探测失败分类。
 * 只翻译 "网关到不了" 这一类传输/服务端故障; 401/4xx/非 axios 错误
 * 返回 null (调用方据此区分 "未登录" 与 "网关挂了")。
 */
describe('gatewayUnavailableMessage', () => {
  let isAxiosErrorMock: ReturnType<typeof vi.mocked<typeof axios.isAxiosError>>

  beforeEach(() => {
    isAxiosErrorMock = vi.mocked(axios.isAxiosError)
    isAxiosErrorMock.mockImplementation(
      (e: unknown): e is import('axios').AxiosError =>
        Boolean((e as { isAxiosError?: boolean })?.isAxiosError),
    )
  })

  afterEach(() => {
    isAxiosErrorMock.mockReset()
  })

  it('无 response + 有 request (连接被拒/DNS) → 无法连接中文', () => {
    expect(
      gatewayUnavailableMessage({ isAxiosError: true, request: {}, message: 'Network Error' }),
    ).toBe('无法连接到网关, 请确认网关已启动后重试')
  })

  it('ECONNABORTED (超时) → 响应超时中文', () => {
    expect(
      gatewayUnavailableMessage({
        isAxiosError: true,
        code: 'ECONNABORTED',
        message: 'timeout of 5000ms exceeded',
      }),
    ).toBe('网关响应超时, 请确认网关已启动后重试')
  })

  it('5xx (500/502/503/504) → 带状态码的暂时不可用中文', () => {
    for (const status of [500, 502, 503, 504]) {
      expect(gatewayUnavailableMessage({ isAxiosError: true, response: { status } })).toBe(
        `网关暂时不可用 (HTTP ${status}), 请稍后重试`,
      )
    }
  })

  it('401/403/404/400 → null (这是业务/鉴权问题, 不是网关不可达)', () => {
    for (const status of [400, 401, 403, 404]) {
      expect(gatewayUnavailableMessage({ isAxiosError: true, response: { status } })).toBeNull()
    }
  })

  it('CanceledError → null (用户主动取消, 不得冒充网关故障)', () => {
    expect(
      gatewayUnavailableMessage({
        isAxiosError: true,
        code: 'ERR_CANCELED',
        name: 'CanceledError',
        request: {},
      }),
    ).toBeNull()
  })

  it('非 axios error (普通 Error/null/字符串) → null', () => {
    expect(gatewayUnavailableMessage(new Error('boom'))).toBeNull()
    expect(gatewayUnavailableMessage(null)).toBeNull()
    expect(gatewayUnavailableMessage('Network Error')).toBeNull()
  })
})

/**
 * _shouldRetry — 拦截器"是否重试"判定的纯函数。
 * 直接导出供单测, 不必经过 interceptor 调用栈 mock。
 */
describe('_shouldRetry', () => {
  function errLike(partial: Partial<import('axios').AxiosError>): import('axios').AxiosError {
    return partial as import('axios').AxiosError
  }

  it('500/502/503/504 → 重试', () => {
    expect(_shouldRetry(errLike({ response: { status: 500 } as never }))).toBe(true)
    expect(_shouldRetry(errLike({ response: { status: 502 } as never }))).toBe(true)
    expect(_shouldRetry(errLike({ response: { status: 503 } as never }))).toBe(true)
    expect(_shouldRetry(errLike({ response: { status: 504 } as never }))).toBe(true)
  })

  it('599 → 重试 (5xx 整段)', () => {
    expect(_shouldRetry(errLike({ response: { status: 599 } as never }))).toBe(true)
  })

  it('400/401/403/404 → 不重试', () => {
    expect(_shouldRetry(errLike({ response: { status: 400 } as never }))).toBe(false)
    expect(_shouldRetry(errLike({ response: { status: 401 } as never }))).toBe(false)
    expect(_shouldRetry(errLike({ response: { status: 403 } as never }))).toBe(false)
    expect(_shouldRetry(errLike({ response: { status: 404 } as never }))).toBe(false)
  })

  it('2xx/3xx → 不重试 (不是错误)', () => {
    expect(_shouldRetry(errLike({ response: { status: 200 } as never }))).toBe(false)
    expect(_shouldRetry(errLike({ response: { status: 304 } as never }))).toBe(false)
  })

  it('ECONNABORTED (timeout) → 重试', () => {
    expect(_shouldRetry(errLike({ code: 'ECONNABORTED', message: 'timeout' }))).toBe(true)
  })

  it('无 response + 有 request (网络错误) → 重试', () => {
    expect(
      _shouldRetry(errLike({ request: {}, message: 'Network Error' })),
    ).toBe(true)
  })

  it('无 response + 无 request → 不重试 (无法判断, 不冒险重试)', () => {
    expect(_shouldRetry(errLike({ message: 'unknown' }))).toBe(false)
  })

  it('ERR_CANCELED (AbortController 取消) → 不重试, 即使带 request', () => {
    // 回归: CanceledError 带 xhr request 字段, 旧逻辑误判为网络错误,
    // 用已 aborted 的 signal 重发后伪装成 "retry exhausted / 网关连不上"。
    expect(
      _shouldRetry(
        errLike({
          code: 'ERR_CANCELED',
          name: 'CanceledError',
          request: {},
          message: 'canceled',
          config: { method: 'get' } as never,
        }),
      ),
    ).toBe(false)
  })

  it('axios.isCancel 判定为取消 → 不重试', () => {
    h.mockIsCancel.mockImplementation((e: unknown) => e === sentinel)
    const sentinel = { request: {}, message: 'canceled' }
    expect(_shouldRetry(errLike(sentinel as never))).toBe(false)
  })

  it('POST/PUT/PATCH/DELETE + 5xx → 不重试 (非幂等, 可能重复落盘)', () => {
    for (const method of ['post', 'put', 'patch', 'delete']) {
      expect(
        _shouldRetry(
          errLike({ response: { status: 503 } as never, config: { method } as never }),
        ),
      ).toBe(false)
    }
  })

  it('POST + timeout/网络错误 → 不重试 (同步双跑回归)', () => {
    expect(
      _shouldRetry(errLike({ code: 'ECONNABORTED', config: { method: 'post' } as never })),
    ).toBe(false)
    expect(
      _shouldRetry(
        errLike({ request: {}, message: 'Network Error', config: { method: 'POST' } as never }),
      ),
    ).toBe(false)
  })

  it('HEAD + 5xx → 重试 (幂等)', () => {
    expect(
      _shouldRetry(errLike({ response: { status: 500 } as never, config: { method: 'head' } as never })),
    ).toBe(true)
  })

  it('config.method 缺失 → 按 GET 处理 (axios 默认方法)', () => {
    expect(_shouldRetry(errLike({ response: { status: 502 } as never }))).toBe(true)
  })

  it('__noRetry: 冷启动 /me 探测即使 5xx/超时/断网也绝不重试', () => {
    // Round-13 簇M: /me 挂短超时 + __noRetry, 最坏 5s 进登录页故障态,
    // 而不是 15s + 重试 500ms ≈ 30.5s 白屏。
    expect(
      _shouldRetry(
        errLike({ response: { status: 503 } as never, config: { __noRetry: true } as never }),
      ),
    ).toBe(false)
    expect(
      _shouldRetry(
        errLike({ code: 'ECONNABORTED', config: { __noRetry: true } as never }),
      ),
    ).toBe(false)
    expect(
      _shouldRetry(
        errLike({ request: {}, message: 'x', config: { __noRetry: true } as never }),
      ),
    ).toBe(false)
  })

  it('__noRetry 缺省 (undefined) 时既有重试行为不变', () => {
    expect(
      _shouldRetry(errLike({ response: { status: 503 } as never, config: {} as never })),
    ).toBe(true)
  })
})

describe('isCanceledError', () => {
  afterEach(() => {
    h.mockIsCancel.mockImplementation(() => false)
  })

  it('code=ERR_CANCELED / name=CanceledError → true', () => {
    expect(isCanceledError({ code: 'ERR_CANCELED' })).toBe(true)
    expect(isCanceledError({ name: 'CanceledError' })).toBe(true)
  })

  it('axios.isCancel 命中 → true', () => {
    const target = { message: 'canceled' }
    h.mockIsCancel.mockImplementation((e: unknown) => e === target)
    expect(isCanceledError(target)).toBe(true)
  })

  it('普通网络/超时错误 → false', () => {
    expect(isCanceledError({ code: 'ECONNABORTED' })).toBe(false)
    expect(isCanceledError({ name: 'Error', message: 'Network Error' })).toBe(false)
    expect(isCanceledError(null)).toBe(false)
    expect(isCanceledError(undefined)).toBe(false)
  })
})

/**
 * 401 vs 重试互斥: 401 + transient 错误同时出现时, 401 优先 (不重试)。
 * 集成重试端到端需要 msw 或真 server, 超出当前 vitest 范围;
 * 这里只验证"5xx 错误 → shouldRetry = true (决策分支)",
 * 完整 retryRequest 行为靠 _shouldRetry + 现有 401 测试已覆盖。
 */
describe('401 拦截 vs 重试互斥', () => {
  it('5xx 不带 canceled → shouldRetry true (走重试决策, 不走 401)', () => {
    // 直接用 _shouldRetry 验证决策 — 拦截器内会调 retryRequest,
    // 但真实调用依赖 axios.request 调度链, 在 mock 环境不验证。
    expect(
      _shouldRetry({ response: { status: 503 }, message: 'Service Unavailable' } as never),
    ).toBe(true)
  })

  it('401 → 不进入 shouldRetry (即不会触发重试)', () => {
    expect(
      _shouldRetry({ response: { status: 401 }, message: 'Unauthorized' } as never),
    ).toBe(false)
  })
})
