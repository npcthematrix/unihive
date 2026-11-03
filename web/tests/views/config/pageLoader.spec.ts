import { describe, expect, it, vi } from 'vitest'

/**
 * Round-13 簇O: ConfigView 加载编排抽出为纯 composable。
 * 旧实现先 await getConfig(), 实体摘要 / 工具清单 / proxy 三路独立请求
 * 等它落地后才发出 —— 无谓瀑布; 且任一软失败的处理散在 SFC 里无法单测。
 * 新实现各路请求同一 tick 并发 (Promise.allSettled), 失败彼此独立;
 * 池参数快照 (round-14 H2) 也并入同一条 load, 面板不再自己 onMounted 拉。
 */
import { createConfigPageLoader, type ConfigPageApis } from '@/views/config/pageLoader'

interface Controlled<T> {
  promise: Promise<T>
  resolve: (v: T) => void
  reject: (e: unknown) => void
}

function deferred<T>(): Controlled<T> {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** 宏任务边界: 刷掉所有已就绪微任务 (Promise.allSettled 链跨多个 microtick)。 */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

/** 每次调用按顺序返回下一个 deferred.promise (模拟多轮 load)。 */
function queued<T>(items: Array<Controlled<T>>): () => Promise<T> {
  let i = 0
  return () => items[i++].promise
}

function makeApis(overrides: Partial<ConfigPageApis> = {}) {
  const calls: string[] = []
  const apis: ConfigPageApis = {
    getConfig: vi.fn(() => {
      calls.push('config')
      return overrides.getConfig?.() ?? Promise.resolve({} as never)
    }),
    getEntities: vi.fn(() => {
      calls.push('entities')
      return overrides.getEntities?.() ?? Promise.resolve({} as never)
    }),
    getInterfaces: vi.fn(() => {
      calls.push('interfaces')
      return overrides.getInterfaces?.() ?? Promise.resolve({ tools: [] })
    }),
    getPool: vi.fn(() => {
      calls.push('pool')
      return overrides.getPool?.() ?? Promise.resolve({} as never)
    }),
    getProxy: vi.fn(() => {
      calls.push('proxy')
      return overrides.getProxy?.() ?? Promise.resolve({} as never)
    }),
  }
  return { apis, calls }
}

function setup(overrides: Partial<ConfigPageApis> = {}, isProxyActive = () => false) {
  const notify = vi.fn()
  const { apis, calls } = makeApis(overrides)
  const loader = createConfigPageLoader(apis, notify, isProxyActive)
  return { loader, apis, calls, notify }
}

describe('createConfigPageLoader — 并发', () => {
  it('五路请求在同一 tick 全部发出 (不被 config 阻塞成瀑布)', async () => {
    const { loader, calls } = setup({
      getConfig: () => deferred().promise,
      getEntities: () => deferred().promise,
      getInterfaces: () => deferred().promise,
      getPool: () => deferred().promise,
      getProxy: () => deferred().promise,
    })
    loader.load()
    expect(calls).toEqual(['config', 'entities', 'interfaces', 'pool', 'proxy'])
  })
})

describe('createConfigPageLoader — 结果落地', () => {
  it('config 成功: 写入 config + lastRefresh, 无 loadError', async () => {
    const { loader } = setup({
      getConfig: () => Promise.resolve({ upstreams: {} } as never),
    })
    await loader.load()
    expect(loader.config.value).toEqual({ upstreams: {} })
    expect(loader.lastRefresh.value).toEqual(expect.any(Number))
    expect(loader.loadError.value).toBe('')
    expect(loader.loading.value).toBe(false)
    expect(loader.manualLoading.value).toBe(false)
  })

  it('config 失败: loadError 记录, 但实体/工具/proxy 照常落地', async () => {
    const { loader, notify } = setup({
      getConfig: () => Promise.reject(new Error('boom')),
      getEntities: () => Promise.resolve({ source: 'db' } as never),
      getInterfaces: () =>
        Promise.resolve({
          tools: [
            {
              name: 'm_quote',
              description: '行情',
              dangerous: false,
              cache_ttl_key: 'realtime_quote',
              params: [],
            },
          ],
        }),
      getProxy: () => Promise.resolve({ coverage: [] } as never),
    })
    await loader.load()
    expect(loader.loadError.value).toBe('boom')
    expect(loader.config.value).toBeNull()
    expect(loader.entities.value).toEqual({ source: 'db' })
    expect(loader.interfaceTools.value).toHaveLength(1)
    expect(loader.interfaceTools.value[0].name).toBe('m_quote')
    expect(loader.proxy.value).toEqual({ coverage: [] })
    expect(notify).not.toHaveBeenCalled()
  })

  it('entities 失败: 只 toast 不阻塞, config 仍落地', async () => {
    const { loader, notify } = setup({
      getConfig: () => Promise.resolve({ upstreams: {} } as never),
      getEntities: () => Promise.reject(new Error('ent-boom')),
    })
    await loader.load()
    expect(loader.config.value).toEqual({ upstreams: {} })
    expect(loader.entities.value).toBeNull()
    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify.mock.calls[0][0]).toContain('配置库实体')
  })

  it('interfaces 失败: 只 toast 不阻塞', async () => {
    const { loader, notify } = setup({
      getInterfaces: () => Promise.reject(new Error('ifs-boom')),
    })
    await loader.load()
    expect(loader.interfaceTools.value).toEqual([])
    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify.mock.calls[0][0]).toContain('工具清单')
  })

  it('proxy 失败: 独立 proxyError, load 整体不 reject', async () => {
    const { loader } = setup({
      getProxy: () => Promise.reject(new Error('proxy-boom')),
    })
    await expect(loader.load()).resolves.toBeUndefined()
    expect(loader.proxyError.value).toBe('proxy-boom')
    expect(loader.proxyLastRefresh.value).toBeNull()
    expect(loader.proxyLoading.value).toBe(false)
  })
})

