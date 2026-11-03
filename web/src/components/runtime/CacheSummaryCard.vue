<!--
  缓存 tab 顶部速览条: 两层缓存 (响应缓存 + MooTDX2 L1) 的健康度定位用,
  详细数值与管理动作在下方 sub-tab 的 RespCacheCard / Mootdx2L1Card 里。
  数据从 useGatewayRuntime() 的同一批 prop 直接派生 (不拉额外接口)。
-->
<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">
          两层缓存 · 速览
          <small class="sub">响应缓存 + MooTDX2 L1</small>
        </h2>
        <span class="card-meta">
          {{ cardMetaText }}
        </span>
      </div>
    </template>

    <!-- B4: 旧版每层三个 18px 大格 (活跃条目 / 命中率 / 工具覆盖), 与下方子标签里的
         RespCacheCard、Mootdx2L1Card 重复显示同几个数字; 宽屏下总览卡与详情卡同屏,
         页面上出现两个「命中率 43%」且无从判断哪个权威。改成每层一行的速览条, 字号
         降到 12-13px 明确从属, 并在末尾写明权威数值在子标签里。 -->
    <div class="strip-list">
      <div class="strip-row">
        <span class="strip-name">
          <!-- M4: 装饰点 aria-hidden, 屏读不读 "绿点"; 旁边文字给真实状态 -->
          <span class="status-dot" :class="respStatusDotClass" aria-hidden="true" />
          响应缓存
          <span class="strip-state" :class="respStatusValueClass">{{ respStatusText }}</span>
        </span>
        <span class="strip-sub">SQLite · logs/cache.db</span>
        <span class="strip-metrics">
          <span class="m">
            <span class="m-label">命中</span>
            <!-- H1: 颜色 + 文字徽标双通道, 色盲/灰度仍可读 -->
            <span class="m-value" :class="respRateClass">{{ respRateText }}</span>
            <span
              v-if="respRateTierLabel"
              class="rate-tier"
              :class="respRateTierClass"
              :aria-label="`${respRateTierLabel} (${respRateText})`"
            >{{ respRateTierLabel }}</span>
          </span>
          <span class="m">
            <span class="m-label">活跃</span>
            <span class="m-value">{{ respLiveText }}</span>
          </span>
          <span class="m" :title="respToolCoverageTitle">
            <span class="m-label">工具</span>
            <span class="m-value">{{ respToolCoverageText }}</span>
          </span>
        </span>
      </div>

      <div class="strip-row">
        <span class="strip-name">
          <span class="status-dot" :class="l1StatusDotClass" aria-hidden="true" />
          MooTDX2 L1
          <span class="strip-state" :class="l1StatusValueClass">{{ l1StatusText }}</span>
        </span>
        <span class="strip-sub">进程内 · 日K / 板块扫描 / 异动扫描</span>
        <span class="strip-metrics">
          <span class="m">
            <span class="m-label">命中</span>
            <span class="m-value" :class="l1RateClass">{{ l1RateText }}</span>
            <span
              v-if="l1RateTierLabel"
              class="rate-tier"
              :class="l1RateTierClass"
              :aria-label="`${l1RateTierLabel} (${l1RateText})`"
            >{{ l1RateTierLabel }}</span>
          </span>
          <span class="m" title="当前条目数 / 上限">
            <span class="m-label">size</span>
            <span class="m-value">{{ l1SizeText }}</span>
          </span>
          <span class="m" title="板块 + 异动条目合计">
            <span class="m-label">扫描</span>
            <span class="m-value">{{ l1ScanSizeText }}</span>
          </span>
        </span>
      </div>
    </div>

    <p class="strip-note">
      速览只用于定位是哪一层出了问题; 命中率、条目数、死页等权威数值与清理操作见下方子标签。
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { CacheStats } from '@/api/status'
import type { UpstreamMetrics, L1CacheStats } from '@/api/upstream-metrics'
import { numOrDash } from '@/utils/number'
import { useHitRate } from '@/composables/useHitRate'
import { POLL_INTERVAL_SEC } from '@/constants/time'

/**
 * 两层缓存速览条: 复用 useGatewayRuntime 的同一批 props 派生,
 * 不增加额外网络请求。GatewayMonitorView 始终挂着, 与 sub-tab 切换无关。
 */
const props = defineProps<{
  respCache: CacheStats | null
  /** stats 来自异进程时, 响应缓存命中率整体抑制。 */
  respForeign: boolean
  /** 工具有缓存条目的总数 (breakdown.total_tools)。 */
  breakdownTotalTools: number
  /** breakdown 拉取失败提示, '' = 无错误。见 respToolCoverageText (M9)。 */
  breakdownError: string
  metrics: UpstreamMetrics | null
  /** 自动刷新是否开启 (页头开关)。关闭时 card-meta 不显示轮询周期。 */
  autoRefresh: boolean
}>()

// ============ 响应缓存派生 ============

// card-meta: 三态 — 异进程残留提示 / 自动刷新 / 手动模式
const cardMetaText = computed(() => {
  if (props.respForeign) return '响应缓存命中率为上一进程残留'
  // L3: 周期文案由 POLL_INTERVAL_MS 派生, 改常量时这里不会静默漂移。
  return props.autoRefresh ? `每 ${POLL_INTERVAL_SEC}s 自动刷新` : '手动刷新模式'
})

