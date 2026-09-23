import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage } from '@/api/client'
import { getUpstreamMetrics } from '@/api/upstream-metrics'
import type { UpstreamMetrics } from '@/api/upstream-metrics'
import { getStatus, type CacheStats } from '@/api/status'
import { getRuntimeStats, type RuntimeStats } from '@/api/runtime'
import {
  type CacheToolBreakdownRow,
  cleanupCache,
  clearCache,
  getCacheToolBreakdown,
  resetUpstreamL1,
  vacuumCache,
} from '@/api/cache'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
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
  const respCache = ref<CacheStats | null>(null)
  const runtime = ref<RuntimeStats | null>(null)
  const initialLoading = ref(false)
  const manualLoading = ref(false)
  const loadError = ref('')
  const l1LoadError = ref('')
  const respLoadError = ref('')

  // 时间戳/时钟: 默认由 useAutoRefresh 提供; 无生命周期环境用本地兜底。
  let autoRefresh: Ref<boolean> = ref(false)
  let nowSec: Ref<number> = ref(Math.floor(Date.now() / 1000))
  let lastFetchSec: Ref<number> = ref(0)
  let ageSec: ComputedRef<number>
  let markFetched: () => void = () => {
    lastFetchSec.value = nowSec.value
  }
  let resetFetched: () => void = () => {
    lastFetchSec.value = 0
  }

  let inFlight = false
  let pollCount = 0

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

  async function load(manual = false, refreshBreakdown = false): Promise<void> {
    if (inFlight) {
      if (manual) manualLoading.value = true
      return
    }
    inFlight = true
    pollCount += 1
    const firstLoad = metrics.value === null && respCache.value === null
    if (firstLoad) initialLoading.value = true
    if (manual) manualLoading.value = true
    loadError.value = ''

    // 三源独立结算: 单源失败不拖垮其余卡片
    const [metricsResult, statusResult, runtimeResult] = await Promise.allSettled([
      getUpstreamMetrics('mootdx2'),
      getStatus(),
      getRuntimeStats(),
    ])

    const sample: TrendSample = {
      l1Rate: null,
      l1Size: null,
      respRate: null,
      liveEntries: null,
    }

    if (metricsResult.status === 'fulfilled') {
      const m = metricsResult.value.metrics
      metrics.value = m
      l1LoadError.value = ''
      sample.l1Rate = m.daily_l1?.hit_rate ?? null
      sample.l1Size = m.daily_l1?.size ?? null
    } else {
      l1LoadError.value = errorMessage(metricsResult.reason, '无法加载 MooTDX2 指标')
    }

    if (statusResult.status === 'fulfilled') {
      const status = statusResult.value
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

    samples.value = [...samples.value, sample].slice(-MAX_SAMPLES)
    initialLoading.value = false
    manualLoading.value = false

    // breakdown 降频: 首屏/手动/管理动作后必刷, 否则每 6 轮一次。
    // inFlight 覆盖 breakdown 全程, 防重叠。
    const shouldFetchBreakdown =
      firstLoad || manual || refreshBreakdown
      || pollCount % BREAKDOWN_REFRESH_CYCLES === 0
    if (shouldFetchBreakdown) {
      breakdownLoading.value = true
      try {
        const resp = await getCacheToolBreakdown()
        breakdownRows.value = resp.tools
        breakdownTotalTools.value = resp.total_tools
      } catch {
        // 旧网关无此路由/瞬时失败: 保留旧行不空表 (首屏时本就为空)
      } finally {
        breakdownLoading.value = false
        breakdownEverLoaded.value = true
        inFlight = false
      }
    } else {
      inFlight = false
    }
  }

  // 生命周期绑定必须在 load 定义之后 (onPoll 闭包), 但早于任何实际触发。
  if (withLifecycle) {
    const ar = useAutoRefresh({ onPoll: () => { void load(false) } })
    autoRefresh = ar.autoRefresh
    nowSec = ar.nowSec
    lastFetchSec = ar.lastFetchSec
    ageSec = ar.ageSec
    markFetched = ar.markFetched
    resetFetched = ar.resetFetched
  } else {
    ageSec = computed(() =>
      lastFetchSec.value
        ? Math.max(0, nowSec.value - lastFetchSec.value)
        : 0,
    )
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
    respCache,
    runtime,
    loadError,
    l1LoadError,
    respLoadError,
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
    onResetL1,
    onCleanup,
    onVacuum,
    onClear,
  }
}
