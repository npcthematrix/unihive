<template>
  <div class="status-page">
    <!-- 上游数据Provider状态 -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">上游数据Provider状态</h2>
          <div class="toolbar-actions">
            <span
              class="last-refresh"
              :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
            >
              {{ refreshLabel }}
            </span>
            <el-button
              :icon="Refresh"
              :loading="manualLoading"
              @click="load(true)"
            >立即刷新</el-button>
            <el-tooltip content="每 10 秒自动刷新" placement="top">
              <span class="auto-toggle">
                <el-switch v-model="autoRefresh" />
                <span class="auto-label">自动刷新</span>
              </span>
            </el-tooltip>
          </div>
        </div>
      </template>

      <!-- 汇总卡片 -->
      <div v-loading="initialLoading" class="summary-grid">
        <div v-for="card in summaryCards" :key="card.key" class="stat-card">
          <span class="stat-num" :class="card.cls">{{ summary?.[card.key] ?? '—' }}</span>
          <span class="stat-label">{{ card.label }}</span>
        </div>
      </div>

      <!-- 过滤 chips -->
      <el-radio-group v-model="filter" size="default" class="filter-chips">
        <el-radio-button value="all">
          全部 <span class="chip-count">{{ rows.length }}</span>
        </el-radio-button>
        <el-radio-button
          value="healthy"
          aria-label="正常：status 为 online 或 configured（已就绪但未探测算在线）"
        >
          <el-tooltip content="status ∈ {online, configured}（已就绪但未探测算在线）" placement="top">
            <span>正常 <span class="chip-count">{{ healthyCount }}</span></span>
          </el-tooltip>
        </el-radio-button>
        <el-radio-button
          value="unhealthy"
          aria-label="异常：除 online 和 configured 之外的上游"
        >
          <el-tooltip content="除 online + configured 之外的上游" placement="top">
            <span>异常 <span class="chip-count">{{ rows.length - healthyCount }}</span></span>
          </el-tooltip>
        </el-radio-button>
      </el-radio-group>

      <!-- 上游表 -->
      <el-table
        v-loading="initialLoading"
        :data="sortedRows"
        class="ups-table"
        :empty-text="tableEmptyText"
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
        <el-table-column label="类型" width="140">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" round>{{ typeText(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="status"
          label="状态"
          sortable="custom"
          :sort-orders="['ascending', 'descending']"
          width="120"
        >
          <template #default="{ row }">
            <span
              class="status-badge"
              :class="['b-' + row.status, { muted: row.status === 'disabled' }]"
              :aria-label="`状态: ${STATUS_TEXT[row.status] ?? '未知'}`"
            >
              <span class="status-icon" :class="`icon-${row.status}`" aria-hidden="true">
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
          width="130"
          align="right"
          header-align="right"
        >
          <template #default="{ row }">
            <el-tooltip v-if="row.latency_ms === null" content="未探测 - 上游未在线或未发起请求" placement="top">
              <span class="latency-cell dim">未探测</span>
            </el-tooltip>
            <el-tooltip v-else-if="row.latency_ms === 0" content="可能为缓存命中或本地解析" placement="top">
              <span class="latency-cell dim">0<span class="ms">ms</span></span>
            </el-tooltip>
            <span v-else class="latency-cell" :class="latencyClass(row.latency_ms)">
              {{ row.latency_ms }}<span class="ms">ms</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="错误" width="80" align="center">
          <template #default="{ row }">
            <el-tooltip v-if="row.last_error" :content="`${row.last_error} (点击复制)`" placement="top">
              <el-button
                text
                class="err-btn"
                :aria-label="`复制 ${row.name} 错误信息`"
                @click.stop="copyError(row.last_error)"
              >
                <el-icon color="var(--el-color-danger)"><WarningFilled /></el-icon>
              </el-button>
            </el-tooltip>
            <span v-else class="err-empty">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { formatRefreshLabel } from '@/utils/time'
import { STALE_THRESHOLD_SEC } from '@/constants/time'
import {
  getStatus,
  type StatusSummary,
  type UpstreamStatus,
  type UpstreamStatusValue,
} from '@/api/status'

interface UpstreamRow extends UpstreamStatus {
  name: string
}

type FilterKey = 'all' | 'healthy' | 'unhealthy'
type SortKey = 'name' | 'status' | 'latency' | null
type SortOrder = 'ascending' | 'descending' | null

/** 与旧 console.js 完全一致的展示常量, 新上游缺映射时回退原始键/类型。 */
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

const rows = ref<UpstreamRow[]>([])
const summary = ref<StatusSummary | null>(null)

const initialLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')

const filter = ref<FilterKey>('all')
const sortKey = ref<SortKey>(null)
const sortAsc = ref(true)

let inFlight = false