describe('createConfigPageLoader — proxy tab 时机', () => {
  it('非 proxy tab: load 不等慢 proxy, 稍后 proxy 落地', async () => {
    const proxyD = deferred<{ coverage: string[] }>()
    const { loader } = setup({ getProxy: () => proxyD.promise }, () => false)
    const done = vi.fn()
    void loader.load().then(done)
    await flush()
    expect(done).toHaveBeenCalled()

    proxyD.resolve({ coverage: [] })
    await proxyD.promise
    expect(loader.proxy.value).toEqual({ coverage: [] })
  })

  it('在 proxy tab: load 等 proxy settled 才返回', async () => {
    const proxyD = deferred<{ coverage: string[] }>()
    const { loader } = setup({ getProxy: () => proxyD.promise }, () => true)
    const done = vi.fn()
    void loader.load().then(done)
    await flush()
    expect(done).not.toHaveBeenCalled()

    proxyD.resolve({ coverage: ['fuyao'] })
    await loader.load() // 新一轮 load 立即取已 resolve 的 proxy
    expect(loader.proxy.value).toEqual({ coverage: ['fuyao'] })
  })
})

describe('createConfigPageLoader — 加载标志', () => {
  it('手动刷新期间 manualLoading=true, 结束复位', async () => {
    const cfgD = deferred<Record<string, unknown>>()
    const { loader } = setup({ getConfig: () => cfgD.promise })
    const task = loader.load(true)
    await Promise.resolve()
    expect(loader.manualLoading.value).toBe(true)
    expect(loader.loading.value).toBe(false)
    cfgD.resolve({})
    await task
    expect(loader.manualLoading.value).toBe(false)
  })

  it('常规加载期间 loading=true', async () => {
    const cfgD = deferred<Record<string, unknown>>()
    const { loader } = setup({ getConfig: () => cfgD.promise })
    const task = loader.load()
    await Promise.resolve()
    expect(loader.loading.value).toBe(true)
    cfgD.resolve({})
    await task
    expect(loader.loading.value).toBe(false)
  })

  it('loadProxy 可由重试按钮单独调用并复位 proxyError', async () => {
    const { loader } = setup({ getProxy: () => Promise.resolve({ coverage: ['x'] } as never) })
    loader.proxyError.value = 'stale'
    await loader.loadProxy()
    expect(loader.proxy.value).toEqual({ coverage: ['x'] })
    expect(loader.proxyError.value).toBe('')
    expect(loader.proxyLastRefresh.value).toEqual(expect.any(Number))
  })

  it('loadProxy 与 load 并发时, 旧 load 的过期 proxy reject 不覆盖新 loadProxy', async () => {
    const px = [deferred<{ coverage: string[] }>(), deferred<{ coverage: string[] }>()]
    const { loader } = setup({ getProxy: queued(px) })
    void loader.load() // 第一轮 proxy 挂起
    const proxyRetry = loader.loadProxy() // 第二轮
    px[1].resolve({ coverage: ['fresh'] })
    await proxyRetry
    px[0].reject(new Error('stale-proxy'))
    await px[0].promise.catch(() => undefined)
    await flush()
    expect(loader.proxy.value).toEqual({ coverage: ['fresh'] })
    expect(loader.proxyError.value).toBe('')
  })
})

