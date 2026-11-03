<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">上游数据源</h2>
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

    <!-- 汇总计数 — .sum-grid/.sum-cell/.sum-num/.sum-label 与「错误跟踪」共用
         (styles/main.css), 旧码本页另写一套居中 6 列版式 (B7)。 -->
    <div v-loading="loading && !rows.length" class="sum-grid summary-grid">
      <div v-for="card in summaryCards" :key="card.key" class="sum-cell">
        <span class="sum-num" :class="card.cls">{{ summary?.[card.key] ?? '—' }}</span>
        <span class="sum-label">{{ card.label }}</span>
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
      :empty-text="rows.length ? '暂无匹配的上游' : '暂无上游数据 (状态未加载或未配置上游)'"
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
        <!-- H3: 徽标一律走 rowStatus() 而不是裸 row.status。后端 _do_probe 对
             http / http_jsonrpc 类型不看 enabled, 照样探活并写 online/degraded/offline,
             于是配置里关掉的 HTTP 上游在这里显示成绿色「在线」, 而同屏汇总卡把它
             算进「已禁用」、过滤片又把它归到「异常」—— 一行数据三种口径。 -->
        <template #default="{ row }">
          <span
            class="status-badge"
            :class="['b-' + rowStatus(row), { muted: rowStatus(row) === 'disabled' }]"
          >
            <span class="status-icon" :class="`icon-${rowStatus(row)}`">
              {{ statusIcon(rowStatus(row)) }}
            </span>
            {{ STATUS_TEXT[rowStatus(row)] ?? '未知' }}
          </span>
        </template>
      </el-table-column>
      <!-- L11: prop 必须是行上真实存在的字段 (latency_ms); 旧码写 "latency",
           custom 排序下不影响显示, 但 el-table 内部拿不到该列的取值。 -->
      <el-table-column
        prop="latency_ms"
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
import { upstreamDisplayName } from '@/constants/upstreams'
import { isUsableUpstream } from '@/api/status'
import type {
  StatusSummary,
  UpstreamStatusValue,
  UpstreamRow,
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

const filter = ref<'all' | 'healthy' | 'unhealthy'>('all')
// L11: 与列上的 prop 一致 (行字段是 latency_ms, 旧码写 'latency')。
const ALLOWED_SORT_KEYS = ['name', 'status', 'latency_ms'] as const
type SortKey = (typeof ALLOWED_SORT_KEYS)[number]
const sortKey = ref<SortKey | null>(null)
const sortAsc = ref(true)

/**
 * H3: 展示用的状态一律以 enabled 为准。后端 _do_probe 只对非 HTTP 类型回
 * 'disabled', http / http_jsonrpc 分支不看 enabled, 照样探活并写
 * online/degraded/offline —— 配置里关掉的 HTTP 上游于是顶着绿色「在线」徽标,
 * 而同屏汇总卡算它「已禁用」、过滤片又把它归进「异常」。
 */
function rowStatus(row: UpstreamRow): UpstreamStatusValue {
  return row.enabled ? row.status : 'disabled'
}

/**
 * M2: 后端按实际状态桶动态累加 (console_api.py `summary[bucket] = …`), 启动
 * 收敛中的上游会落进 unknown 桶。旧码只渲染 6 张固定卡, 于是启动阶段
 * 「上游总数 4」而六卡加起来 3, 缺的那个无处可看 —— 而「网关进程」标签恰恰
 * 会报「N 个未知 (启动中)」。这里在 unknown > 0 时补一张卡。
 */
const summaryCards = computed(() => {
  const base = [
    { key: 'total', label: '上游总数', cls: '' },
    { key: 'online', label: '在线', cls: 'text-online' },
    { key: 'degraded', label: '降级', cls: 'text-degraded' },
    { key: 'offline', label: '离线', cls: 'text-offline' },
    { key: 'configured', label: '已配置', cls: 'text-configured' },
    { key: 'disabled', label: '已禁用', cls: 'text-disabled' },
  ]
  if ((props.summary?.unknown ?? 0) > 0) {
    base.splice(5, 0, { key: 'unknown', label: '启动中', cls: 'text-unknown' })
  }
  return base
})

function isHealthy(row: UpstreamRow): boolean {
  // M4: 与「网关进程」标签同一口径 (api/status.ts isUsableUpstream)。
  // 旧实现只看 status, 忽略 enabled —— 后端会用 client 状态桶覆盖禁用上游的
  // status, 于是同一行在两个标签一边算健康一边不算。
  return isUsableUpstream(row)
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
      va = STATUS_ORDER[rowStatus(a)] ?? 99
      vb = STATUS_ORDER[rowStatus(b)] ?? 99
    } else {
      va = a.latency_ms === null ? -1 : a.latency_ms
      vb = b.latency_ms === null ? -1 : b.latency_ms
    }
    if (va < vb) return -1 * dir
    if (va > vb) return 1 * dir
    return 0
  })
})

const displayName = upstreamDisplayName

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
/* .panel-card 圆角 / .card-toolbar / .card-title 已收敛到 styles/main.css (全局)。
   B7: 摘要格子本体 (.sum-grid/.sum-cell/.sum-num/.sum-label) 同样上提全局, 与
   「错误跟踪」共用一套。旧码本页另写一份居中 + repeat(6,1fr) + 断点 1100/560,
   与错误跟踪的左对齐 repeat(4,…) 断点 768 完全不同 —— 同页三个标签三种版式。
   这里只留本页特有的着色类与间距。 */

.summary-grid {
  margin-bottom: 12px;
  min-height: 70px;
}

.text-online { color: var(--el-color-success); }
.text-degraded { color: var(--el-color-warning); }
.text-offline { color: var(--el-color-danger); }
.text-configured { color: var(--el-color-primary); }
.text-disabled { color: var(--el-text-color-placeholder); }
/* M2: 启动中 (unknown 桶) — 中性灰, 不是故障红也不是健康绿 */
.text-unknown { color: var(--el-text-color-secondary); }

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

/* 摘要格子的断点由全局 .sum-grid 统一 (auto-fit + ≤768px 降 2 列);
   旧码本页另写 1100→3 列 / 560→2 列, 与「错误跟踪」的 768 断点不一致 (B7)。 */
</style>