/** 自动刷新 + 时钟 + tab 可见时陈旧重拉 — 见 web/src/composables/useAutoRefresh.ts */
const { autoRefresh, lastFetchSec, ageSec, markFetched, resetFetched } = useAutoRefresh({
  onPoll: () => { void load(false) },
})

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

const healthyCount = computed(() => rows.value.filter(isHealthy).length)

const filteredRows = computed(() => {
  if (filter.value === 'healthy') return rows.value.filter(isHealthy)
  if (filter.value === 'unhealthy') return rows.value.filter((r) => !isHealthy(r))
  return rows.value
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

const tableEmptyText = computed(() => {
  if (initialLoading.value) return '加载中…'
  if (loadError.value && rows.value.length === 0) {
    return `无法加载上游状态：${loadError.value}`
  }
  if (rows.value.length === 0) return '暂无上游配置：请到「配置信息 → 上游」检查启用状态 (YAML 逃生模式下需编辑 config/upstreams.yaml 后重启)'
  if (filter.value === 'healthy') return '当前没有正常的上游'
  if (filter.value === 'unhealthy') return '当前没有异常的上游'
  return '没有匹配的上游'
})

const refreshLabel = computed(() =>
  formatRefreshLabel({
    loadError: loadError.value,
    lastFetchSec: lastFetchSec.value,
    ageSec: ageSec.value,
  }),
)

function displayName(name: string): string {
  return UPSTREAM_DISPLAY_NAME[name] ?? name
}

function typeText(type: string): string {
  return TYPE_TEXT[type] || type || '-'
}

/** 状态色盲友好图标 (✓ 在线 / ⚠ 降级/离线/未知 / ● 已就绪 / ⊘ 已禁用)。
 *  与颜色 + 文字并存, 提供冗余通道。 */
function statusIcon(status: UpstreamStatusValue): string {
  switch (status) {
    case 'online':
      return '✓'
    case 'degraded':
      return '⚠'
    case 'offline':
    case 'unknown':
      return '✕'
    case 'configured':
      return '●'
    case 'disabled':
      return '⊘'
    default:
      return '?'
  }
}

function latencyClass(ms: number): string {
  if (ms > 3000) return 'bad'
  if (ms > 1000) return 'slow'
  return 'good'
}

function copyError(text: string): void {
  void copyText(text, '错误信息已复制')
}

/** el-table 两态排序 (sort-orders 限定 asc/desc, 无第三态取消)。 */
function onSortChange(payload: { prop: string | null; order: SortOrder }): void {
  if (payload.prop === null || payload.order === null) {
    sortKey.value = null
    return
  }
  sortKey.value = payload.prop as Exclude<SortKey, null>
  sortAsc.value = payload.order === 'ascending'
}

async function load(manual = false): Promise<void> {
  // 撞上在途请求 (轮询) 时不发第二个请求, 但仍点亮按钮 loading —
  // 在途请求的 finally 会统一清除。
  if (inFlight) {
    if (manual) manualLoading.value = true
    return
  }
  inFlight = true
  const firstLoad = rows.value.length === 0 && summary.value === null
  if (firstLoad) initialLoading.value = true
  if (manual) manualLoading.value = true
  loadError.value = ''
  try {
    const data = await getStatus()
    rows.value = Object.entries(data.upstreams ?? {}).map(([name, info]) => ({ name, ...info }))
    summary.value = data.summary
    markFetched()
  } catch (error) {
    loadError.value = errorMessage(error, '请检查网关是否运行')
    // 与旧版一致: 失败后强制下次重拉
    resetFetched()
  } finally {
    initialLoading.value = false
    manualLoading.value = false
    inFlight = false
  }
}
</script>

<style scoped>
.status-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

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

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.last-refresh {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.last-refresh.failed {
  color: var(--el-color-danger);
}

.last-refresh.stale {
  color: var(--el-color-warning);
}

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: default;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin-bottom: 14px;
  min-height: 74px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 14px 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
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
  line-height: 1;
  font-weight: 700;
}

.status-icon.icon-online { color: var(--el-color-success); }
.status-icon.icon-degraded { color: var(--el-color-warning); }
.status-icon.icon-offline,
.status-icon.icon-unknown { color: var(--el-color-danger); }
.status-icon.icon-configured { color: var(--el-color-primary); }
.status-icon.icon-disabled { color: var(--el-text-color-secondary); }

.status-badge.b-online {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.status-badge.b-degraded {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.status-badge.b-offline,
.status-badge.b-unknown {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.status-badge.b-configured {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.status-badge.b-disabled {
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
}

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
  cursor: help;
}

/* 触达目标 ≥24px (WCAG 2.5.8): 16px 图标 + 4px*2 */
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
  .summary-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 900px) {
  .summary-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 560px) {
  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
