<template>
  <div class="sync-panel">
    <!-- 上次同步概览 -->
    <section class="last-sync-section">
      <h3>上次同步</h3>
      <el-skeleton v-if="loading && !lastLog" :rows="2" animated />
      <el-empty
        v-else-if="!lastLog"
        description="暂无同步记录 — 下方点击&quot;立即同步&quot;开始"
        :image-size="60"
      />
      <div v-else class="last-sync-card" :class="lastSyncStatusClass">
        <div class="last-sync-label">上次同步</div>
        <div class="last-sync-main">
          <span class="last-sync-source">{{ displaySource(lastLog.source) }}</span>
          <span class="last-sync-type">{{ displayBoardType(lastLog.board_type) }}</span>
          <el-tag :type="statusTagType(lastLog.status)" size="small" effect="light">
            {{ lastLog.status }}
          </el-tag>
        </div>
        <div class="last-sync-meta">
          <span>{{ formatTime(lastLog.start_time) }}</span>
          <span v-if="durationText">耗时 <strong>{{ durationText }}</strong></span>
          <span class="last-sync-count">
            写入 <strong>{{ lastLog.record_count || 0 }}</strong> 条
          </span>
        </div>
        <div
          v-if="lastLog.status !== 'success' && lastLog.message"
          class="last-sync-message"
        >{{ lastLog.message }}</div>
      </div>
    </section>

    <!-- 同步控制区 -->
    <section class="sync-controls-section">
      <h3>同步控制</h3>
      <div class="sync-controls">
        <span class="ctrl-label">数据源:</span>
        <el-select v-model="source" :disabled="syncing" style="width: 160px">
          <el-option label="全部" value="all" />
          <el-option label="同花顺" value="fuyao" />
          <el-option label="TDX-QUANT" value="tqquant" />
        </el-select>
        <span class="ctrl-label">板块类型:</span>
        <el-select v-model="boardType" :disabled="syncing" style="width: 160px">
          <el-option label="全部" value="all" />
          <el-option label="行业板块" value="industry" />
          <el-option label="概念板块" value="concept" />
        </el-select>
        <el-button
          type="primary"
          :loading="syncing"
          @click="onSync"
        >{{ syncing ? '同步中...' : '立即同步' }}</el-button>
        <el-button
          v-if="syncing"
          type="default"
          @click="onCancel"
        >中止</el-button>
      </div>
      <div v-if="syncing" class="sync-hint">
        同步可能持续数十秒, 请勿关闭页面; 可点"中止"主动取消
        (后端任务会运行到 {{ SYNC_TIMEOUT_MIN }} 分钟超时)。
      </div>
    </section>

    <!-- 最近 N 次同步历史 -->
    <section class="history-section">
      <h3>最近同步</h3>
      <el-empty
        v-if="!recentLogs.length"
        description="暂无同步记录"
        :image-size="60"
      />
      <ul v-else class="log-list">
        <li
          v-for="l in recentLogs"
          :key="l.id ?? `${l.source}-${l.start_time}`"
          class="log-item"
        >
          <span class="log-time">{{ formatTime(l.start_time) }}</span>
          <span class="log-source">{{ displaySource(l.source) }}</span>
          <span class="log-type">{{ displayBoardType(l.board_type) }}</span>
          <el-tag :type="statusTagType(l.status)" size="small" effect="light">
            {{ l.status }}
          </el-tag>
          <span class="log-count">{{ l.record_count || 0 }} 条</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { OmniSource, OmniSyncLog } from '@/api/omni'
import { runOmniSync } from '@/api/omni'
import { errorMessage } from '@/api/client'
import {
  displayBoardType,
  displaySource,
  formatDuration,
  formatTime,
  statusTagType,
} from './format'

interface Props {
  recentLogs: OmniSyncLog[]
  loading: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'synced'): void
}>()

const SYNC_TIMEOUT_MIN = 5 // 与后端 SYNC_TIMEOUT_SECONDS 一致

const source = ref<'all' | OmniSource>('all')
const boardType = ref<'all' | 'industry' | 'concept'>('all')
const syncing = ref(false)
let activeAbort: AbortController | null = null

const lastLog = computed(() => props.recentLogs[0] || null)

const lastSyncStatusClass = computed(() => {
  const status = lastLog.value?.status
  if (status === 'success') return 'last-sync-success'
  if (status === 'running') return 'last-sync-running'
  return 'last-sync-failed'
})

const durationText = computed(() => {
  if (!lastLog.value) return null
  return formatDuration(lastLog.value.start_time, lastLog.value.end_time)
})

async function onSync() {
  if (syncing.value) {
    ElMessage.warning('同步已在进行中, 请等待完成')
    return
  }
  syncing.value = true
  activeAbort = new AbortController()
  try {
    const d = await runOmniSync(
      { source: source.value, board_type: boardType.value },
      activeAbort.signal,
    )
    if (d.status === 'success') {
      ElMessage.success(`同步成功: ${d.message || ''}`)
    } else {
      const msg = (d.message || '未知错误').substring(0, 200)
      ElMessage.error(`同步失败: ${msg}`)
    }
  } catch (e) {
    if ((e as { name?: string })?.name === 'CanceledError') {
      ElMessage.warning('已中止同步')
    } else {
      ElMessage.error(`同步失败: ${errorMessage(e)}`)
    }
  } finally {
    syncing.value = false
    activeAbort = null
    emit('synced')
  }
}

function onCancel() {
  activeAbort?.abort()
}

defineExpose({
  /** 由 OmniView 在卸载/切换时调用, 避免泄露 in-flight 请求。 */
  cancelInFlight: () => activeAbort?.abort(),
})
</script>

<style scoped>
.sync-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

h3 {
  margin: 0 0 8px;
  font-size: 0.92rem;
  font-weight: 600;
}

.last-sync-section,
.sync-controls-section,
.history-section {
  background: var(--card-bg);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 14px 16px;
}

.last-sync-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
}

.last-sync-success {
  background: #f0fdf4;
  border-color: #bbf7d0;
}

.last-sync-running {
  background: #fffbeb;
  border-color: #fde68a;
}

.last-sync-failed {
  background: #fef2f2;
  border-color: #fecaca;
}

.last-sync-label {
  font-size: 0.78rem;
  color: var(--el-text-color-secondary);
}

.last-sync-main {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.95rem;
}

.last-sync-source {
  font-weight: 600;
}

.last-sync-type {
  color: var(--el-text-color-secondary);
  font-size: 0.85rem;
}

.last-sync-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 0.8rem;
  color: var(--el-text-color-secondary);
}

.last-sync-count strong {
  color: var(--el-text-color-regular);
  font-variant-numeric: tabular-nums;
}

.last-sync-message {
  padding: 8px 10px;
  background: rgba(0, 0, 0, 0.04);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  white-space: pre-wrap;
  word-break: break-word;
}

.sync-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.ctrl-label {
  font-size: 0.82rem;
  color: var(--el-text-color-secondary);
}

.sync-hint {
  margin-top: 10px;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 0.78rem;
  color: var(--el-text-color-secondary);
}

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
  padding: 8px 10px;
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  font-size: 0.82rem;
}

.log-time {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
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
}
</style>
