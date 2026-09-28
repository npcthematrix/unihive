<template>
  <!-- ============ MooTDX2 L1 (单上游内存缓存) ============ -->
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">
          MooTDX2 L1 缓存 <small class="sub">日K · 板块/异动 L1</small>
        </h2>
        <el-button size="small" :loading="resetLoading" @click="emit('reset')">
          重置 L1 缓存
        </el-button>
      </div>
    </template>

    <el-alert
      v-if="loadError"
      type="error"
      :title="loadError"
      show-icon
      :closable="false"
      class="card-alert"
    />

    <div v-loading="initialLoading" class="stat-grid">
      <HitRateCard
        :pct="hr.pct.value"
        :text="hr.text.value"
        :color="hr.color.value"
        :hits="dailyL1?.hits ?? '—'"
        :misses="dailyL1?.misses ?? '—'"
        :hint="hitRateHint"
      />

      <MiniStat label="状态">
        <template #value>
          <span class="status-dot" :class="statusDotClass" />
          <span :class="statusValueClass">{{ statusText }}</span>
        </template>
      </MiniStat>

      <MiniStat
        label="当前 size"
        :value="sizeText"
        :hint="sizeHint"
      />
      <MiniStat
        label="并发上限"
        :value="semaphoreText"
        hint="Reader 并发控制"
      />
      <MiniStat label="TTL" :value="ttlText" hint="到次日 9:00" />
      <MiniStat label="总请求数" :value="metrics?.total_requests ?? '—'" />
      <MiniStat label="扫描 L1 条目" :value="scanL1Size" hint="板块 + 异动" />
    </div>

    <div class="trend-grid">
      <TrendRow
        label="命中率趋势"
        :points="rateSeries"
        color="var(--el-color-primary)"
        :chart-aria-label="rateAria('L1 命中率', rateSeries)"
      />
      <TrendRow
        label="缓存 size"
        :points="sizeSeries"
        color="var(--el-color-warning)"
        :chart-aria-label="countAria('L1 缓存条目', sizeSeries)"
      />
    </div>

    <div class="l1-extra-grid">
      <L1CacheBlock
        title="板块扫描 L1"
        :sub="blockSub('stock_top_board', metrics?.top_board_l1)"
        :stats="metrics?.top_board_l1"
      />
      <L1CacheBlock
        title="异动扫描 L1"
        :sub="blockSub('stock_unusual', metrics?.unusual_l1)"
        :stats="metrics?.unusual_l1"
      />
    </div>

    <details class="constants-details">
      <summary>配置常量 · get_daily_local L1</summary>
      <div class="kv-grid">
        <div class="kv-key">缓存 key</div>
        <div class="kv-val"><code>(date, symbol, adjust, start_date, end_date)</code> — 仅 <code>adjust="none"</code> 写入</div>
        <div class="kv-key">value 形状</div>
        <div class="kv-val"><code>list[dict]</code> — get_daily_sync 原始 records</div>
        <div class="kv-key">过期策略</div>
        <div class="kv-val">当日有效，次日 9:00 自动过期，避免陈旧行情</div>
      </div>
    </details>

    <p v-if="metrics && !dailyL1?.enabled" class="off-hint">
      L1 缓存未启用 (cachetools 缺失或代码已禁用)
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, toRef } from 'vue'
import type { UpstreamMetrics, L1CacheStats } from '@/api/upstream-metrics'
import { formatTtl, rateAria, countAria } from '@/utils/number'
import { useHitRate } from '@/composables/useHitRate'
import HitRateCard from '@/components/HitRateCard.vue'
import MiniStat from '@/components/MiniStat.vue'
import TrendRow from '@/components/TrendRow.vue'
import L1CacheBlock from '@/components/L1CacheBlock.vue'

/** MooTDX2 L1 卡: 日K L1 + 板块/异动扫描 + 连接池; 重置经 emit。 */
const props = defineProps<{
  metrics: UpstreamMetrics | null
  initialLoading: boolean
  loadError: string
  rateSeries: (number | null)[]
  sizeSeries: (number | null)[]
  resetLoading: boolean
}>()

const emit = defineEmits<{
  (e: 'reset'): void
}>()