describe('createConfigPageLoader — 并发重入: 只有最新一轮落地', () => {
  it('旧 load 的 reject 不得清空新一轮已成功的数据或错误旗标', async () => {
    const cfg = [deferred<Record<string, unknown>>(), deferred<Record<string, unknown>>()]
    const ent = [deferred<Record<string, unknown>>(), deferred<Record<string, unknown>>()]
    const ifs = [deferred<{ tools: unknown[] }>(), deferred<{ tools: unknown[] }>()]
    const px = [deferred<Record<string, unknown>>(), deferred<Record<string, unknown>>()]
    const { loader, notify } = setup({
      getConfig: queued(cfg),
      getEntities: queued(ent),
      getInterfaces: queued(ifs),
      getProxy: queued(px),
    })

    const first = loader.load()
    const second = loader.load()
    // 新一轮先成功
    cfg[1].resolve({ upstreams: { fuyao: {} } })
    ent[1].resolve({ source: 'db' } as never)
    ifs[1].resolve({ tools: [] })
    px[1].resolve({ coverage: [] } as never)
    await flush()
    // 旧一轮随后才失败 —— 必须被 generation 闸全部吞掉
    cfg[0].reject(new Error('stale-config'))
    ent[0].reject(new Error('stale-entities'))
    ifs[0].reject(new Error('stale-tools'))
    px[0].reject(new Error('stale-proxy'))
    await Promise.allSettled([first, second])
    await flush()

    expect(loader.config.value).toEqual({ upstreams: { fuyao: {} } })
    expect(loader.loadError.value).toBe('')
    expect(loader.entities.value).toEqual({ source: 'db' })
    expect(loader.interfaceTools.value).toEqual([])
    expect(loader.proxy.value).toEqual({ coverage: [] })
    expect(loader.proxyError.value).toBe('')
    expect(notify).not.toHaveBeenCalled()
  })

  it('旧 load 的成功不得覆盖新一轮失败的错误态 (首次加载无旧数据可保留)', async () => {
    const cfg = [deferred<Record<string, unknown>>(), deferred<Record<string, unknown>>()]
    const { loader } = setup({ getConfig: queued(cfg) })

    const first = loader.load()
    const second = loader.load()
    cfg[1].reject(new Error('fresh-fail'))
    await flush()
    expect(loader.loadError.value).toBe('fresh-fail')
    expect(loader.config.value).toBeNull()

    cfg[0].resolve({ upstreams: { stale: true } })
    await Promise.allSettled([first, second])
    await flush()
    expect(loader.loadError.value).toBe('fresh-fail')
    expect(loader.config.value).toBeNull()
  })

  it('旧 load 先结束不能清掉仍在跑的新一轮 loading 标志', async () => {
    const cfg = [deferred<Record<string, unknown>>(), deferred<Record<string, unknown>>()]
    const { loader } = setup({ getConfig: queued(cfg) })

    void loader.load(false)
    const second = loader.load(true)
    expect(loader.loading.value).toBe(true)
    expect(loader.manualLoading.value).toBe(true)

    cfg[0].resolve({})
    await flush()
    // 第一轮过期, 不得碰标志 —— 新一轮的按钮 loading 态保留
    expect(loader.loading.value).toBe(true)
    expect(loader.manualLoading.value).toBe(true)
    expect(loader.config.value).toBeNull() // 旧结果也不许落地

    cfg[1].resolve({})
    await second
    expect(loader.loading.value).toBe(false)
    expect(loader.manualLoading.value).toBe(false)
  })
})

