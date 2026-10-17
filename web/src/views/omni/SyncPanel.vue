<template>
  <div class="sync-panel">
    <!-- 上次同步概览 -->
    <section class="last-sync-section">
      <h3 class="panel-section-title">上次同步</h3>
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
            :aria-label="`同步状态: ${displayStatus(lastLog.status)}`"
          >
            {{ statusIcon(lastLog.status) }}{{ displayStatus(lastLog.status) }}
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
      <h3 class="panel-section-title">同步控制</h3>
      <div class="sync-controls">
        <label class="ctrl-label" for="omni-sync-source">数据源:</label>
        <el-select
          id="omni-sync-source"
          v-model="source"
          class="omni-select"
          :disabled="syncing"
        >
          <el-option label="全部" value="all" />
          <el-option label="同花顺" value="fuyao" />
          <el-option label="TDX-QUANT" value="tqquant" />
        </el-select>
        <label class="ctrl-label" for="omni-sync-board">板块类型:</label>
        <el-select
          id="omni-sync-board"
          v-model="boardType"
          class="omni-select"
          :disabled="syncing"
        >
          <el-option label="全部" value="all" />
          <el-option
            v-for="key in availableBoardTypes"
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
          v-if="canCancel"
          type="default"
          @click="onCancel"
        >中止</el-button>
      </div>
      <div v-if="hint" class="sync-hint">{{ hint }}</div>
    </section>

    <!-- 最近 N 次同步历史 -->
    <section class="history-section">
      <h3 class="panel-section-title">最近同步</h3>
      <SyncLogList :logs="recentLogs" :loading="loading" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { OmniSource, OmniStatsResponse, OmniSyncLog } from '@/api/omni'
import { getOmniStats, runOmniSync } from '@/api/omni'
import { errorMessage, isCanceledError } from '@/api/client'
import {
  BOARD_TYPE_KEYS,
  displayBoardType,
  displaySource,
  displayStatus,
  formatDuration,
  formatTime,
  statusIcon,
  statusTagType,
} from './format'
import {
  canCancelSync,
  isRecentRunning,
  resolveSyncPhase,
  shouldPoll,
  syncHint,
} from './syncState'
import SyncLogList from './SyncLogList.vue'

interface Props {
  recentLogs: OmniSyncLog[]
  loading: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'synced'): void
  /** 携带 stats payload, 父组件直接消费, 避免轮询期间重复 GET /api/omni。 */
  (e: 'poll', payload: OmniStatsResponse): void
}>()

const SYNC_TIMEOUT_MIN = 5 // 与后端 SYNC_TIMEOUT_SECONDS 一致
const POLL_INTERVAL_MS = 4000
const POLL_INTERVAL_S = POLL_INTERVAL_MS / 1000

const source = ref<'all' | OmniSource>('all')
const boardType = ref<'all' | (typeof BOARD_TYPE_KEYS)[number]>('all')
const syncing = ref(false)
/** true = 当前 syncing 态没有本面板在飞的 POST (接管既有 running / 已中止等待)。 */
const externalRun = ref(false)
/** true = 用户点了「中止」: POST 已断, 但后端仍持锁, 交给轮询收敛。 */
const canceled = ref(false)
let activeAbort: AbortController | null = null

// 同花顺仅支持行业/概念；不提供的类型直接不下发，避免长等后只得到
// "No sectors synced" 这种含义不清的失败。
const availableBoardTypes = computed(() =>
  source.value === 'fuyao'
    ? BOARD_TYPE_KEYS.filter((k) => k === 'industry' || k === 'concept')
    : BOARD_TYPE_KEYS,
)
watch(availableBoardTypes, (types) => {
  if (boardType.value !== 'all' && !(types as readonly string[]).includes(boardType.value)) {
    boardType.value = 'all'
  }
})

const phase = computed(() =>
  resolveSyncPhase(syncing.value, externalRun.value, canceled.value),
)
const canCancel = computed(() => canCancelSync(phase.value))
const hint = computed(() => syncHint(phase.value, SYNC_TIMEOUT_MIN, POLL_INTERVAL_S))

// ===== 状态对齐轮询 =====
// 两种用途：(1) 用户点"中止"只是断 HTTP，后端持锁继续跑，靠轮询收敛 UI；
// (2) 回到页面时后端已有同步在跑（本面板没发过 POST），接管其进度。
let pollTimer: ReturnType<typeof setInterval> | null = null
/** 路由卸载导致的中止：静默，不 toast、不 emit。 */
const unmounting = ref(false)

