import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage } from '@/api/client'
import { getUpstreamMetrics } from '@/api/upstream-metrics'
import type { UpstreamMetrics, ThsdkMetrics } from '@/api/upstream-metrics'
import { getStatus, type CacheStats, type GatewayStatus } from '@/api/status'
import { getRuntimeStats, type RuntimeStats } from '@/api/runtime'
import {
  type CacheToolBreakdownRow,
  cleanupCache,
  clearCache,
  getCacheToolBreakdown,
  resetUpstreamL1,
  vacuumCache,
} from '@/api/cache'
import { POLL_INTERVAL_MS, STALE_THRESHOLD_SEC } from '@/constants/time'
import { formatRefreshLabel } from '@/utils/time'

/** breakdown 降频: 每 6 轮 10s 轮询刷一次 (≈60s)。 */
const BREAKDOWN_REFRESH_CYCLES = 6
/** 趋势采样环容量 (60 点 ≈ 最近 10 分钟)。 */
const MAX_SAMPLES = 60

export interface ConfirmOptions {
  message: string
  title: string
  confirmText: string
  type?: 'warning' | 'info'
  confirmClass?: string
}

/** 用户通知口: 默认走 Element Plus, 测试注入假实现。 */
export interface RuntimeNotifier {
  success(message: string): void
  error(message: string): void
  confirm(options: ConfirmOptions): Promise<boolean>
}

const defaultNotifier: RuntimeNotifier = {
  success: (message) => {
    ElMessage.success(message)
  },
  error: (message) => {
    ElMessage.error(message)
  },
  confirm: async (options) => {
    try {
      await ElMessageBox.confirm(options.message, options.title, {
        confirmButtonText: options.confirmText,
        cancelButtonText: '取消',
        type: options.type,
        confirmButtonClass: options.confirmClass,
      })
      return true
    } catch {
      return false // 用户取消
    }
  },
}

export interface UseGatewayRuntimeOptions {
  notifier?: RuntimeNotifier
  /**
   * false = 不挂生命周期 (单测): 不创建时钟/轮询, 返回本地时间戳兜底。
   * 默认 true, 走正式 useAutoRefresh。
   */
  lifecycle?: boolean
}

/**
 * 网关运行监控页的全部编排: 三源 (mootdx2 指标 / 网关状态 / 运行态)
 * allSettled 独立结算、趋势采样环、breakdown 降频、四个管理动作。
 * 从 .vue 抽出以便脱离 plugin-vue 直接单测。
 */
