import { computed, type Ref } from 'vue'
import { formatPercent } from '@/utils/number'

interface HitRateSource {
  hit_rate?: number | null
}

/**
 * 命中率卡片的共享派生 (响应缓存 / MooTDX2 L1 两处结构同构)。
 *
 * suppressed: 数据被抑制时比率整体不展示 —— 响应缓存的 stats 文件来自
 * 上一网关进程时传 true, 避免旧比率冒充当前进程。
 */
export function useHitRate(
  source: Ref<HitRateSource | null | undefined>,
  options: { suppressed?: Ref<boolean> } = {},
) {
  const isSuppressed = computed(() => options.suppressed?.value ?? false)

  const pct = computed(() => {
    if (isSuppressed.value) return null
    const rate = source.value?.hit_rate
    return rate == null ? null : Number((rate * 100).toFixed(1))
  })

  const text = computed(() => {
    if (isSuppressed.value) return '—'
    const rate = source.value?.hit_rate
    return rate == null ? '—' : formatPercent(rate)
  })

  const color = computed(() => {
    const value = pct.value
    if (value === null) return undefined
    if (value > 70) return 'var(--el-color-success)'
    if (value > 40) return 'var(--el-color-warning)'
    return 'var(--el-color-danger)'
  })

  return { pct, text, color }
}
