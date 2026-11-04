<template>
  <el-skeleton v-if="loading && !logs.length" :rows="2" animated />
  <el-empty v-else-if="!logs.length" description="暂无同步记录" :image-size="60" />
  <ul v-else class="log-list">
    <li
      v-for="l in logs"
      :key="l.id ?? `${l.source}-${l.start_time}`"
      class="log-item"
    >
      <span class="log-time">{{ formatTime(l.start_time) }}</span>
      <span class="log-source">{{ displaySource(l.source) }}</span>
      <span class="log-type">{{ displayBoardType(l.board_type) }}</span>
      <el-tag
        :type="statusTagType(l.status)"
        size="small"
        effect="light"
        :aria-label="`同步状态: ${displayStatus(l.status)}`"
      >
        {{ statusIcon(l.status) }}{{ displayStatus(l.status) }}
      </el-tag>
      <span class="log-count">{{ l.record_count || 0 }} 条</span>
      <span v-if="showDuration && durationText(l)" class="log-duration">{{ durationText(l) }}</span>
    </li>
  </ul>
</template>

<script setup lang="ts">
import type { OmniSyncLog } from '@/api/omni'
import {
  displayBoardType,
  displaySource,
  displayStatus,
  formatDuration,
  formatTime,
  statusIcon,
  statusTagType,
} from './format'

/** 最近同步记录列表 — SyncPanel 用 (StatsPanel 已聚焦统计数字, 不再展示日志)。 */
defineProps<{
  logs: OmniSyncLog[]
  loading?: boolean
  /** 是否展示耗时列; SyncPanel 开启, 轻量场景可关掉省一列。 */
  showDuration?: boolean
}>()

function durationText(l: OmniSyncLog): string | null {
  return formatDuration(l.start_time, l.end_time)
}
</script>

<style scoped>
.log-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.log-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--card-bg);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  font-size: 13px;
}

.log-time {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  min-width: 140px;
}

.log-source {
  font-weight: 500;
  min-width: 80px;
}

.log-type {
  color: var(--el-text-color-secondary);
  min-width: 90px;
}

.log-count {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-regular);
}

.log-duration {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  min-width: 50px;
  text-align: right;
}

/* ≤768px: 固定 min-width 的时间/源/类型列在窄屏会把行撑爆, 改为换行。
   与 QueryPanel 断点一致 (原 640 在 640-768 平板区间仍会溢出)。 */
@media (max-width: 768px) {
  .log-item {
    flex-wrap: wrap;
  }
  .log-time {
    min-width: 100px;
  }
  .log-source,
  .log-type {
    min-width: auto;
  }
}
</style>
