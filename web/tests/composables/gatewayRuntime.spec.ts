import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import type { UpstreamMetricsResponse } from '@/api/upstream-metrics'
import type { GatewayStatus } from '@/api/status'
import type { RuntimeStats } from '@/api/runtime'
import type {
  CacheBreakdownResponse,
  CacheCleanupResponse,
  CacheClearResponse,
  CacheVacuumResponse,
  UpstreamL1ResetResponse,
} from '@/api/cache'

// 四个 API 模块整体 mock: 用例自行控制 fulfilled/rejected 与调用计数。
vi.mock('@/api/upstream-metrics', () => ({
  getUpstreamMetrics: vi.fn(),
}))
vi.mock('@/api/status', () => ({
  getStatus: vi.fn(),
}))
vi.mock('@/api/runtime', () => ({
  getRuntimeStats: vi.fn(),
}))
vi.mock('@/api/cache', () => ({
  getCacheToolBreakdown: vi.fn(),
  cleanupCache: vi.fn(),
  clearCache: vi.fn(),
  vacuumCache: vi.fn(),
  resetUpstreamL1: vi.fn(),
}))

import { getUpstreamMetrics } from '@/api/upstream-metrics'
import { getStatus } from '@/api/status'
import { getRuntimeStats } from '@/api/runtime'
import {
  cleanupCache,
  clearCache,
  getCacheToolBreakdown,
  resetUpstreamL1,
  vacuumCache,
} from '@/api/cache'
import {
  useGatewayRuntime,
  type RuntimeNotifier,
} from '@/composables/useGatewayRuntime'

const metricsResponse: UpstreamMetricsResponse = {
  upstream: 'mootdx2',
  timestamp: 1,
  metrics: {
    daily_l1: {
      enabled: true,
      hits: 5,
      misses: 5,
      hit_rate: 0.5,
      size: 3,
      maxsize: 128,
      ttl_seconds: 600,
      reader_semaphore_limit: 4,
    },
  },
}

const statusResponse = (pid = 111, statsPid: number | null = 111): GatewayStatus => ({
  timestamp: 1,
  pid,
  summary: { total: 0, online: 0, degraded: 0, offline: 0, configured: 0, disabled: 0 },
  upstreams: {},
  cache: {
    enabled: true,
    entries: 10,
    live_entries: 9,
    expired_entries: 1,
    null_expiry_entries: 0,
    nearest_expiry: 2000,
    oldest_created: 1000,
    db_size_mb: 0.5,
    wal_size_bytes: 0,
    freelist_count: 0,
    page_count: 10,
    page_size: 4096,
    hits: 8,
    misses: 2,
    hit_rate: 0.8,
    stats_updated_at: 1,
    stats_pid: statsPid,
    timestamp: 1,
  },
})

const runtimeResponse: RuntimeStats = {
  pid: 111,
  uptime_seconds: 42,
  active_requests: 0,
  tool_count: 207,
  calls_last_1m: 0,
  calls_last_5m: 0,
  timestamp: 1,
}

const breakdownResponse: CacheBreakdownResponse = {
  tools: [{ tool: 'tool_a', entries: 4, live: 3 }],
  total_tools: 1,
  timestamp: 1,
}

function makeNotifier(confirmResult = true): RuntimeNotifier & {
  success: ReturnType<typeof vi.fn>
  error: ReturnType<typeof vi.fn>
  confirm: ReturnType<typeof vi.fn>
} {
  return {
    success: vi.fn(),
    error: vi.fn(),
    confirm: vi.fn().mockResolvedValue(confirmResult),
  }
}

