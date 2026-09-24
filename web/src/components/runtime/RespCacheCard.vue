<template>
  <!-- ============ 响应缓存 (网关级) ============ -->
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">
          响应缓存 <small class="sub">SQLite · logs/cache.db</small>
        </h2>
        <div class="mgmt-actions">
          <el-button size="small" :loading="cleanupLoading" @click="emit('cleanup')">
            清理过期
          </el-button>
          <el-button
            size="small"
            :loading="vacuumLoading"
            :disabled="!hasReclaimablePages"
            @click="emit('vacuum')"
          >
            整理 (VACUUM)
          </el-button>
          <el-button
            size="small"
            type="danger"
            plain
            class="danger-action"
            :disabled="(cache?.entries ?? 0) === 0"
            :loading="clearLoading"
            @click="emit('clear')"
          >
            清空全部
          </el-button>
        </div>
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
        :hits="hitsDisplay"
        :misses="missesDisplay"
        :hint="hitRateHint"
      />

      <MiniStat label="状态">
        <template #value>
          <span class="status-dot" :class="cache?.enabled ? 'status-online' : 'status-disabled'" />
          <span :class="cache?.enabled ? 'on' : 'off'">
            {{ cache?.enabled ? '启用' : '停用' }}
          </span>
        </template>
      </MiniStat>

      <MiniStat label="活跃条目" :value="liveEntriesText" hint="未过期" />
      <MiniStat label="过期条目" :value="expiredText" hint="待清理" />
      <MiniStat label="总条目" :value="entriesText" />
      <MiniStat label="数据库" :value="dbSizeText" :hint="`WAL ${walText}`" />
      <MiniStat label="死页" :value="freelistText" hint="VACUUM 可回收" />
    </div>

    <div class="trend-grid">
      <TrendRow
        label="命中率趋势"
        :points="rateSeries"
        color="var(--el-color-success)"
        :chart-aria-label="rateAria('响应缓存命中率', rateSeries)"
      />
      <TrendRow
        label="活跃条目"
        :points="entriesSeries"
        color="var(--el-color-primary)"
        :chart-aria-label="countAria('活跃条目', entriesSeries)"
      />
    </div>

    <CacheBreakdownTable
      :rows="breakdownRows"
      :loading="breakdownLoading"
      :total-entries="cache?.entries ?? null"
    />
    <p v-if="breakdownNote" class="trunc-note">{{ breakdownNote }}</p>

    <p v-if="cache && !cache.enabled" class="off-hint">
      {{ cache.error || '缓存数据库未创建' }}
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, toRef } from 'vue'
import type { CacheStats } from '@/api/status'
import type { CacheToolBreakdownRow } from '@/api/cache'
import { rateAria, countAria } from '@/utils/number'
import { useHitRate } from '@/composables/useHitRate'
import HitRateCard from '@/components/HitRateCard.vue'
import MiniStat from '@/components/MiniStat.vue'
import TrendRow from '@/components/TrendRow.vue'
import CacheBreakdownTable from '@/components/CacheBreakdownTable.vue'

/** 响应缓存卡: 纯展示, 管理动作经 emit 交回视图。 */
const props = defineProps<{
  cache: CacheStats | null
  /** stats 文件是否来自异进程: 是则命中率整体抑制。 */
  foreign: boolean
  initialLoading: boolean
  loadError: string
  rateSeries: (number | null)[]
  entriesSeries: (number | null)[]
  breakdownRows: CacheToolBreakdownRow[]
  breakdownLoading: boolean
  breakdownTotalTools: number
  cleanupLoading: boolean
  vacuumLoading: boolean
  clearLoading: boolean
}>()

const emit = defineEmits<{
  (e: 'cleanup'): void
  (e: 'vacuum'): void
  (e: 'clear'): void
}>()

const hr = useHitRate(toRef(props, 'cache'), {
  suppressed: computed(() => props.foreign),
})

const hitsDisplay = computed(() =>
  props.foreign ? '—' : (props.cache?.hits ?? '—'),
)
const missesDisplay = computed(() =>
  props.foreign ? '—' : (props.cache?.misses ?? '—'),
)

const hitRateHint = computed(() => {
  const c = props.cache
  if (!c?.enabled) return c?.error || '缓存数据库未创建'
  if (props.foreign) return '命中统计来自上一网关进程, 不代表当前进程'
  const total = (c.hits ?? 0) + (c.misses ?? 0)
  return total > 0
    ? `共 ${total} 次请求 · 同 key 并发只计一次`
    : '网关启动后累计 (尚未请求)'
})

function numOrDash(value: number | null | undefined): number | string {
  return value == null ? '—' : value
}

const liveEntriesText = computed(() =>
  props.cache?.enabled ? numOrDash(props.cache.live_entries) : '—',
)
const expiredText = computed(() =>
  props.cache?.enabled ? numOrDash(props.cache.expired_entries) : '—',
)
const entriesText = computed(() =>
  props.cache?.enabled ? numOrDash(props.cache.entries) : '—',
)
const dbSizeText = computed(() =>
  props.cache?.enabled ? `${(props.cache.db_size_mb || 0).toFixed(2)} MB` : '—',
)
const walText = computed(() => {
  const bytes = props.cache?.wal_size_bytes ?? 0
  return bytes >= 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${bytes} B`
})
const freelistText = computed(() =>
  props.cache?.enabled ? numOrDash(props.cache.freelist_count) : '—',
)

const hasReclaimablePages = computed(() =>
  Boolean(props.cache?.enabled) && (props.cache?.freelist_count ?? 0) > 0,
)

const breakdownNote = computed(() => {
  if (props.breakdownLoading || props.breakdownTotalTools === 0) return ''
  const shown = props.breakdownRows.length
  if (shown < props.breakdownTotalTools) {
    return `仅显示条目最多的 ${shown} 个工具 · 共 ${props.breakdownTotalTools} 个工具有缓存条目`
  }
  return `共 ${props.breakdownTotalTools} 个工具有缓存条目`
})

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

.mgmt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

/* 危险按钮与常规操作拉开层级, 折行时自动靠右 */
.danger-action {
  margin-left: auto;
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

/* .on/.off 着色必须用 :slotted: 父 scope 的 .mini-value 在 MiniStat 内部, 跨组件 scope 隔离让普通后代选择器失效 */
:slotted(.on) { color: var(--el-color-success); }
:slotted(.off) { color: var(--el-text-color-secondary); }

.trunc-note {
  margin: 8px 2px 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.off-hint {
  margin: 10px 2px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 不设 3 列断点: 8 个网格单位在 3 列下末尾必缺一格;
   4 列保持到 860px 直接降 2 列。 */
@media (max-width: 860px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .trend-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .stat-grid {
    grid-template-columns: 1fr;
  }
}
</style>
