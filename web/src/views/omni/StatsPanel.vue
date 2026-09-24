<template>
  <div class="stats-panel">
    <el-skeleton v-if="loading && !stats.length" :rows="3" animated />

    <el-empty
      v-else-if="!stats.length && !loadError"
      description="暂无数据, 请先同步"
    />

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

    <div v-else class="stats-grid">
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

    <!-- 同步日志 -->
    <section class="sync-logs">
      <h3>同步日志</h3>
      <SyncLogList :logs="recentLogs" :loading="loading" />
    </section>
  </div>
</template>

<script setup lang="ts">
import type { OmniStatRow, OmniSyncLog } from '@/api/omni'
import { displayBoardType, displaySource } from './format'
import SyncLogList from './SyncLogList.vue'

interface Props {
  stats: OmniStatRow[]
  recentLogs: OmniSyncLog[]
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
  font-size: 0.78rem;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.stat-value {
  font-size: 1.4rem;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.stat-value--big {
  font-size: 1.8rem;
  color: var(--el-color-primary);
}

.stat-unit {
  font-size: 0.72rem;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

.stat-card .stat-value + .stat-unit {
  margin-bottom: 0;
}

.sync-logs h3 {
  margin: 4px 0 8px;
  font-size: 0.92rem;
  font-weight: 600;
}
</style>
