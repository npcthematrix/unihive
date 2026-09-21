<template>
  <el-table
    :data="rows"
    v-loading="loading"
    size="small"
    class="breakdown-table"
    empty-text="暂无缓存条目"
  >
    <el-table-column label="工具" min-width="220">
      <template #default="{ row }">
        <code class="tool-code">{{ row.tool }}</code>
      </template>
    </el-table-column>
    <el-table-column label="条目" width="90" align="right">
      <template #default="{ row }">
        <span class="num">{{ row.entries }}</span>
      </template>
    </el-table-column>
    <el-table-column label="活跃" width="90" align="right">
      <template #default="{ row }">
        <span class="num">{{ row.live }}</span>
      </template>
    </el-table-column>
    <el-table-column label="占条目总数" min-width="160">
      <template #default="{ row }">
        <div class="share-cell">
          <el-progress
            :percentage="sharePct(row)"
            :stroke-width="6"
            :show-text="false"
          />
          <span class="share-text">{{ sharePct(row).toFixed(1) }}%</span>
        </div>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { CacheToolBreakdownRow } from '@/api/cache'

/** 响应缓存按工具的条目分布表 (GET /api/cache/tool-breakdown)。 */
const props = defineProps<{
  rows: CacheToolBreakdownRow[]
  loading?: boolean
  /** 全库总条目 (含未进 Top N 的工具), 占比的正确分母; 缺省时退回行和。 */
  totalEntries?: number | null
}>()

const rowsTotal = computed(() =>
  props.rows.reduce((sum, row) => sum + row.entries, 0),
)

const totalEntries = computed(() =>
  props.totalEntries != null && props.totalEntries > 0
    ? props.totalEntries
    : rowsTotal.value,
)

function sharePct(row: CacheToolBreakdownRow): number {
  const total = totalEntries.value
  return total > 0 ? Number(((row.entries / total) * 100).toFixed(1)) : 0
}
</script>

<style scoped>
.breakdown-table {
  width: 100%;
}

.tool-code {
  font-size: 12px;
}

.share-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.share-cell :deep(.el-progress) {
  flex: 1;
}

.share-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  width: 44px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