async function pollOnce() {
  try {
    const d = await getOmniStats()
    // payload 直接交给父组件, 父组件不再重复请求 (原先一次轮询 2 个 GET)。
    emit('poll', d)
    if (!syncing.value) return
    const latest = d.recent_logs?.[0]
    if (!latest) return
    if (latest.status !== 'running') {
      finishExternal(latest.status === 'success', latest.message || '')
    } else if (!isRecentRunning(latest)) {
      // running 记录已超过 STALE_RUNNING_MINUTES → 任务僵死/失联, 不能无限等
      finishExternal(false, '同步任务已超时或失联, 请重试')
    }
  } catch {
    // 轮询失败静默（可能是网关重启中），下一轮再试
  }
}

function finishExternal(ok: boolean, message: string) {
  if (!externalRun.value) return // 有在飞 POST，收尾以 POST 响应为准
  stopPolling()
  syncing.value = false
  externalRun.value = false
  canceled.value = false
  if (ok) ElMessage.success(`同步成功: ${message}`)
  else ElMessage.error(`同步失败: ${(message || '').substring(0, 200)}`)
}

function startPolling() {
  if (pollTimer !== null) return
  pollTimer = setInterval(() => void pollOnce(), POLL_INTERVAL_MS)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// 只要不是 idle 就必须保持轮询 —— 中止等待态正是靠它把 UI 拉回一致。
watch(phase, (p) => (shouldPoll(p) ? startPolling() : stopPolling()))

/**
 * 接管判定改用父组件已加载的 recentLogs，不再自己 GET 一次。
 * v-show 会让本面板在页面首屏即挂载，原先的 onMounted 自取与父组件
 * onMounted 形成两个并发 /api/omni 请求。
 */
watch(
  () => props.recentLogs,
  (logs) => {
    if (syncing.value) return
    if (isRecentRunning(logs?.[0])) {
      externalRun.value = true
      syncing.value = true
    }
  },
  { immediate: true },
)

onBeforeUnmount(stopPolling)

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
  externalRun.value = false
  canceled.value = false
  activeAbort = new AbortController()
  try {
    const d = await runOmniSync(
      { source: source.value, board_type: boardType.value },
      activeAbort.signal,
    )
    // POST 已返回 → 本次同步已定论, 之前点过的「中止」不再有意义
    canceled.value = false
    if (d.status === 'success') {
      ElMessage.success(`同步成功: ${d.message || ''}`)
    } else {
      const msg = (d.message || '未知错误').substring(0, 200)
      ElMessage.error(`同步失败: ${msg}`)
    }
  } catch (e) {
    if (unmounting.value) return
    if (isCanceledError(e)) {
      if (!canceled.value) return // 切页/卸载竞态导致的静默中断
      ElMessage.warning('已停止等待；后端任务仍在收尾')
    } else {
      ElMessage.error(`同步失败: ${errorMessage(e)}`)
    }
  } finally {
    activeAbort = null
    if (unmounting.value) return
    if (canceled.value) {
      // 中止路径: 保持 syncing=true 与 externalRun=true, 由 4s 轮询收敛。
      // 这里不发 synced —— 同步尚未真正结束, 且 force 加载会点亮顶栏 loading。
      return
    }
    syncing.value = false
    emit('synced')
  }
}

function onCancel() {
  if (!canCancel.value) return
  canceled.value = true
  externalRun.value = true // 从此没有本面板在飞的 POST, 收尾以轮询为准
  activeAbort?.abort()
}

defineExpose({
  /**
   * 由 OmniView 在**卸载**时调用。silent=true（路由离开）：不 toast、
   * 不 emit synced；后端任务照跑，回来时由 recentLogs watch 重新接管。
   *
   * 注意: 切换子标签**不应**调用本方法 —— v-show 下本面板保持挂载,
   * 轮询继续运行才能让中止/接管态收敛。
   */
  cancelInFlight: (silent = false) => {
    if (silent) {
      unmounting.value = true
      stopPolling()
      activeAbort?.abort()
      activeAbort = null
      return
    }
    activeAbort?.abort()
  },
})
</script>

<style scoped>
.sync-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
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
  font-size: 15px;
}

.last-sync-source {
  font-weight: 600;
}

.last-sync-type {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.last-sync-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 13px;
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
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.sync-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.omni-select {
  width: 160px;
}

.ctrl-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.sync-hint {
  margin-top: 10px;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