export function useGatewayRuntime(options: UseGatewayRuntimeOptions = {}) {
  const notify = options.notifier ?? defaultNotifier
  const withLifecycle = options.lifecycle ?? true

  const metrics = ref<UpstreamMetrics | null>(null)
  const gatewayStatus = ref<GatewayStatus | null>(null)
  const respCache = ref<CacheStats | null>(null)
  const runtime = ref<RuntimeStats | null>(null)
  const thsdkMetrics = ref<ThsdkMetrics | null>(null)
  const initialLoading = ref(false)
  const manualLoading = ref(false)
  const loadError = ref('')
  const l1LoadError = ref('')
  const respLoadError = ref('')
  const thsdkLoadError = ref('')

  // 时间戳/时钟: 默认挂生命周期 (1s 时钟 + 轮询 + visibility 重拉); 单测可走本地兜底。
  const autoRefresh = ref(true)
  const nowSec = ref(Math.floor(Date.now() / 1000))
  const lastFetchSec = ref(0)
  const ageSec = computed(() =>
    lastFetchSec.value ? Math.max(0, nowSec.value - lastFetchSec.value) : 0,
  )
  function markFetched(timestampSec?: number): void {
    lastFetchSec.value = timestampSec ?? Math.floor(Date.now() / 1000)
  }
  function resetFetched(): void {
    lastFetchSec.value = 0
  }

  let inFlight = false
  let pollCount = 0
  /** 手动刷新撞在途轮询时排队: 本轮结束后补一次真正的手动刷新。 */
  let pendingManual = false
  /** 任一主源成功过即锁存: 之前 firstLoad 只看两源是否为 null, 两源持续
   * 失败时每轮都算首屏 → 遮罩反复闪。 */
  let everLoaded = false
  /** breakdown 首次成功 (含空结果) 后锁存: 此前每轮重试, 成功后降频。
   * 否则主源持续失败时 breakdown 会跟着每轮空跑。 */
  let breakdownFirstSucceeded = false

  // 趋势采样环: 源失败对应字段为 null, Sparkline 留缺口。
  interface TrendSample {
    l1Rate: number | null
    l1Size: number | null
    respRate: number | null
    liveEntries: number | null
  }
  const samples = ref<TrendSample[]>([])
  const breakdownRows = ref<CacheToolBreakdownRow[]>([])
  const breakdownTotalTools = ref(0)
  const breakdownLoading = ref(false)
  const breakdownEverLoaded = ref(false)

  /** 遮罩只在首屏 (含首次失败) 显示; 之后静默刷新, 表格不再每轮闪烁。 */
  const breakdownTableLoading = computed(() =>
    breakdownLoading.value && !breakdownEverLoaded.value,
  )

  // 管理操作 loading
  const resetL1Loading = ref(false)
  const cleanupLoading = ref(false)
  const vacuumLoading = ref(false)
  const clearLoading = ref(false)

  const gatewayPid = ref(0)

  /** stats 文件来自异进程时, 响应卡命中率整体抑制 (唯一判定, 多处共用)。 */
  function foreignStats(cache: CacheStats | null, pid: number): boolean {
    return cache?.stats_pid != null && cache.stats_pid !== pid
  }

  const statsFromForeignProcess = computed(() =>
    foreignStats(respCache.value, gatewayPid.value),
  )

  // 序列不过滤 null: Sparkline 按全样本序号定位, null 即缺口
  function seriesOf(pick: (sample: TrendSample) => number | null): (number | null)[] {
    return samples.value.map(pick)
  }

  const l1RateSeries = computed(() => seriesOf((sample) => sample.l1Rate))
  const l1SizeSeries = computed(() => seriesOf((sample) => sample.l1Size))
  const respRateSeries = computed(() => seriesOf((sample) => sample.respRate))
  const liveEntriesSeries = computed(() => seriesOf((sample) => sample.liveEntries))

  const refreshLabel = computed(() =>
    formatRefreshLabel({
      loadError: loadError.value,
      lastFetchSec: lastFetchSec.value,
      ageSec: ageSec.value,
    }),
  )

  /** 管线标签的 2.5s 快采是否在跑: 是 → 全局 10s 轮询跳过 mootdx/thsdk
   *  metrics (快采已在更频繁地更新它们), 消除重复请求。 */
  const fastPollActive = ref(false)

  async function load(manual = false, refreshBreakdown = false): Promise<void> {
    if (inFlight) {
      if (manual) {
        // 不丢点击: 排队, 本轮结束后补刷; loading 持续到补刷完成。
        pendingManual = true
        manualLoading.value = true
      }
      return
    }
    inFlight = true
    pollCount += 1
    const firstLoad = !everLoaded
    if (firstLoad) initialLoading.value = true
    if (manual) manualLoading.value = true
    loadError.value = ''

    // 四源独立结算: 单源失败不拖垮其余卡片
    // 管线快采在跑时, 两个组件 metrics 跳过 (返回哨兵, 下方 handler 识别后不更新)。
    const skip = Promise.resolve({ skipped: true })
    const fetchComponents = !fastPollActive.value
    const [metricsResult, statusResult, runtimeResult, thsdkResult] = await Promise.allSettled([
      fetchComponents ? getUpstreamMetrics('mootdx2') : skip,
      getStatus(),
      getRuntimeStats(),
      fetchComponents ? getUpstreamMetrics('thsdk') : skip,
    ])

    const sample: TrendSample = {
      l1Rate: null,
      l1Size: null,
      respRate: null,
      liveEntries: null,
    }

    if (metricsResult.status === 'fulfilled' && !('skipped' in metricsResult.value)) {
      const m = metricsResult.value.metrics
      metrics.value = m
      l1LoadError.value = ''
      sample.l1Rate = m.daily_l1?.hit_rate ?? null
      sample.l1Size = m.daily_l1?.size ?? null
    } else if (metricsResult.status === 'rejected') {
      l1LoadError.value = errorMessage(metricsResult.reason, '无法加载 MooTDX2 指标')
    }

    if (thsdkResult.status === 'fulfilled' && !('skipped' in thsdkResult.value)) {
      thsdkMetrics.value = thsdkResult.value.metrics as unknown as ThsdkMetrics
      thsdkLoadError.value = ''
    } else if (thsdkResult.status === 'rejected') {
      thsdkMetrics.value = null
      thsdkLoadError.value = errorMessage(thsdkResult.reason, 'THSDK 状态不可用')
    }

    if (statusResult.status === 'fulfilled') {
      const status = statusResult.value
      gatewayStatus.value = status
      respCache.value = status.cache
      gatewayPid.value = status.pid ?? 0
      respLoadError.value = ''
      const c = status.cache
      // 异进程 stats 不进趋势, 避免旧比率污染当前序列
      sample.respRate =
        !foreignStats(c, status.pid ?? 0) && c.hit_rate != null
          ? c.hit_rate
          : null
      sample.liveEntries = c.live_entries ?? null
      markFetched()
    } else {
      respLoadError.value = errorMessage(statusResult.reason, '请检查网关是否运行')
      loadError.value = respLoadError.value
      resetFetched()
    }

    // runtime 源失败保留上一次数据, 不挂告警
    if (runtimeResult.status === 'fulfilled') {
      runtime.value = runtimeResult.value
    }

    // 任一主源成功 = 页面已拿到过真实数据, 锁存首屏
    if (
      metricsResult.status === 'fulfilled'
      || statusResult.status === 'fulfilled'
      || thsdkResult.status === 'fulfilled'
    ) {
      everLoaded = true
    }

    samples.value = [...samples.value, sample].slice(-MAX_SAMPLES)
    initialLoading.value = false

    // 本轮收尾: 有排队的手动点击则补刷 (loading 不复位), 否则结束。
    const finishCycle = (): void => {
      inFlight = false
      if (pendingManual) {
        pendingManual = false
        void load(true)
      } else {
        manualLoading.value = false
      }
    }

    // breakdown 降频: 首次成功前每轮重试 / 手动 / 管理动作后必刷,
    // 成功后每 6 轮一次。inFlight 覆盖全程, 防重叠。
    const shouldFetchBreakdown =
      !breakdownFirstSucceeded || manual || refreshBreakdown
      || pollCount % BREAKDOWN_REFRESH_CYCLES === 0
    if (shouldFetchBreakdown) {
      breakdownLoading.value = true
      try {
        const resp = await getCacheToolBreakdown()
        breakdownRows.value = resp.tools
        breakdownTotalTools.value = resp.total_tools
        breakdownFirstSucceeded = true
      } catch {
        // 旧网关无此路由/瞬时失败: 保留旧行不空表 (首屏时本就为空)
      } finally {
        breakdownLoading.value = false
        breakdownEverLoaded.value = true
        finishCycle()
      }
    } else {
      finishCycle()
    }
  }

  // 生命周期绑定必须在 load 定义之后 (onPoll 闭包), 但早于任何实际触发。
  // 走原 useAutoRefresh: 1s 时钟 + 轮询 + tab 可见时陈旧重拉; 单测时 lifecycle=false 不挂。
  let clockTimer: ReturnType<typeof setInterval> | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  function tickClock(): void {
    if (typeof document !== 'undefined' && document.hidden) return
    nowSec.value = Math.floor(Date.now() / 1000)
  }
  function onVisibility(): void {
    if (document.hidden) return
    nowSec.value = Math.floor(Date.now() / 1000)
    if (lastFetchSec.value && nowSec.value - lastFetchSec.value > STALE_THRESHOLD_SEC) {
      void load(false)
    }
  }
  watch(autoRefresh, (on) => {
    if (!on) {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
      return
    }
    pollTimer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      void load(false)
    }, POLL_INTERVAL_MS)
  })

  if (withLifecycle) {
    onMounted(() => {
      void load(false)
      clockTimer = setInterval(tickClock, 1000)
      if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', onVisibility)
      }
    })

    onBeforeUnmount(() => {
      if (clockTimer) {
        clearInterval(clockTimer)
        clockTimer = null
      }
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility)
      }
    })
  }

  /** 只刷新两个组件 metrics (管线标签快速采样用, 不拉 status/cache)。 */
  async function loadComponentMetrics(): Promise<void> {
    const [mootdxResult, thsdkResult] = await Promise.allSettled([
      getUpstreamMetrics('mootdx2'),
      getUpstreamMetrics('thsdk'),
    ])
    if (mootdxResult.status === 'fulfilled') {
      metrics.value = mootdxResult.value.metrics
    } else {
      metrics.value = null
    }
    if (thsdkResult.status === 'fulfilled') {
      thsdkMetrics.value = thsdkResult.value.metrics as unknown as ThsdkMetrics
    } else {
      thsdkMetrics.value = null
    }
  }

  async function onResetL1(): Promise<void> {
    const confirmed = await notify.confirm({
      message:
        '确认清空 mootdx2 全部 L1 内存缓存 (日K / 板块扫描 / 异动扫描)? 下次请求将重新从通达信拉取。',
      title: '重置 L1 缓存',
      confirmText: '重置',
    })
    if (!confirmed) return
    resetL1Loading.value = true
    try {
      await resetUpstreamL1('mootdx2')
      notify.success('L1 缓存已重置')
      await load()
    } catch (error) {
      notify.error(errorMessage(error, '重置失败'))
    } finally {
      resetL1Loading.value = false
    }
  }

  async function onCleanup(): Promise<void> {
    cleanupLoading.value = true
    try {
      const resp = await cleanupCache()
      notify.success(`已清理 ${resp.deleted} 条过期条目`)
      await load(false, true)
    } catch (error) {
      notify.error(errorMessage(error, '清理失败'))
    } finally {
      cleanupLoading.value = false
    }
  }

  async function onVacuum(): Promise<void> {
    const confirmed = await notify.confirm({
      message: 'VACUUM 会短暂锁定缓存数据库并重建文件, 确认整理?',
      title: '整理缓存数据库',
      confirmText: '整理',
    })
    if (!confirmed) return
    vacuumLoading.value = true
    try {
      const resp = await vacuumCache()
      notify.success(`整理完成, 数据库 ${resp.db_size_mb} MB`)
      await load()
    } catch (error) {
      notify.error(errorMessage(error, '整理失败'))
    } finally {
      vacuumLoading.value = false
    }
  }

  async function onClear(): Promise<void> {
    const confirmed = await notify.confirm({
      message:
        '将删除 logs/cache.db 中的全部缓存条目并归零命中率 (包括历史冻结数据), 操作不可恢复。确认清空?',
      title: '清空全部响应缓存',
      confirmText: '全部清空',
      type: 'warning',
      confirmClass: 'el-button--danger',
    })
    if (!confirmed) return
    clearLoading.value = true
    try {
      await clearCache()
      notify.success('响应缓存已清空')
      samples.value = []
      await load(false, true)
    } catch (error) {
      notify.error(errorMessage(error, '清空失败'))
    } finally {
      clearLoading.value = false
    }
  }

  return {
    // 状态
    metrics,
    gatewayStatus,
    respCache,
    runtime,
    thsdkMetrics,
    loadError,
    l1LoadError,
    respLoadError,
    thsdkLoadError,
    initialLoading,
    manualLoading,
    statsFromForeignProcess,
    // 时钟/自动刷新
    autoRefresh,
    nowSec,
    ageSec,
    refreshLabel,
    // 趋势序列
    l1RateSeries,
    l1SizeSeries,
    respRateSeries,
    liveEntriesSeries,
    // breakdown
    breakdownRows,
    breakdownTotalTools,
    breakdownTableLoading,
    // 管理 loading
    resetL1Loading,
    cleanupLoading,
    vacuumLoading,
    clearLoading,
    // 动作
    load,
    loadComponentMetrics,
    fastPollActive,
    onResetL1,
    onCleanup,
    onVacuum,
    onClear,
  }
}