const respStatusText = computed(() => {
  const c = props.respCache
  if (c == null) return '未知'
  return c.enabled ? '启用' : '停用'
})
const respStatusDotClass = computed(() => {
  const c = props.respCache
  if (c == null) return 'status-unknown'
  return c.enabled ? 'status-online' : 'status-disabled'
})
const respStatusValueClass = computed(() => {
  const c = props.respCache
  if (c == null) return 'unknown'
  return c.enabled ? 'on' : 'off'
})

const respLiveText = computed(() =>
  props.respCache?.enabled ? numOrDash(props.respCache.live_entries) : '—',
)
const respToolCoverageText = computed(() => {
  if (!props.respCache?.enabled) return '—'
  // M9: breakdown 失败时 total_tools 停在上一次的值 (首轮失败则为 0), 旧码把它
  // 渲染成「0 个工具有缓存」, 与 RespCacheCard 里那条「明细加载失败」告警互相
  // 矛盾 —— 读者会以为缓存真的空了。失败一律显示 "—"。
  if (props.breakdownError) return '—'
  return String(props.breakdownTotalTools)
})
const respToolCoverageTitle = computed(() =>
  props.breakdownError
    ? `明细未取到: ${props.breakdownError}`
    : '有缓存条目的工具数',
)

// 命中率: 单一来源 useHitRate (直读后端 hit_rate)。异进程 / 缓存停用 → 抑制成 "—"。
// 核对后端 console_api.py:815 与 cache.py:87: total>0 时 hit_rate = hits/(hits+misses),
// total=0 时返回 None —— 与卡片原先自行 hits/(hits+misses) 的算法逐位一致。故统一到
// useHitRate, 消除重复实现与未来漂移风险 (原注释称"hit_rate 四舍五入漂 1 位", 前提不成立)。
const respRate = useHitRate(
  computed(() => props.respCache),
  {
    suppressed: computed(() => props.respForeign || !props.respCache?.enabled),
  },
)
const respRateText = respRate.text
const respRateClass = computed(() =>
  respRate.tier.value ? `rate-${respRate.tier.value}` : '',
)
const respRateTierLabel = respRate.tierLabel
const respRateTierClass = computed(() =>
  respRate.tier.value ? `tier-${respRate.tier.value}` : '',
)

// ============ MooTDX2 L1 派生 ============
const dailyL1 = computed<L1CacheStats | null>(
  () => props.metrics?.daily_l1 ?? null,
)

const l1StatusText = computed(() => {
  const l1 = dailyL1.value
  if (l1 == null) return '未知'
  return l1.enabled ? '启用' : '停用'
})
const l1StatusDotClass = computed(() => {
  const l1 = dailyL1.value
  if (l1 == null) return 'status-unknown'
  return l1.enabled ? 'status-online' : 'status-disabled'
})
const l1StatusValueClass = computed(() => {
  const l1 = dailyL1.value
  if (l1 == null) return 'unknown'
  return l1.enabled ? 'on' : 'off'
})

const l1SizeText = computed(() => {
  const l1 = dailyL1.value
  if (l1?.size == null) return '—'
  return l1.maxsize != null ? `${l1.size}/${l1.maxsize}` : String(l1.size)
})

// 命中率: 同响应缓存, 复用 useHitRate (单一来源); 停用 / 无指标 → 抑制成 "—"。
const l1Rate = useHitRate(
  computed(() => props.metrics?.daily_l1 ?? null),
  {
    suppressed: computed(() => !props.metrics?.daily_l1?.enabled),
  },
)
const l1RateText = l1Rate.text
const l1RateClass = computed(() =>
  l1Rate.tier.value ? `rate-${l1Rate.tier.value}` : '',
)
const l1RateTierLabel = l1Rate.tierLabel
const l1RateTierClass = computed(() =>
  l1Rate.tier.value ? `tier-${l1Rate.tier.value}` : '',
)

const l1ScanSizeText = computed<string>(() => {
  const m = props.metrics
  if (m == null) return '—'
  const top = m.top_board_l1?.size
  const unu = m.unusual_l1?.size
  if (top == null && unu == null) return '—'
  return String((top ?? 0) + (unu ?? 0))
})
</script>

<style scoped>
/* .panel-card 圆角 / .card-toolbar / .card-title / .sub / .status-dot 系列
   已收敛到 styles/main.css (全局, 6 个 runtime 组件共用)。 */

.card-meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.strip-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.strip-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 14px;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.strip-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.strip-state {
  font-size: 12px;
  font-weight: 400;
}

.strip-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.strip-metrics {
  margin-left: auto;
  display: inline-flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.m {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  font-size: 12px;
}

.m-label {
  color: var(--el-text-color-secondary);
}

.m-value {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

.m-value.rate-good { color: var(--el-color-success); }
.m-value.rate-mid { color: var(--el-color-warning); }
.m-value.rate-low { color: var(--el-color-danger); }

.strip-state.on { color: var(--el-color-success); font-weight: 600; }
.strip-state.off { color: var(--el-text-color-secondary); }
.strip-state.unknown { color: var(--el-text-color-placeholder); }

.rate-tier {
  font-size: 11px;
  font-weight: 600;
  padding: 0 6px;
  border-radius: 999px;
  border: 1px solid currentColor;
  line-height: 1.4;
}

.rate-tier.tier-good { color: var(--el-color-success); }
.rate-tier.tier-mid  { color: var(--el-color-warning); }
.rate-tier.tier-low  { color: var(--el-color-danger); }

.strip-note {
  margin: 10px 2px 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

@media (max-width: 768px) {
  .strip-metrics {
    margin-left: 0;
    width: 100%;
  }
}
</style>
