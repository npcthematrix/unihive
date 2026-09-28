<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">上游数据PROVIDERs</h2>
        <div class="toolbar-actions">
          <el-button
            :icon="Refresh"
            :loading="loading"
            size="small"
            @click="emit('refresh')"
          >刷新</el-button>
        </div>
      </div>
    </template>

    <!-- 汇总计数 -->
    <div v-loading="loading && !rows.length" class="summary-grid">
      <div v-for="card in summaryCards" :key="card.key" class="stat-card">
        <span class="stat-num" :class="card.cls">{{ summary?.[card.key] ?? '—' }}</span>
        <span class="stat-label">{{ card.label }}</span>
      </div>
    </div>

    <!-- 过滤 -->
    <el-radio-group v-model="filter" size="small" class="filter-chips">
      <el-radio-button value="all">
        全部 <span class="chip-count">{{ rows.length }}</span>
      </el-radio-button>
      <el-radio-button value="healthy">
        正常 <span class="chip-count">{{ healthyCount }}</span>
      </el-radio-button>
      <el-radio-button value="unhealthy">
        异常 <span class="chip-count">{{ rows.length - healthyCount }}</span>
      </el-radio-button>
    </el-radio-group>

    <!-- 上游表 -->
    <el-table
      :data="sortedRows"
      class="ups-table"
      size="small"
      empty-text="暂无匹配的上游"
      @sort-change="onSortChange"
    >
      <el-table-column
        prop="name"
        label="上游"
        sortable="custom"
        :sort-orders="['ascending', 'descending']"
        min-width="180"
      >
        <template #default="{ row }">
          <span class="ups-name">{{ displayName(row.name) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="120">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" round>{{ typeText(row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="status"
        label="状态"
        sortable="custom"
        :sort-orders="['ascending', 'descending']"
        width="110"
      >
        <template #default="{ row }">
          <span class="status-badge" :class="['b-' + row.status, { muted: row.status === 'disabled' }]">
            <span class="status-icon" :class="`icon-${row.status}`">
              {{ statusIcon(row.status) }}
            </span>
            {{ STATUS_TEXT[row.status] ?? '未知' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column
        prop="latency"
        label="延迟"
        sortable="custom"
        :sort-orders="['ascending', 'descending']"
        width="110"
        align="right"
      >
        <template #default="{ row }">
          <span v-if="isInProcess(row.name)" class="latency-cell local">本地</span>
          <span v-else-if="row.latency_ms === null" class="latency-cell dim">未探测</span>
          <span v-else-if="row.latency_ms === 0" class="latency-cell dim">
            0<span class="ms">ms</span>
          </span>
          <span v-else class="latency-cell" :class="latencyClass(row.latency_ms)">
            {{ row.latency_ms }}<span class="ms">ms</span>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="错误" width="70" align="center">
        <template #default="{ row }">
          <el-tooltip v-if="row.last_error" :content="`${row.last_error} (点击复制)`" placement="top">
            <el-button text class="err-btn" @click.stop="copyError(row.last_error)">
              <el-icon color="var(--el-color-danger)"><WarningFilled /></el-icon>
            </el-button>
          </el-tooltip>
          <span v-else class="err-empty">—</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import { copyText } from '@/utils/clipboard'
import type {
  StatusSummary,
  UpstreamStatus,
  UpstreamStatusValue,
} from '@/api/status'

/** 上游总览: 纯展示 + 过滤/排序。**不自动刷新**, 只在手动「刷新」
 *  (emit refresh → 父层拉取并回传 props) 或首次进入时更新。 */
const props = defineProps<{
  /** 已带 name 字段的行 (父层从 {name: info} map 而来)。 */
  rows: UpstreamRow[]
  summary: StatusSummary | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
}>()

export interface UpstreamRow extends UpstreamStatus {
  name: string
}

const STATUS_TEXT: Record<string, string> = {
  online: '在线',
  degraded: '降级',
  offline: '离线',
  configured: '就绪',
  disabled: '已禁用',
  unknown: '未知',
}
const STATUS_ORDER: Record<string, number> = {
  online: 0,
  degraded: 1,
  configured: 2,
  offline: 3,
  disabled: 4,
  unknown: 5,
}
const TYPE_TEXT: Record<string, string> = { http: 'HTTP', stdio: 'Stdio', npx: 'NPX' }
const UPSTREAM_DISPLAY_NAME: Record<string, string> = {
  tdx_quant: '通达信-量化终端',
  mootdx2: 'MooTDX2',
  fuyao_ashare: '同花顺-a-share',
  fuyao_index: '同花顺-a-share-index',
  fuyao_meta: '同花顺-meta',
  fuyao_fund: '同花顺-fund',
}

const filter = ref<'all' | 'healthy' | 'unhealthy'>('all')
const ALLOWED_SORT_KEYS = ['name', 'status', 'latency'] as const
type SortKey = (typeof ALLOWED_SORT_KEYS)[number]
const sortKey = ref<SortKey | null>(null)
const sortAsc = ref(true)

const summaryCards = [
  { key: 'total', label: '上游总数', cls: '' },
  { key: 'online', label: '在线', cls: 'text-online' },
  { key: 'degraded', label: '降级', cls: 'text-degraded' },
  { key: 'offline', label: '离线', cls: 'text-offline' },
  { key: 'configured', label: '已配置', cls: 'text-configured' },
  { key: 'disabled', label: '已禁用', cls: 'text-disabled' },
] as const

function isHealthy(row: UpstreamRow): boolean {
  return row.status === 'online' || row.status === 'configured'
}

const healthyCount = computed(() => props.rows.filter(isHealthy).length)

const filteredRows = computed(() => {
  if (filter.value === 'healthy') return props.rows.filter(isHealthy)
  if (filter.value === 'unhealthy') return props.rows.filter((row) => !isHealthy(row))
  return props.rows
})

const sortedRows = computed(() => {
  if (!sortKey.value) return filteredRows.value
  const key = sortKey.value
  const dir = sortAsc.value ? 1 : -1
  return [...filteredRows.value].sort((a, b) => {
    let va: string | number
    let vb: string | number
    if (key === 'name') {
      va = a.name
      vb = b.name
    } else if (key === 'status') {
      va = STATUS_ORDER[a.status] ?? 99
      vb = STATUS_ORDER[b.status] ?? 99
    } else {
      va = a.latency_ms === null ? -1 : a.latency_ms
      vb = b.latency_ms === null ? -1 : b.latency_ms
    }
    if (va < vb) return -1 * dir
    if (va > vb) return 1 * dir
    return 0
  })
})

function displayName(name: string): string {
  return UPSTREAM_DISPLAY_NAME[name] ?? name
}

function typeText(type: string): string {
  return TYPE_TEXT[type] || type || '-'
}

function statusIcon(status: UpstreamStatusValue): string {
  switch (status) {
    case 'online': return '✓'
    case 'degraded': return '⚠'
    case 'offline': return '✕'
    // M2: unknown = 启动中未探测, 用中性 '?' (旧码与 offline 同 '✕' 红色, 误报故障)。
    case 'unknown': return '?'
    case 'configured': return '●'
    case 'disabled': return '⊘'
    default: return '?'
  }
}

function latencyClass(ms: number): string {
  if (ms > 3000) return 'bad'
  if (ms > 1000) return 'slow'
  return 'good'
}

/** 进程内组件: 与网关同进程、无网络往返, 延迟列显示「本地」而非「未探测」。 */
const IN_PROCESS_UPSTREAMS = new Set([
  'mootdx2',
  'tdx_quant',
  'omni',
  'thsdk',
])

function isInProcess(name: string): boolean {
  return IN_PROCESS_UPSTREAMS.has(name)
}

function copyError(text: string): void {
  void copyText(text, '错误信息已复制')
}

function onSortChange(payload: { prop: string | null; order: 'ascending' | 'descending' | null }): void {
  if (payload.prop === null || payload.order === null) {
    sortKey.value = null
    return
  }
  // 守卫: 只允许已知列触发排序, 否则静默忽略 (避免排序键越界)
  if (!(ALLOWED_SORT_KEYS as readonly string[]).includes(payload.prop)) {
    sortKey.value = null
    return
  }
  sortKey.value = payload.prop as SortKey
  sortAsc.value = payload.order === 'ascending'
}
</script>

<style scoped>
.panel-card {
  border-radius: 8px;
}

.card-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin-bottom: 12px;
  min-height: 70px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 12px 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.stat-num {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.text-online { color: var(--el-color-success); }
.text-degraded { color: var(--el-color-warning); }
.text-offline { color: var(--el-color-danger); }
.text-configured { color: var(--el-color-primary); }
.text-disabled { color: var(--el-text-color-placeholder); }

.filter-chips {
  margin-bottom: 12px;
}

.chip-count {
  display: inline-block;
  margin-left: 4px;
  font-size: 12px;
  opacity: 0.75;
}

.ups-table {
  width: 100%;
}

.ups-name {
  font-weight: 500;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
}

.status-icon {
  font-size: 12px;
  font-weight: 700;
}

.icon-online { color: var(--el-color-success); }
.icon-degraded { color: var(--el-color-warning); }
.icon-offline { color: var(--el-color-danger); }
/* M2: unknown 中性灰, 不与 offline 红色故障同色。 */
.icon-unknown { color: var(--el-text-color-placeholder); }
.icon-configured { color: var(--el-color-primary); }
.icon-disabled { color: var(--el-text-color-secondary); }

.b-online { color: var(--el-color-success); background: var(--el-color-success-light-9); }
.b-degraded { color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.b-offline { color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
/* M2: unknown 中性浅灰底 (启动中), 不再是红色 danger。 */
.b-unknown { color: var(--el-text-color-secondary); background: var(--el-fill-color-light); }
.b-configured { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.b-disabled { color: var(--el-text-color-secondary); background: var(--el-fill-color-light); }

.status-badge.muted {
  opacity: 0.75;
}

.latency-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.latency-cell .ms {
  opacity: 0.6;
  font-weight: 400;
}

.latency-cell.good { color: var(--el-color-success); }
.latency-cell.slow { color: var(--el-color-warning); }
.latency-cell.bad { color: var(--el-color-danger); }
.latency-cell.dim {
  color: var(--el-text-color-placeholder);
  font-weight: 400;
}

.latency-cell.local {
  color: var(--el-text-color-secondary);
  font-weight: 500;
}

.err-btn {
  padding: 4px;
  min-width: 24px;
  min-height: 24px;
}

.err-empty {
  color: var(--el-text-color-placeholder);
  opacity: 0.5;
}

@media (max-width: 1100px) {
  .summary-grid { grid-template-columns: repeat(3, 1fr); }
}

@media (max-width: 560px) {
  .summary-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