const dailyL1 = computed<L1CacheStats | null>(
  () => props.metrics?.daily_l1 ?? null,
)
const hr = useHitRate(toRef(dailyL1))

// M2: dailyL1 未加载 (null) = 「未知」, enabled===false 才是「停用」。
const statusText = computed(() => {
  const l1 = dailyL1.value
  if (l1 == null) return '未知'
  return l1.enabled ? '启用' : '停用'
})
const statusDotClass = computed(() => {
  const l1 = dailyL1.value
  if (l1 == null) return 'status-unknown'
  return l1.enabled ? 'status-online' : 'status-disabled'
})
const statusValueClass = computed(() => {
  const l1 = dailyL1.value
  if (l1 == null) return 'unknown'
  return l1.enabled ? 'on' : 'off'
})

const hitRateHint = computed(() => {
  const l1 = dailyL1.value
  if (!l1?.enabled) return l1 ? '缓存已停用' : '尚未拉到指标'
  const total = (l1.hits ?? 0) + (l1.misses ?? 0)
  return total > 0
    ? `共 ${total} 次请求 · 同 key 并发只计一次`
    : '网关启动后累计 (尚未请求)'
})

const sizeText = computed(() => {
  // 指标未加载/失败时不展示数字, 也不拿 2048 编造上限
  const size = dailyL1.value?.size
  return size == null ? '—' : String(size)
})

const sizeHint = computed(() => {
  const maxsize = dailyL1.value?.maxsize
  return maxsize != null ? `/ ${maxsize} 上限` : undefined
})

const semaphoreText = computed(() => {
  const l1 = dailyL1.value
  if (!l1) return '—'
  return l1.reader_semaphore_limit ?? '—'
})

const ttlText = computed(() => {
  const ttl = dailyL1.value?.ttl_seconds
  return ttl == null ? '—' : formatTtl(ttl)
})

const scanL1Size = computed<number | string>(() => {
  // M2: metrics 未加载 / 两扫描块 size 皆 null → '—', 不把缺失伪装成空 0。
  const m = props.metrics
  if (m == null) return '—'
  const top = m.top_board_l1?.size
  const unu = m.unusual_l1?.size
  if (top == null && unu == null) return '—'
  return (top ?? 0) + (unu ?? 0)
})

// 扫描 L1 块副标题: TTL 取实时指标, 不再硬编码 "10s 档"
function blockSub(prefix: string, stats?: L1CacheStats): string {
  const ttl = stats?.ttl_seconds
  return `${prefix} · ${ttl != null ? formatTtl(ttl) : '固定档'}`
}

</script>

<style scoped>
.panel-card {
  border-radius: 8px;
}

.card-alert {
  margin-bottom: 12px;
}

.card-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  margin-left: 6px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  min-height: 96px;
}

.trend-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

.l1-extra-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.status-online {
  background: var(--el-color-success);
  box-shadow: 0 0 0 3px var(--el-color-success-light-9);
}

.status-disabled {
  background: var(--el-text-color-placeholder);
}

/* M2: 未知 = 空心虚框, 区别于停用实心灰。 */
.status-unknown {
  background: transparent;
  border: 1px dashed var(--el-border-color);
}

/* .on/.off 着色必须用 :slotted: 父 scope 的 .mini-value 在 MiniStat 内部, 跨组件 scope 隔离让普通后代选择器失效 */
:slotted(.on) { color: var(--el-color-success); }
:slotted(.off) { color: var(--el-text-color-secondary); }
:slotted(.unknown) { color: var(--el-text-color-placeholder); }

/* 折叠的配置常量 */
.constants-details {
  margin-top: 12px;
}

.constants-details summary {
  cursor: pointer;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.constants-details summary:hover {
  color: var(--el-text-color-primary);
}

.off-hint {
  margin: 10px 2px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kv-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 10px 14px;
  margin-top: 10px;
}

.kv-key {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.kv-val {
  font-size: 13px;
}

/* 不设 3 列断点 (同响应卡: 8 单位在 3 列下末尾缺格)。 */
@media (max-width: 860px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .trend-grid,
  .l1-extra-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .stat-grid {
    grid-template-columns: 1fr;
  }
}
</style>
