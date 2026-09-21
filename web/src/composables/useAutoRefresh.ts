import { computed, onBeforeUnmount, onMounted, ref, watch, type ComputedRef, type Ref } from 'vue'
import { POLL_INTERVAL_MS, STALE_THRESHOLD_SEC } from '@/constants/time'

/** 自动刷新 + 时钟同步 composable。
 *
 * 提供 autoRefresh 开关、nowSec 1s 时钟、轮询计时器、tab 可见时陈旧重拉。
 * 调用方负责提供 load 回调 (如何发请求/写 store 由调用方决定), 调用方在
 * load 成功后通过 markFetched() 通知 composable 更新 lastFetchSec, 失败时
 * 通过 resetFetched() 强制下次重拉 (与原 StatusView/L1CacheView 行为一致)。
 *
 * 同时处理: clockTimer (1s tick)、pollTimer (intervalMs)、visibilitychange
 * 监听、onBeforeUnmount 清理。
 */
export interface UseAutoRefreshOptions {
  /** 计时器触发的轮询回调 (autoRefresh=true 且 tab 可见时触发) */
  onPoll?: () => void | Promise<void>
  /** 轮询间隔 (ms), 默认 10000 */
  intervalMs?: number
  /** 数据陈旧阈值 (sec), 超过时 tab 切回会触发一次 onPoll, 默认 30 */
  staleThresholdSec?: number
}

export interface UseAutoRefreshReturn {
  /** 自动刷新开关 */
  autoRefresh: Ref<boolean>
  /** 当前时间戳 (sec), 1s 时钟更新 */
  nowSec: Ref<number>
  /** 上次成功 fetch 时间戳 (sec), 0 = 从未拉过 */
  lastFetchSec: Ref<number>
  /** 距上次 fetch 多少秒 (lastFetchSec=0 时为 0) */
  ageSec: ComputedRef<number>
  /** 通知 composable 数据已成功 fetch (默认 now) */
  markFetched: (timestampSec?: number) => void
  /** 通知 composable fetch 失败, 强制下次重拉 */
  resetFetched: () => void
}

export function useAutoRefresh(options: UseAutoRefreshOptions = {}): UseAutoRefreshReturn {
  const intervalMs = options.intervalMs ?? POLL_INTERVAL_MS
  const staleThresholdSec = options.staleThresholdSec ?? STALE_THRESHOLD_SEC
  const onPoll = options.onPoll

  const autoRefresh = ref(false)
  const nowSec = ref(Math.floor(Date.now() / 1000))
  const lastFetchSec = ref(0)

  let clockTimer: ReturnType<typeof setInterval> | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const ageSec = computed(() =>
    lastFetchSec.value ? Math.max(0, nowSec.value - lastFetchSec.value) : 0,
  )

  function markFetched(timestampSec?: number): void {
    lastFetchSec.value = timestampSec ?? Math.floor(Date.now() / 1000)
  }

  function resetFetched(): void {
    lastFetchSec.value = 0
  }

  function tickClock(): void {
    if (typeof document !== 'undefined' && document.hidden) return
    nowSec.value = Math.floor(Date.now() / 1000)
  }

  function onVisibility(): void {
    if (document.hidden) return
    nowSec.value = Math.floor(Date.now() / 1000)
    if (lastFetchSec.value && nowSec.value - lastFetchSec.value > staleThresholdSec) {
      void onPoll?.()
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
      void onPoll?.()
    }, intervalMs)
  })

  onMounted(() => {
    void onPoll?.()
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

  return { autoRefresh, nowSec, lastFetchSec, ageSec, markFetched, resetFetched }
}