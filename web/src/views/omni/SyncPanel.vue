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
        <div class="last-sync-main">
          <span class="last-sync-source">{{ displaySource(lastLog.source) }}</span>
          <span class="last-sync-type">{{ displayBoardType(lastLog.board_type) }}</span>
          <el-tag
            :type="statusTagType(lastLog.status)"
            size="small"
            effect="light"
            :aria-label="`同步状态: ${lastLog.status}`"
          >
            {{ statusIcon(lastLog.status) }}{{ lastLog.status }}
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
          <el-option
            v-for="key in BOARD_TYPE_KEYS"
            :key="key"
            :label="displayBoardType(key)"
            :value="key"
          />
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
      <SyncLogList :logs="recentLogs" :loading="loading" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { OmniSource, OmniSyncLog } from '@/api/omni'
import { runOmniSync } from '@/api/omni'
import { errorMessage, isCanceledError } from '@/api/client'
import {
  BOARD_TYPE_KEYS,
  displayBoardType,
  displaySource,
  formatDuration,
  formatTime,
  statusIcon,
  statusTagType,
} from './format'
import SyncLogList from './SyncLogList.vue'

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
const boardType = ref<'all' | (typeof BOARD_TYPE_KEYS)[number]>('all')
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
    if (isCanceledError(e)) {
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

/* 状态高亮块: 只保留浅色底, 不再套边框 (避免 card > section > card 三层嵌套) */
.last-sync-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
}

.last-sync-success {
  background: var(--el-color-success-light-9);
}

.last-sync-running {
  background: var(--el-color-warning-light-9);
}

.last-sync-failed {
  background: var(--el-color-danger-light-9);
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
  background: var(--el-fill-color-light);
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

</style>