describe('createConfigPageLoader — 失败保留上次可用数据', () => {
  it('瞬时失败时 config/entities/tools/proxy 全部保留, 只显示错误', async () => {
    const cfg = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const ent = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const ifs = [
      deferred<{ tools: unknown[] }>(),
      deferred<{ tools: unknown[] }>(),
    ]
    const px = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const { loader, notify } = setup({
      getConfig: queued(cfg),
      getEntities: queued(ent),
      getInterfaces: queued(ifs),
      getProxy: queued(px),
    })

    cfg[0].resolve({ upstreams: { mootdx2: {} } })
    ent[0].resolve({ source: 'db' } as never)
    ifs[0].resolve({
      tools: [
        {
          name: 'm_quote',
          description: '行情',
          dangerous: false,
          cache_ttl_key: null,
          params: [],
        },
      ],
    })
    px[0].resolve({ coverage: ['fuyao'] } as never)
    await loader.load()

    // 第二轮四路全挂
    cfg[1].reject(new Error('cfg-down'))
    ent[1].reject(new Error('ent-down'))
    ifs[1].reject(new Error('ifs-down'))
    px[1].reject(new Error('proxy-down'))
    await loader.load(true)

    expect(loader.config.value).toEqual({ upstreams: { mootdx2: {} } })
    expect(loader.entities.value).toEqual({ source: 'db' })
    expect(loader.interfaceTools.value).toHaveLength(1)
    expect(loader.proxy.value).toEqual({ coverage: ['fuyao'] })
    expect(loader.loadError.value).toBe('cfg-down')
    expect(loader.proxyError.value).toBe('proxy-down')
    expect(notify).toHaveBeenCalledTimes(2)
  })

  it('/api/interfaces 响应缺 tools (契约漂移) 不抛 unhandled rejection, 保留旧清单', async () => {
    const ifs = [
      deferred<{ tools: unknown[] }>(),
      deferred<{ tools: unknown[] }>(),
    ]
    const { loader } = setup({
      getInterfaces: queued(ifs),
    })
    ifs[0].resolve({
      tools: [
        {
          name: 'a',
          description: '',
          dangerous: false,
          cache_ttl_key: null,
          params: [],
        },
      ],
    })
    await loader.load()

    ifs[1].resolve({} as never) // 后端契约漂移: 无 tools 字段
    await expect(loader.load(true)).resolves.toBeUndefined()
    await flush()
    expect(loader.interfaceTools.value).toHaveLength(1)
  })
})

