<template>
  <div class="stats-panel">
    <!-- 刷新失败但已有可用数据: 保留网格, 只在上方加一条提示 ——
         原先 v-else-if="loadError" 排在网格分支之前, 一次瞬时失败会把
         已展示的统计整块换成告警。 -->
    <el-alert
      v-if="loadError && stats.length"
      type="warning"
      :closable="false"
      show-icon
      :title="`刷新失败, 以下为上次结果: ${loadError}`"
    />

    <el-skeleton v-if="loading && !stats.length" :rows="3" animated />

    <div v-else-if="stats.length" class="stats-grid">
      <div v-for="s in stats" :key="`${s.source}-${s.board_type}`" class="stat-card">
        <div class="stat-label">
          {{ displaySource(s.source) }} / {{ displayBoardType(s.board_type) }}
        </div>
        <div class="stat-value stat-value--big">{{ s.sector_count }}</div>
        <div class="stat-unit">板块</div>
        <div class="stat-value">{{ s.total_stocks }}</div>
        <div class="stat-unit">股票</div>
      </div>
    </div>

    <el-alert
      v-else-if="loadError"
      type="warning"
      :closable="false"
      show-icon
      :title="loadError"
    >
      <template #default>
        <el-button size="small" @click="$emit('reload')">重试</el-button>
      </template>
    </el-alert>

    <el-empty v-else description="暂无数据, 请先同步" />
  </div>
</template>

<script setup lang="ts">
import type { OmniStatRow } from '@/api/omni'
import { displayBoardType, displaySource } from './format'

interface Props {
  stats: OmniStatRow[]
  loading: boolean
  loadError: string
}

defineProps<Props>()
defineEmits<{
  (e: 'reload'): void
}>()
</script>

<style scoped>
.stats-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.stat-card {
  padding: 14px 16px;
  background: var(--card-bg);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.stat-value--big {
  font-size: 28px;
  color: var(--el-color-primary);
}

.stat-unit {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

.stat-card .stat-value + .stat-unit {
  margin-bottom: 0;
}
</style>
