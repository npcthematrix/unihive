<template>
  <div class="map-panel">
    <!-- 搜索框 -->
    <div class="cfg-search-wrap" :class="{ 'has-value': search }">
      <input
        v-model="search"
        type="text"
        class="cfg-search"
        placeholder="按路由键 / 上游 / 上游 tool 筛选…"
        aria-label="筛选工具映射"
      >
      <button
        v-if="search"
        type="button"
        class="cfg-search-clear"
        title="清空"
        @click="search = ''"
      >×</button>
    </div>

    <el-table :data="filteredEntries" :empty-text="emptyText" class="cfg-table map-table">
      <el-table-column label="路由键" min-width="200">
        <template #default="{ row }">
          <span class="tool-name">{{ row.toolName }}</span>
        </template>
      </el-table-column>
      <el-table-column label="调用链 (上游 → 上游 tool)">
        <template #default="{ row }">
          <div v-if="row.pills.length" class="chain-list">
            <template v-for="(p, idx) in row.pills" :key="idx">
              <span
                v-if="idx > 0"
                class="chain-arrow"
                aria-hidden="true"
              >→</span>
              <el-tooltip
                :content="`${p.upstream} :: ${p.toolCall}`"
                placement="top"
              >
                <span class="chain-pill" :class="{ primary: idx === 0 }">
                  {{ p.upstream }}
                  <span class="chain-sep">::</span>
                  {{ p.toolCall }}
                </span>
              </el-tooltip>
            </template>
          </div>
          <span v-else class="muted">无上游映射</span>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="search.trim()" class="map-count">
      显示 {{ filteredEntries.length }} / {{ totalEntries }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { GatewayConfig } from '@/api/config'

interface Props {
  config: GatewayConfig | null
}

const props = defineProps<Props>()

interface ChainPill {
  upstream: string
  toolCall: string
}

interface MappingRow {
  toolName: string
  pills: ChainPill[]
  searchKey: string
}

const search = ref('')

/** 排序 + 展开成 row 数组, 复用 chain pill 数据。 */
const entries = computed<MappingRow[]>(() => {
  const map = props.config?.upstream_tool_mapping || {}
  return Object.entries(map)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map<MappingRow>(([toolName, chain]) => {
      const items = Object.entries(chain || {})
      return {
        toolName,
        pills: items.map(([upstream, toolCall]) => ({ upstream, toolCall })),
        searchKey: (
          toolName + ' ' + items.map(([u, t]) => `${u} ${t}`).join(' ')
        ).toLowerCase(),
      }
    })
})

const totalEntries = computed(() => entries.value.length)

const filteredEntries = computed<MappingRow[]>(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter((r) => r.searchKey.includes(q))
})

const emptyText = computed(() => {
  if (!totalEntries.value) return '暂无工具映射'
  return '无匹配项'
})
</script>

<style scoped>
.map-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chain-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.chain-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.75rem;
  cursor: default;
}

.chain-pill.primary {
  background: var(--primary-soft, var(--el-color-primary-light-9));
  border-color: var(--primary, var(--el-color-primary));
  color: var(--primary, var(--el-color-primary));
}

.chain-sep {
  opacity: 0.55;
}

.chain-arrow {
  color: var(--text-secondary);
  font-size: 0.8rem;
  margin: 0 2px;
}

.map-count {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 4px;
}
</style>