beforeEach(() => {
  vi.mocked(getUpstreamMetrics).mockResolvedValue(metricsResponse)
  vi.mocked(getStatus).mockResolvedValue(statusResponse())
  vi.mocked(getRuntimeStats).mockResolvedValue(runtimeResponse)
  vi.mocked(getCacheToolBreakdown).mockResolvedValue(breakdownResponse)
  vi.mocked(cleanupCache).mockResolvedValue({
    deleted: 3,
    timestamp: 1,
  } satisfies CacheCleanupResponse)
  vi.mocked(clearCache).mockResolvedValue({
    cleared: true,
    timestamp: 1,
  } satisfies CacheClearResponse)
  vi.mocked(vacuumCache).mockResolvedValue({
    db_size_mb: 0.4,
    wal_size_bytes: 0,
    timestamp: 1,
  } satisfies CacheVacuumResponse)
  vi.mocked(resetUpstreamL1).mockResolvedValue({
    upstream: 'mootdx2',
    result: { enabled: true, cleared: ['daily', 'top_board', 'unusual'] },
    timestamp: 1,
  } satisfies UpstreamL1ResetResponse)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('useGatewayRuntime — 三源独立结算', () => {
  test('metrics 源失败不影响状态/运行态, 错误落到 l1LoadError', async () => {
    vi.mocked(getUpstreamMetrics).mockRejectedValueOnce(new Error('boom'))
    const rt = useGatewayRuntime({ lifecycle: false })

    await rt.load()

    expect(rt.l1LoadError.value).toContain('boom')
    expect(rt.metrics.value).toBeNull()
    expect(rt.respCache.value?.live_entries).toBe(9)
    expect(rt.runtime.value?.tool_count).toBe(207)
    expect(rt.l1RateSeries.value.at(-1)).toBeNull()
    expect(rt.respRateSeries.value.at(-1)).toBe(0.8)
  })

  test('status 源失败: loadError + 时间戳复位, metrics/runtime 仍更新', async () => {
    vi.mocked(getStatus).mockRejectedValueOnce(new Error('down'))
    const rt = useGatewayRuntime({ lifecycle: false })

    await rt.load()

    expect(rt.respLoadError.value).toContain('down')
    expect(rt.loadError.value).toContain('down')
    expect(rt.respCache.value).toBeNull()
    expect(rt.metrics.value?.daily_l1?.size).toBe(3)
    expect(rt.runtime.value?.pid).toBe(111)
    expect(rt.refreshLabel.value).toContain('失败')
  })

  test('回归 (H2): runtime 源失败保留旧值, 但挂独立错误面并计入 loadError', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load()
    vi.mocked(getRuntimeStats).mockRejectedValueOnce(new Error('x'))

    await rt.load()

    // 冻结值仍可读 (不空表), 但必须能被界面感知
    expect(rt.runtime.value?.tool_count).toBe(207)
    expect(rt.runtimeLoadError.value).toContain('x')
    expect(rt.loadError.value).toContain('x')
  })

  test('runtime 恢复成功后错误面复位', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load()
    vi.mocked(getRuntimeStats).mockRejectedValueOnce(new Error('x'))
    await rt.load()
    expect(rt.runtimeLoadError.value).not.toBe('')

    await rt.load()

    expect(rt.runtimeLoadError.value).toBe('')
    expect(rt.loadError.value).toBe('')
  })
})

describe('useGatewayRuntime — 异进程 stats 抑制', () => {
  test('stats_pid 与网关 pid 不同: 命中率不进序列, foreign 标记为 true', async () => {
    vi.mocked(getStatus).mockResolvedValueOnce(statusResponse(111, 999))
    const rt = useGatewayRuntime({ lifecycle: false })

    await rt.load()

    expect(rt.statsFromForeignProcess.value).toBe(true)
    expect(rt.respCache.value?.hit_rate).toBe(0.8) // 原始值仍在, 由卡片抑制展示
    expect(rt.respRateSeries.value.at(-1)).toBeNull()
    // live_entries 直读 cache.db, 与进程无关, 不抑制
    expect(rt.liveEntriesSeries.value.at(-1)).toBe(9)
  })

  test('同进程: foreign 为 false, 命中率正常入序列', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })

    await rt.load()

    expect(rt.statsFromForeignProcess.value).toBe(false)
    expect(rt.respRateSeries.value.at(-1)).toBe(0.8)
  })
})

describe('useGatewayRuntime — 趋势采样环', () => {
  test('超过 60 点后截断, 始终保留最近 60 点', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })

    for (let i = 0; i < 65; i += 1) {
      await rt.load()
    }

    expect(rt.l1RateSeries.value).toHaveLength(60)
    expect(rt.respRateSeries.value).toHaveLength(60)
    expect(rt.liveEntriesSeries.value).toHaveLength(60)
    // 每点都是当前 mock 的值, 截断不改变内容
    expect(rt.l1RateSeries.value[0]).toBe(0.5)
  })

  test('快采在跑时主轮询跳过 metrics: L1 趋势回填当前值, 不留假缺口', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load() // 首轮: 正常拉到 metrics (hit_rate=0.5, size=3)
    expect(rt.l1RateSeries.value.at(-1)).toBe(0.5)
    expect(rt.l1SizeSeries.value.at(-1)).toBe(3)

    // 模拟管线标签激活: 快采接管 metrics 更新, 主轮询跳过 mootdx 源
    rt.fastPollActive.value = true
    await rt.loadComponentMetrics() // 快采刷新 metrics.value
    await rt.load() // 主轮询: metricsResult = { skipped: true }

    // 旧码此处为 null (Sparkline 断点); 现应回填当前已知值
    expect(rt.l1RateSeries.value.at(-1)).toBe(0.5)
    expect(rt.l1SizeSeries.value.at(-1)).toBe(3)
  })
})

