<!--
  缓存与存储 tab 顶部 summary: 两层缓存 (响应缓存 + MooTDX2 L1) 的关键指标,
  一眼可见健康度, 详细数据进 sub-tab 看。
  数据从 useGatewayRuntime() 的同一批 prop 直接派生 (不拉额外接口)。
-->
<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">
          两层缓存 · 总览
          <small class="sub">响应缓存 + MooTDX2 L1</small>
        </h2>
        <span class="card-meta">
          {{ respForeign ? '响应缓存命中率为上一进程残留' : '每 10s 自动刷新' }}
        </span>
      </div>
    </template>

    <div class="tile-grid">
      <!-- 响应缓存 tile -->
      <div class="cache-tile" data-layer="resp">
        <div class="tile-head">
          <span class="tile-title">响应缓存</span>
          <span class="tile-status">
            <!-- M4: 装饰点 aria-hidden, 屏读不读 "绿点"; 旁边文字给真实状态 -->
            <span class="status-dot" :class="respStatusDotClass" aria-hidden="true" />
            <span :class="respStatusValueClass">{{ respStatusText }}</span>
          </span>
          <span class="tile-sub">SQLite · logs/cache.db</span>
        </div>

        <div class="tile-stats">
          <div class="stat-cell">
            <div class="stat-label">活跃条目</div>
            <div class="stat-value">{{ respLiveText }}</div>
            <div v-if="!respForeign" class="stat-hint">总 {{ respEntriesText }} · 死页 {{ respFreelistText }}</div>
            <div v-else class="stat-hint">总 {{ respEntriesText }}</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">命中率</div>
            <!-- H1: 颜色 + 文字徽标双通道, 色盲/灰度仍可读 -->
            <div class="stat-value-row">
              <span class="stat-value" :class="respRateClass">{{ respRateText }}</span>
              <span
                v-if="respRateTierLabel"
                class="rate-tier"
                :class="respRateTierClass"
                :aria-label="`${respRateTierLabel} (${respRateText})`"
              >{{ respRateTierLabel }}</span>
            </div>
            <div v-if="respHitHint" class="stat-hint">{{ respHitHint }}</div>
            <div v-else class="stat-hint">尚无请求</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">工具覆盖</div>
            <div class="stat-value">{{ respToolCoverageText }}</div>
            <div class="stat-hint">有缓存条目的工具数</div>
          </div>
        </div>
      </div>

      <!-- MooTDX2 L1 tile -->
      <div class="cache-tile" data-layer="l1">
        <div class="tile-head">
          <span class="tile-title">MooTDX2 L1</span>
          <span class="tile-status">
            <!-- M4: 装饰点 aria-hidden -->
            <span class="status-dot" :class="l1StatusDotClass" aria-hidden="true" />
            <span :class="l1StatusValueClass">{{ l1StatusText }}</span>
          </span>
          <span class="tile-sub">进程内 · 日K / 板块扫描 / 异动扫描</span>
        </div>

        <div class="tile-stats">
          <div class="stat-cell">
            <div class="stat-label">当前 size</div>
            <div class="stat-value">{{ l1SizeText }}</div>
            <div class="stat-hint">{{ l1SizeHint }}</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">命中率</div>
            <!-- H1: 颜色 + 文字徽标双通道 -->
            <div class="stat-value-row">
              <span class="stat-value" :class="l1RateClass">{{ l1RateText }}</span>
              <span
                v-if="l1RateTierLabel"
                class="rate-tier"
                :class="l1RateTierClass"
                :aria-label="`${l1RateTierLabel} (${l1RateText})`"
              >{{ l1RateTierLabel }}</span>
            </div>
            <div v-if="l1HitHint" class="stat-hint">{{ l1HitHint }}</div>
            <div v-else class="stat-hint">尚无请求</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">扫描 L1</div>
            <div class="stat-value">{{ l1ScanSizeText }}</div>
            <div class="stat-hint">板块 + 异动条目合计</div>
          </div>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { CacheStats } from '@/api/status'
