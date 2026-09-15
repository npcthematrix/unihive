<template>
  <div class="status-page">
    <!-- 上游服务状态 -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">上游服务状态</h2>
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
              <span class="status-dot" :class="dotClass(row.status)" />
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

    <!-- 缓存 -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">缓存 <small class="sub">(运行时统计)</small></h2>
          <div class="toolbar-actions">
            <span class="timestamp">{{ cacheTimeText }}</span>
            <el-button :icon="Refresh" :loading="manualLoading" @click="load(true)">立即刷新</el-button>
          </div>
        </div>
      </template>

      <div v-loading="initialLoading" class="cache-grid">
        <div class="cache-hitrate-card">
          <div class="cache-hitrate-head">
            <span class="mini-label">命中率</span>
            <span class="cache-hitrate-value">{{ hitRateText }}</span>
          </div>
          <el-progress
            v-if="hitRatePct !== null"
            :percentage="hitRatePct"
            :stroke-width="10"
            :show-text="false"
            :color="hitRateColor"
          />
          <div v-else class="hitrate-bar-empty" />
          <div class="cache-hitrate-meta">
            <span><strong>{{ cache?.hits ?? 0 }}</strong> 命中</span>
            <span><strong>{{ cache?.misses ?? 0 }}</strong> 未命中</span>
            <span class="hint">{{ hitRateHint }}</span>
          </div>
        </div>

        <div class="cache-mini-stat">
          <div class="mini-label">状态</div>
          <div class="mini-value">
            <span class="status-dot" :class="cache?.enabled ? 'status-online' : 'status-disabled'" />
            <span :class="cache?.enabled ? 'on' : 'off'">{{ cache?.enabled ? '启用' : '停用' }}</span>
          </div>
        </div>
        <div class="cache-mini-stat">
          <div class="mini-label">活跃条目</div>
          <div class="mini-value">{{ cache?.enabled && cache.live_entries != null ? cache.live_entries : '—' }}</div>
          <div class="mini-hint">未过期</div>
        </div>
        <div class="cache-mini-stat">
          <div class="mini-label">总条目</div>
          <div class="mini-value">{{ cache?.enabled && cache.entries != null ? cache.entries : '—' }}</div>
        </div>
        <div class="cache-mini-stat">
          <div class="mini-label">数据库</div>
          <div class="mini-value">{{ cache?.enabled ? `${(cache.db_size_mb || 0).toFixed(2)} MB` : '—' }}</div>
        </div>
      </div>
      <p v-if="cache && !cache.enabled" class="cache-off-hint">{{ cache.error || '缓存数据库未创建' }}</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import {
  getStatus,
  type CacheStats,
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
const STALE_THRESHOLD_SEC = 30
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
const cache = ref<CacheStats | null>(null)

const initialLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
const lastFetchSec = ref(0)
const nowSec = ref(Math.floor(Date.now() / 1000))

const filter = ref<FilterKey>('all')
const sortKey = ref<SortKey>(null)
const sortAsc = ref(true)
const autoRefresh = ref(false)

let clockTimer: ReturnType<typeof setInterval> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let inFlight = false

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
  if (rows.value.length === 0) return '暂无上游配置：编辑 config/upstreams.yaml 后重启网关'
  if (filter.value === 'healthy') return '当前没有正常的上游'
  if (filter.value === 'unhealthy') return '当前没有异常的上游'
  return '没有匹配的上游'
})

const ageSec = computed(() =>
  lastFetchSec.value ? Math.max(0, nowSec.value - lastFetchSec.value) : 0,
)