describe('useGatewayRuntime — breakdown 降频与静默', () => {
  test('首屏必刷; 第 2-5 轮不刷; 第 6 轮再刷', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })

    await rt.load() // 第 1 轮: firstLoad
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(1)
    expect(rt.breakdownRows.value[0]?.tool).toBe('tool_a')
    expect(rt.breakdownTotalTools.value).toBe(1)

    for (let i = 0; i < 4; i += 1) {
      await rt.load() // 第 2-5 轮
    }
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(1)

    await rt.load() // 第 6 轮
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(2)
  })

  test('首屏后 breakdown 后台刷新不再触发表格遮罩', async () => {
    let resolveBreakdown: (value: CacheBreakdownResponse) => void = () => {}
    vi.mocked(getCacheToolBreakdown)
      .mockResolvedValueOnce(breakdownResponse) // 首屏
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveBreakdown = resolve
        }),
      )

    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load() // 第 1 轮 (load 会 await breakdown 完成)
    expect(rt.breakdownTableLoading.value).toBe(false)

    // 第 2-5 轮: 不刷 breakdown, 正常返回
    for (let i = 0; i < 4; i += 1) {
      await rt.load()
    }
    // 第 6 轮: load 会一直挂到 breakdown resolve, 故不 await
    const pendingLoad = rt.load()
    await vi.waitFor(() =>
      expect(getCacheToolBreakdown).toHaveBeenCalledTimes(2),
    )
    // breakdown 挂起期间也不应显示遮罩
    expect(rt.breakdownTableLoading.value).toBe(false)

    resolveBreakdown(breakdownResponse)
    await pendingLoad
    expect(rt.breakdownRows.value[0]?.tool).toBe('tool_a')
  })

  test('breakdown 请求失败保留旧行不空表', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load()
    vi.mocked(getCacheToolBreakdown)
      .mockRejectedValueOnce(new Error('no route'))

    await rt.load(true) // 手动 = 必刷
    expect(rt.breakdownRows.value[0]?.tool).toBe('tool_a')
  })
})

describe('useGatewayRuntime — 管理动作', () => {
  test('onCleanup 成功后强制 breakdown 刷新', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load()
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(1)

    await rt.onCleanup()

    expect(cleanupCache).toHaveBeenCalledTimes(1)
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(2)
  })

  test('onResetL1: 确认后调重置接口并刷新; 取消则不调', async () => {
    const notifier = makeNotifier(true)
    const rt = useGatewayRuntime({ lifecycle: false, notifier })

    await rt.onResetL1()

    expect(notifier.confirm).toHaveBeenCalledTimes(1)
    expect(resetUpstreamL1).toHaveBeenCalledWith('mootdx2')
    expect(notifier.success).toHaveBeenCalled()

    const cancelNotifier = makeNotifier(false)
    const cancelled = useGatewayRuntime({ lifecycle: false, notifier: cancelNotifier })
    await cancelled.onResetL1()
    expect(resetUpstreamL1).toHaveBeenCalledTimes(1) // 没有第二次
  })

  test('onVacuum 成功提示数据库大小', async () => {
    const notifier = makeNotifier(true)
    const rt = useGatewayRuntime({ lifecycle: false, notifier })

    await rt.onVacuum()

    expect(vacuumCache).toHaveBeenCalledTimes(1)
    expect(notifier.success).toHaveBeenCalledWith(expect.stringContaining('0.4'))
  })

  test('onClear 只清响应缓存趋势, 保留 L1 趋势, 并强制 breakdown 刷新', async () => {
    const notifier = makeNotifier(true)
    const rt = useGatewayRuntime({ lifecycle: false, notifier })
    await rt.load()
    await rt.load()
    expect(rt.respRateSeries.value).toHaveLength(2)
    expect(rt.l1RateSeries.value).toHaveLength(2)

    await rt.onClear()

    expect(clearCache).toHaveBeenCalledTimes(1)
    // onClear 不再清空整个 samples, 而是把 respRate/liveEntries 设为 null;
    // L1 趋势 (l1Rate/l1Size) 保留不变。load(false, true) 添加 1 个新采样点。
    // 总长度 = 2 (原有, respRate=null) + 1 (新) = 3
    expect(rt.respRateSeries.value).toHaveLength(3)
    // 前两个点的 respRate 被置为 null (响应缓存清空)
    expect(rt.respRateSeries.value[0]).toBeNull()
    expect(rt.respRateSeries.value[1]).toBeNull()
    // L1 趋势保留: 仍是 2 个原有非 null 点 + 1 个新点 = 3
    expect(rt.l1RateSeries.value).toHaveLength(3)
    expect(rt.l1RateSeries.value[0]).not.toBeNull()
    expect(rt.l1RateSeries.value[1]).not.toBeNull()
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(2)
  })

  test('重置接口失败时走 notifier.error 且 loading 复位', async () => {
    const notifier = makeNotifier(true)
    vi.mocked(resetUpstreamL1).mockRejectedValueOnce(new Error('reset fail'))
    const rt = useGatewayRuntime({ lifecycle: false, notifier })

    await rt.onResetL1()

    expect(notifier.error).toHaveBeenCalledWith(expect.stringContaining('reset fail'))
    expect(rt.resetL1Loading.value).toBe(false)
  })
})

