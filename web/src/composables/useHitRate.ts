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
    // M1: 与 CacheSummaryCard / Mootdx2L1Card 的 rate-good/mid/low 同阈值
    // (80/50), 三卡对同一份命中率展示同一档颜色。
    if (value >= 80) return 'var(--el-color-success)'
    if (value >= 50) return 'var(--el-color-warning)'
    return 'var(--el-color-danger)'
  })

  // H1: 命中率 tier (良好 / 一般 / 较低), 给色盲/灰度屏的非颜色 fallback。
  const tier = computed<'good' | 'mid' | 'low' | null>(() => {
    const value = pct.value
    if (value === null) return null
    if (value >= 80) return 'good'
    if (value >= 50) return 'mid'
    return 'low'
  })

  // H1: 徽标显示文字 (良好 / 一般 / 较低), 与 tier 同步; pct 为 null 不显示。
  const tierLabel = computed(() => {
    const t = tier.value
    if (t === 'good') return '良好'
    if (t === 'mid') return '一般'
    if (t === 'low') return '较低'
    return null
  })

  return { pct, text, color, tier, tierLabel }
}