import type { UpstreamMetrics, L1CacheStats } from '@/api/upstream-metrics'
import { numOrDash } from '@/utils/number'
import { useHitRate } from '@/composables/useHitRate'

/**
 * 两层缓存 summary tile: 复用 useGatewayRuntime 的同一批 props 派生,
 * 不增加额外网络请求。GatewayMonitorView 始终挂着, 与 sub-tab 切换无关。
 */
const props = defineProps<{
  respCache: CacheStats | null
  /** stats 来自异进程时, 响应缓存命中率整体抑制。 */
  respForeign: boolean
  /** 工具有缓存条目的总数 (breakdown.total_tools)。 */
  breakdownTotalTools: number
  metrics: UpstreamMetrics | null
}>()

// ============ 响应缓存派生 ============

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
const respEntriesText = computed(() =>
  props.respCache?.enabled ? numOrDash(props.respCache.entries) : '—',
)
const respFreelistText = computed(() =>
  props.respCache?.enabled ? numOrDash(props.respCache.freelist_count) : '—',
)
const respToolCoverageText = computed(() => {
  if (!props.respCache?.enabled) return '—'
  return props.breakdownTotalTools > 0 ? String(props.breakdownTotalTools) : '0'
})

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
const respHitHint = computed(() => {
  const c = props.respCache
  if (!c?.enabled) return c?.error || '缓存数据库未创建'
  if (props.respForeign) return '上一进程残留'
  const total = (c.hits ?? 0) + (c.misses ?? 0)
  return total > 0 ? `共 ${total} 次请求` : ''
})

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
  const size = dailyL1.value?.size
  return size == null ? '—' : String(size)
})
const l1SizeHint = computed(() => {
  const maxsize = dailyL1.value?.maxsize
  return maxsize != null ? `上限 ${maxsize}` : '进程内, 重启清空'
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
const l1HitHint = computed(() => {
  const l1 = dailyL1.value
  if (!l1?.enabled) return l1 ? '缓存已停用' : '尚未拉到指标'
  const total = (l1.hits ?? 0) + (l1.misses ?? 0)
  return total > 0 ? `共 ${total} 次请求` : ''
})

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

.tile-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.cache-tile {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.tile-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.tile-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.tile-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
}

.tile-sub {
  margin-left: auto;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

/* .status-dot / .status-online / .status-disabled / .status-unknown 已上提全局 */

.tile-status .on { color: var(--el-color-success); font-weight: 600; }
.tile-status .off { color: var(--el-text-color-secondary); }
.tile-status .unknown { color: var(--el-text-color-placeholder); }

.tile-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.stat-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.stat-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.stat-value {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

.stat-value.rate-good { color: var(--el-color-success); }
.stat-value.rate-mid { color: var(--el-color-warning); }
.stat-value.rate-low { color: var(--el-color-danger); }

/* H1: stat-value + tier 徽标同行 (避免 tier 把大字挤换行) */
.stat-value-row {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.rate-tier {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid currentColor;
  line-height: 1.4;
}

.rate-tier.tier-good { color: var(--el-color-success); }
.rate-tier.tier-mid  { color: var(--el-color-warning); }
.rate-tier.tier-low  { color: var(--el-color-danger); }

.stat-hint {
  font-size: 11px;
  color: var(--el-text-color-regular);
}

/* M3: 768px 中间断点替代原 860px。
   原 860px 在平板横屏 (768-1024) 把 tile 降单列过早; 改为 768px 才降。 */
@media (max-width: 768px) {
  .tile-grid {
    grid-template-columns: 1fr;
  }

  .tile-sub {
    margin-left: 0;
  }
}

@media (max-width: 560px) {
  .tile-stats {
    grid-template-columns: 1fr;
  }
}
</style>