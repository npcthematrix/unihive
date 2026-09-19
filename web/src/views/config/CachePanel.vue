<template>
  <div class="cache-panel">
    <div class="cfg-banner cache-banner">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span v-if="isDbMode">
        此处为当前生效的 <code>cache</code> 全局配置 — 存于配置库
        <code>data/config.db</code> 的 <code>config_section</code>
        (空库由出厂 YAML 模板 seed; 与"上游状态"标签页的运行时缓存是不同概念)。
        只读展示, <strong>修改后需重启网关</strong>生效;
        单工具 TTL 覆盖请到「FUYAO 缓存」标签页 (可热生效)。
      </span>
      <span v-else>
        网关以 <code>UNIHIVE_CONFIG_SOURCE=yaml</code> 逃生模式启动, 此处为
        出厂 YAML (<code>config/upstreams.yaml</code> /
        <code>config/config.yaml</code>) 中的 <code>cache</code> 配置,
        只读展示, 编辑文件后重启网关生效。
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
            <template #default="{ row }"><span class="num">{{ row.seconds }}</span></template>
          </el-table-column>
          <el-table-column label="人类可读">
            <template #default="{ row }">
              <span class="ttl-human">{{ humanizeDuration(row.seconds) }}</span>
            </template>
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
  /** 配置事实源: db (默认, 配置库) / yaml (逃生模式)。决定 banner 措辞。 */
  source?: 'db' | 'yaml'
}

const props = withDefaults(defineProps<Props>(), { source: 'db' })

const isDbMode = computed(() => props.source === 'db')

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
  background: var(--cfg-bg-soft);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.78rem;
}

/* 秒列与人类可读列都含数字, 等宽数字避免排序后上下行跳动 */
.cache-ttl-table :deep(.num),
.cache-ttl-table :deep(.ttl-human) {
  font-variant-numeric: tabular-nums;
}
</style>