const refreshLabel = computed(() => {
  if (loadError.value && lastFetchSec.value === 0) return `刷新失败 (${loadError.value})`
  if (!lastFetchSec.value) return '从未刷新'
  const sec = ageSec.value
  if (sec < 5) return '刚刚刷新'
  if (sec < 60) return `${sec} 秒前刷新`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前刷新`
  return `${Math.floor(sec / 3600)} 小时前刷新`
})

const cacheTimeText = computed(() =>
  cache.value?.timestamp ? new Date(cache.value.timestamp * 1000).toLocaleTimeString('zh-CN') : '-',
)

const hitRatePct = computed(() => {
  const c = cache.value
  if (!c?.enabled || c.hit_rate == null) return null
  return Number((c.hit_rate * 100).toFixed(1))
})

// 展示固定一位小数 (旧版 toFixed(1)); hitRatePct 是数字, 供 el-progress 宽度用。
const hitRateText = computed(() => {
  const c = cache.value
  if (!c?.enabled || c.hit_rate == null) return '—'
  return `${(c.hit_rate * 100).toFixed(1)}%`
})

const hitRateHint = computed(() => {
  const c = cache.value
  if (!c?.enabled) return c?.error || '缓存数据库未创建'
  const total = (c.hits ?? 0) + (c.misses ?? 0)
  return total > 0 && c.hit_rate != null ? `共 ${total} 次请求` : '网关启动后累计 (尚未请求)'
})

const hitRateColor = computed(() => {
  const pct = hitRatePct.value
  if (pct === null) return undefined
  if (pct > 70) return 'var(--el-color-success)'
  if (pct > 40) return 'var(--el-color-warning)'
  return 'var(--el-color-danger)'
})

function displayName(name: string): string {
  return UPSTREAM_DISPLAY_NAME[name] ?? name
}

function typeText(type: string): string {
  return TYPE_TEXT[type] || type || '-'
}

function dotClass(status: UpstreamStatusValue): string {
  switch (status) {
    case 'online':
      return 'status-online'
    case 'degraded':
      return 'status-degraded'
    case 'offline':
    case 'unknown':
      return 'status-offline'
    default:
      return 'status-disabled'
  }
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
    cache.value = data.cache
    lastFetchSec.value = Math.floor(Date.now() / 1000)
  } catch (error) {
    loadError.value = errorMessage(error, '请检查网关是否运行')
    // 与旧版一致: 失败后强制下次重拉
    lastFetchSec.value = 0
  } finally {
    initialLoading.value = false
    manualLoading.value = false
    inFlight = false
  }
}

watch(autoRefresh, (on) => {
  if (!on) {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    return
  }
  pollTimer = setInterval(() => {
    // 标签页隐藏时跳过, 切回时由 visibilitychange 监听补一次 (见 onVisibility)。
    // 避免后台轮询空跑, 后端网关不至于被空请求打。
    if (typeof document !== 'undefined' && document.hidden) return
    void load(false)
  }, 10_000)
})

/** 标签页重新可见且数据超过 30s 时静默重拉 (等价旧版切回面板的 stale 检查)。 */
function onVisibility(): void {
  if (!document.hidden && lastFetchSec.value && nowSec.value - lastFetchSec.value > STALE_THRESHOLD_SEC) {
    void load(false)
  }
}

onMounted(() => {
  void load(true)
  clockTimer = setInterval(() => {
    nowSec.value = Math.floor(Date.now() / 1000)
  }, 1000)
  document.addEventListener('visibilitychange', onVisibility)
})

onUnmounted(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (pollTimer) clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisibility)
})
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

.sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
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

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.status-online {
  background: var(--el-color-success);
  box-shadow: 0 0 0 3px var(--el-color-success-light-9);
}

.status-degraded {
  background: var(--el-color-warning);
  box-shadow: 0 0 0 3px var(--el-color-warning-light-9);
}

.status-offline {
  background: var(--el-color-danger);
  box-shadow: 0 0 0 3px var(--el-color-danger-light-9);
}

.status-disabled {
  background: var(--el-text-color-placeholder);
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

.err-btn {
  padding: 2px;
}

.err-empty {
  color: var(--el-text-color-placeholder);
  opacity: 0.5;
}

.timestamp {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.cache-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.cache-hitrate-card {
  grid-column: span 2;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.cache-hitrate-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.cache-hitrate-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.hitrate-bar-empty {
  height: 10px;
  border-radius: 5px;
  background: var(--el-fill-color-light);
}

.cache-hitrate-meta {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.cache-hitrate-meta .hint {
  color: var(--el-text-color-secondary);
}

.cache-mini-stat {
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.mini-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.mini-value {
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 7px;
}

.mini-value .on { color: var(--el-color-success); }
.mini-value .off { color: var(--el-text-color-secondary); }

.mini-hint {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
}

.cache-off-hint {
  margin: 10px 2px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
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

@media (max-width: 700px) {
  .cache-hitrate-card {
    grid-column: span 1;
  }
}
</style>
