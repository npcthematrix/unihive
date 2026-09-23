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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { OmniSource, OmniSyncLog } from '@/api/omni'
import { getOmniStats, runOmniSync } from '@/api/omni'
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
  (e: 'poll'): void
}>()

const SYNC_TIMEOUT_MIN = 5 // 与后端 SYNC_TIMEOUT_SECONDS 一致

const source = ref<'all' | OmniSource>('all')
const boardType = ref<'all' | (typeof BOARD_TYPE_KEYS)[number]>('all')
const syncing = ref(false)
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

/** running 是否在 10 分钟内（与后端 STALE_RUNNING_MINUTES 同阈值）。 */
function isRecentRunning(log: OmniSyncLog | null | undefined): boolean {
  if (!log || log.status !== 'running' || !log.start_time) return false
  const started = new Date(log.start_time.replace(' ', 'T') + 'Z').getTime()
  return Number.isFinite(started) && Date.now() - started < 10 * 60 * 1000
}

// ===== 状态对齐轮询 =====
// 两种用途：(1) 用户点"中止"只是断 HTTP，后端持锁继续跑，靠轮询收敛 UI；
// (2) 回到页面时后端已有同步在跑（本面板没发过 POST），接管其进度。
const POLL_INTERVAL_MS = 4000
let pollTimer: ReturnType<typeof setInterval> | null = null
/** true = 当前 syncing 态由"接管既有 running"而来，没有在飞 POST。 */
let externalRun = false
/** 路由卸载导致的中止：静默，不 toast、不 emit。 */
const unmounting = ref(false)

async function pollOnce() {
  try {
    const d = await getOmniStats()
    emit('poll')
    const latest = d.recent_logs[0]
    if (!syncing.value) return
    if (latest && latest.status !== 'running') {
      finishExternal(latest.status === 'success', latest.message || '')
    }
  } catch {
    // 轮询失败静默（可能是网关重启中），下一轮再试
  }
}

function finishExternal(ok: boolean, message: string) {
  if (!externalRun) return // 有在飞 POST，收尾以 POST 响应为准
  stopPolling()
  syncing.value = false
  externalRun = false
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

watch(syncing, (v) => (v ? startPolling() : stopPolling()))

onMounted(async () => {
  // 进入面板时若后端已有近期 running（如从其他页面切回/网关重启后），接管
  try {
    const d = await getOmniStats()
    if (isRecentRunning(d.recent_logs[0])) {
      externalRun = true
      syncing.value = true
    }
  } catch {
    // 忽略：面板不因此阻塞用户操作
  }
})

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
  externalRun = false
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
    if (unmounting.value) return
    if (isCanceledError(e)) {
      ElMessage.warning('已中止同步')
    } else {
      ElMessage.error(`同步失败: ${errorMessage(e)}`)
    }
  } finally {
    syncing.value = false
    activeAbort = null
    // 卸载路径不触发 synced（父组件已在离场，emit 只会多发一次 stats 请求）
    if (!unmounting.value) emit('synced')
  }
}

function onCancel() {
  activeAbort?.abort()
}

defineExpose({
  /**
   * 由 OmniView 在卸载/切换时调用。silent=true（路由离开）：不 toast、
   * 不 emit synced；后端任务照跑，回来时由 onMounted 轮询接管。
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