describe('createConfigPageLoader — 通道状态 (entitiesError / toolsLoaded)', () => {
  it('entities 失败: entitiesError 置位且 entities 保持 null; 重试成功后清空', async () => {
    const ent = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const { loader } = setup({ getEntities: queued(ent) })

    const first = loader.load()
    ent[0].reject(new Error('ent-down'))
    await first
    // 这是「请求失败」而非「yaml 逃生模式」—— 上层据此区分错误面与正常说明
    expect(loader.entitiesError.value).toBe('ent-down')
    expect(loader.entities.value).toBeNull()

    const retry = loader.loadEntities()
    ent[1].resolve({ source: 'db' } as never)
    await retry
    expect(loader.entitiesError.value).toBe('')
    expect(loader.entities.value).toEqual({ source: 'db' })
  })

  it('toolsLoaded 只在真的拿到清单时置位 (M1: 失败与契约漂移都不算就绪)', async () => {
    const ok = setup({ getInterfaces: () => Promise.resolve({ tools: [] }) })
    expect(ok.loader.toolsLoaded.value).toBe(false)
    await ok.loader.load()
    expect(ok.loader.toolsLoaded.value).toBe(true)
    expect(ok.loader.toolsError.value).toBe('')

    const bad = setup({ getInterfaces: () => Promise.reject(new Error('ifs-down')) })
    await bad.loader.load()
    expect(bad.loader.toolsLoaded.value).toBe(false)
    expect(bad.loader.toolsError.value).toBe('ifs-down')

    const drifted = setup({ getInterfaces: () => Promise.resolve({} as never) })
    await drifted.loader.load()
    expect(drifted.loader.toolsLoaded.value).toBe(false)
    expect(drifted.loader.toolsError.value).toContain('tools')
  })

  it('成功一轮后再失败: 保留 toolsLoaded 与旧清单, 只记错误', async () => {
    const ifs = [
      deferred<{ tools: unknown[] }>(),
      deferred<{ tools: unknown[] }>(),
    ]
    const { loader } = setup({ getInterfaces: queued(ifs) })
    ifs[0].resolve({ tools: [] })
    await loader.load()
    expect(loader.toolsLoaded.value).toBe(true)

    ifs[1].reject(new Error('ifs-down'))
    await loader.load(true)
    expect(loader.toolsLoaded.value).toBe(true)
    expect(loader.toolsError.value).toBe('ifs-down')
  })

  it('旧一轮 entities 失败不得污染新一轮的成功态', async () => {
    const ent = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const { loader } = setup({ getEntities: queued(ent) })

    const first = loader.load()
    const second = loader.load()
    ent[1].resolve({ source: 'db' } as never)
    await flush()
    ent[0].reject(new Error('stale-entities'))
    await Promise.allSettled([first, second])
    await flush()

    expect(loader.entities.value).toEqual({ source: 'db' })
    expect(loader.entitiesError.value).toBe('')
  })
})

describe('createConfigPageLoader — 池参数通道 (H2)', () => {
  it('load 落地快照并清空 poolError', async () => {
    const { loader } = setup({
      getPool: () => Promise.resolve({ mootdx2_reader_limit: 4 } as never),
    })
    await loader.load()
    expect(loader.pool.value).toEqual({ mootdx2_reader_limit: 4 })
    expect(loader.poolError.value).toBe('')
  })

  it('池失败只置 poolError: 不打 toast, 也不影响其余通道', async () => {
    const { loader, notify } = setup({
      getConfig: () => Promise.resolve({ upstreams: {} } as never),
      getPool: () => Promise.reject(new Error('pool-down')),
    })
    await expect(loader.load()).resolves.toBeUndefined()
    expect(loader.pool.value).toBeNull()
    expect(loader.poolError.value).toBe('pool-down')
    expect(loader.config.value).toEqual({ upstreams: {} })
    expect(loader.loadError.value).toBe('')
    expect(notify).not.toHaveBeenCalled()
  })

  it('瞬时失败保留上次可用快照 (面板据此继续可编辑)', async () => {
    const pl = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const { loader } = setup({ getPool: queued(pl) })
    pl[0].resolve({ mootdx2_reader_limit: 4 } as never)
    await loader.load()

    pl[1].reject(new Error('pool-down'))
    await loader.load(true)
    expect(loader.pool.value).toEqual({ mootdx2_reader_limit: 4 })
    expect(loader.poolError.value).toBe('pool-down')
  })

  it('过期一轮的池结果不得覆盖 loadPool 重试后的新数据', async () => {
    const pl = [
      deferred<Record<string, unknown>>(),
      deferred<Record<string, unknown>>(),
    ]
    const { loader } = setup({ getPool: queued(pl) })
    void loader.load() // 第一轮池挂起
    const retry = loader.loadPool() // 折叠区重试按钮
    pl[1].resolve({ mootdx2_reader_limit: 8 } as never)
    await retry
    pl[0].reject(new Error('stale-pool'))
    await pl[0].promise.catch(() => undefined)
    await flush()

    expect(loader.pool.value).toEqual({ mootdx2_reader_limit: 8 })
    expect(loader.poolError.value).toBe('')
  })
})
