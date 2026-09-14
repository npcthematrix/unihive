<template>
  <div class="cache-panel">
    <div class="cfg-banner cache-banner">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        此处显示的是 <code>config/config.yaml</code> 里的 <code>cache</code> 配置
        （与"上游状态"标签页的运行时缓存是不同概念）。
        <strong>修改后需重启网关</strong>生效。
      </span>
    </div>

    <!-- 基本配置 -->
    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          缓存基本配置
        </span>
      </summary>
      <div class="cfg-section-body">
        <div class="cfg-kv-grid">
          <div class="cfg-kv-key">启用</div>
          <div class="cfg-kv-val">
            <span
              class="cfg-switch"
              :class="enabled ? 'on' : 'off'"
            >{{ enabled ? '启用' : '停用' }}</span>
          </div>
          <div class="cfg-kv-key">DB 路径</div>
          <div class="cfg-kv-val">
            <code v-if="dbPath">{{ dbPath }}</code>
            <span v-else class="muted">-</span>
          </div>
          <div class="cfg-kv-key">TTL 配置</div>
          <div class="cfg-kv-val">
            <span class="num">{{ ttlEntries.length }}</span> 条 ·
            最短 <strong>{{ ttlMinText }}</strong> ·
            最长 <strong>{{ ttlMaxText }}</strong>
          </div>
        </div>
      </div>
    </details>

    <!-- TTL 配置 -->
    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          {{ ttlEntries.length }} 条 TTL 配置
          <span class="cfg-section-sub">— 按 TTL 升序排列</span>
        </span>
      </summary>
      <div class="cfg-section-body">
        <el-table :data="ttlEntries" class="cfg-table cache-ttl-table" :empty-text="ttlEmptyText">
          <el-table-column label="键" min-width="200">
            <template #default="{ row }">
              <span class="tool-name">{{ row.key }}</span>
            </template>
          </el-table-column>
          <el-table-column label="秒" width="100" align="right" header-align="right">
            <template #default="{ row }">{{ row.seconds }}</template>
          </el-table-column>
          <el-table-column label="人类可读">
            <template #default="{ row }">{{ humanizeDuration(row.seconds) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { GatewayConfig } from '@/api/config'
import { humanizeDuration } from './format'

interface Props {
  config: GatewayConfig | null
}

const props = defineProps<Props>()

const cache = computed(() => props.config?.cache || {})

const enabled = computed(() => Boolean(cache.value.enabled))
const dbPath = computed(() => (cache.value.db_path as string | undefined) || '')

interface TtlRow {
  key: string
  seconds: number
}

const ttlEntries = computed<TtlRow[]>(() => {
  const ttl = cache.value.ttl || {}
  return Object.entries(ttl)
    .sort((a, b) => Number(a[1]) - Number(b[1]))
    .map(([key, seconds]) => ({ key, seconds: Number(seconds) }))
})

const ttlMinText = computed(() => {
  if (!ttlEntries.value.length) return '-'
  return humanizeDuration(ttlEntries.value[0].seconds)
})

const ttlMaxText = computed(() => {
  if (!ttlEntries.value.length) return '-'
  return humanizeDuration(ttlEntries.value[ttlEntries.value.length - 1].seconds)
})

const ttlEmptyText = computed(() => (ttlEntries.value.length ? '' : '无 TTL 配置'))
</script>

<style scoped>
.cache-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cache-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 0;
}

.cache-banner code {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.78rem;
}

.cache-ttl-table :deep(.num) {
  font-variant-numeric: tabular-nums;
}
</style>