describe('useGatewayRuntime — 回归 (M4): 快采哨兵不算「已加载」', () => {
  test('主源全败 + 组件源被快采跳过时, 下一轮真拉仍按首屏处理', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    // 停在「并发」标签: 三个组件源被换成 { skipped: true } 哨兵 (status=fulfilled)
    rt.fastPollActive.value = true
    vi.mocked(getStatus).mockRejectedValueOnce(new Error('down'))
    vi.mocked(getRuntimeStats).mockRejectedValueOnce(new Error('down'))
    await rt.load()

    // 旧码把哨兵当成真数据 → everLoaded 置真 → 恢复后 firstLoad=false,
    // 首屏遮罩再也不出现: 用户看到的是空表而非「正在加载」。
    rt.fastPollActive.value = false
    const recovery = rt.load()
    expect(rt.initialLoading.value).toBe(true)
    await recovery
    expect(rt.initialLoading.value).toBe(false)
  })

  test('哨兵轮询里只要 status 成功就算已加载, 不误留首屏遮罩', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    rt.fastPollActive.value = true
    vi.mocked(getRuntimeStats).mockRejectedValueOnce(new Error('down'))
    await rt.load()

    rt.fastPollActive.value = false
    const next = rt.load()
    expect(rt.initialLoading.value).toBe(false)
    await next
  })
})

describe('useGatewayRuntime — 回归 (M8): 快采失败必须可见', () => {
  test('快采轮询失败挂到与主轮询同一批 *LoadError, 恢复后清空', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    vi.mocked(getUpstreamMetrics)
      .mockRejectedValueOnce(new Error('l1 down')) // mootdx2
      .mockRejectedValueOnce(new Error('thsdk down')) // thsdk
      .mockRejectedValueOnce(new Error('tdx down')) // tdx_quant

    await rt.loadComponentMetrics()

    // 旧码快采失败既不挂错误面也不提示: 停在「并发」标签时三源全挂,
    // 条形图静默冻结在最后一次成功值上。
    expect(rt.l1LoadError.value).toContain('l1 down')
    expect(rt.thsdkLoadError.value).toContain('thsdk down')
    // tdx_quant 与主 load() 同策略: 非首屏关键源, 不挂告警
    expect(rt.loadError.value).toContain('l1 down')
    expect(rt.loadError.value).not.toContain('tdx down')
    // 保留旧值不闪空
    expect(rt.metrics.value).toBeNull()

    await rt.loadComponentMetrics()

    expect(rt.l1LoadError.value).toBe('')
    expect(rt.thsdkLoadError.value).toBe('')
    expect(rt.metrics.value?.daily_l1?.size).toBe(3)
  })
})

describe('useGatewayRuntime — 手动刷新排队/首屏锁存', () => {
  test('轮询在途时点击手动刷新: 不丢弃, 本轮结束后补一次真手动刷新', async () => {
    let resolveBreakdown: (value: CacheBreakdownResponse) => void = () => {}
    vi.mocked(getCacheToolBreakdown).mockReturnValueOnce(
      new Promise((resolve) => { resolveBreakdown = resolve }),
    )
    const rt = useGatewayRuntime({ lifecycle: false })

    const firstLoad = rt.load() // 首轮挂在 breakdown 上
    await vi.waitFor(() => expect(getCacheToolBreakdown).toHaveBeenCalledTimes(1))

    rt.load(true) // 在途时手动点击 → 排队
    expect(rt.manualLoading.value).toBe(true)

    resolveBreakdown(breakdownResponse)
    await firstLoad
    // 补刷已发起并完成 (第二次 breakdown), loading 复位
    await vi.waitFor(() => {
      expect(getCacheToolBreakdown).toHaveBeenCalledTimes(2)
      expect(rt.manualLoading.value).toBe(false)
    })
  })

  test('breakdown 首次成功后, 主源持续失败也不再每轮重拉', async () => {
    const rt = useGatewayRuntime({ lifecycle: false })
    await rt.load() // 首轮: 主源成功 + breakdown 成功
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(1)

    vi.mocked(getStatus).mockRejectedValue(new Error('down'))
    vi.mocked(getUpstreamMetrics).mockRejectedValue(new Error('boom'))
    await rt.load() // 主源全败, 但 breakdown 已锁存 → 不重拉
    await rt.load()
    expect(getCacheToolBreakdown).toHaveBeenCalledTimes(1)
  })
})
