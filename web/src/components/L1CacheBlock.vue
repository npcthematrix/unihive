<template>
  <div class="l1-block" :class="{ disabled: !stats?.enabled }">
    <div class="l1-head">
      <span class="l1-title">{{ title }}</span>
      <span class="l1-sub">{{ sub }}</span>
    </div>
    <template v-if="stats?.enabled">
      <div class="l1-items">
        <div class="l1-item">
          <span class="l1-label">命中率</span>
          <span class="l1-value">{{ hitRateText }}</span>
        </div>
        <div class="l1-item">
          <span class="l1-label">条目</span>
          <span class="l1-value">{{ sizeText }}</span>
        </div>
        <div class="l1-item">
          <span class="l1-label">TTL</span>
          <span class="l1-value">{{ ttlText }}</span>
        </div>
      </div>
    </template>
    <p v-else class="l1-off">未启用</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { L1CacheStats } from '@/api/upstream-metrics'
import { formatPercent, formatTtl } from '@/utils/number'

/** mootdx2 单个 L1 内存缓存的紧凑展示块 (板块扫描 / 异动扫描等)。 */
const props = defineProps<{
  title: string
  sub?: string
  stats?: L1CacheStats | null
}>()

const hitRateText = computed(() => {
  const rate = props.stats?.hit_rate
  return rate == null ? '—' : formatPercent(rate)
})

const sizeText = computed(() => {
  const s = props.stats
  if (s?.size == null) return '—'
  return `${s.size}/${s.maxsize ?? '—'}`
})

const ttlText = computed(() => {
  const t = props.stats?.ttl_seconds
  return t == null ? '—' : formatTtl(t)
})
</script>

<style scoped>
.l1-block {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.l1-block.disabled {
  background: var(--el-fill-color-light);
}

.l1-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.l1-title {
  font-size: 13px;
  font-weight: 600;
}

.l1-sub {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.l1-items {
  display: flex;
  gap: 18px;
}

.l1-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.l1-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.l1-value {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.l1-off {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
</style>
