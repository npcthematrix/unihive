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
         (styles/main.css), 旧码本页另写一套居中 6 列版式 (B7)。
         T-M1: 本页只保留下方表格一个 loading 遮罩 —— 首屏时汇总格与表同时转两个
         圈是噪音, 而汇总格在 summary 为 null 时本来就渲染 '—', 已是可用空态。 -->
    <div class="sum-grid summary-grid">
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

    <!-- 上游表
         T-B1/T-B2: 列宽按内容实测重排 —— 固定列 (类型/状态/延迟/错误) 从
         120/110/110/70 = 410px 压到 88/92/80/48 = 308px, 腾出的宽度交给两个
         min-width 列 (上游 160 / 说明 200)。el-table 是 table-layout:fixed,
         min-width 列吃掉全部富余宽度, 所以窄屏下最小总宽从 690 降到 668,
         宽屏下上游名与说明各得一半富余。
         T-B6: 不额外加 matchMedia/resize 去隐藏说明列 —— el-table__body-wrapper
         自带横向滚动 (不受 .view-body 的 overflow-x:hidden 影响), 内容始终可达;
         「错误跟踪」标签的表也是同一套 min-width + 内部滚动约定。 -->
    <el-table
      v-loading="loading"
      :data="sortedRows"
      size="small"
      :empty-text="emptyText"
      @sort-change="onSortChange"
    >
      <el-table-column
        prop="name"
        label="上游"
        sortable="custom"
        :sort-orders="['ascending', 'descending', null]"
        min-width="160"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <span class="ups-name">{{ displayName(row.name) }}</span>
        </template>
      </el-table-column>
      <!-- T-B3: tag 会把不认识的类型原样打出来, 也可能因为列窄被省略号截断;
           :title 保证后端原始 type id 永远可悬停取回。 -->
      <el-table-column label="类型" width="88">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" round :title="row.type">
            {{ typeText(row.type) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="status"
        label="状态"
        sortable="custom"
        :sort-orders="['ascending', 'descending', null]"
        width="92"
      >
        <!-- H3: 徽标一律走 rowStatus() 而不是裸 row.status。后端 _do_probe 对
             http / http_jsonrpc 类型不看 enabled, 照样探活并写 online/degraded/offline,
             于是配置里关掉的 HTTP 上游在这里显示成绿色「在线」, 而同屏汇总卡把它
             算进「已禁用」、过滤片又把它归到「异常」—— 一行数据三种口径。 -->
        <!-- T-B7: 状态点统一到全局 .status-dot 词汇 (styles/main.css, 6 态齐全),
             不再用本页独有的 ✓/⚠/✕/? 文字字形 —— 同页缓存三卡已在用那套点,
             两套词汇表达同一概念。无障碍不受影响: 旁边的中文状态文字才是非颜色
             通道, 点本身 aria-hidden。 -->
        <template #default="{ row }">
          <span
            class="status-badge"
            :class="['b-' + rowStatus(row), { muted: rowStatus(row) === 'disabled' }]"
          >
            <span
              class="status-dot"
              :class="'status-' + rowStatus(row)"
              aria-hidden="true"
            />
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
        :sort-orders="['ascending', 'descending', null]"
        width="80"
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
      <el-table-column label="错误" width="48" align="center">
        <template #default="{ row }">
          <el-tooltip v-if="row.last_error" :content="`${row.last_error} (点击复制)`" placement="top">
            <el-button text class="err-btn" @click.stop="copyError(row.last_error)">
              <el-icon color="var(--el-color-danger)"><WarningFilled /></el-icon>
            </el-button>
          </el-tooltip>
          <span v-else class="err-empty">—</span>
        </template>
      </el-table-column>
      <!-- T-B2: 后端 /api/status 每行都带 description (upstreams.yaml 里写的
           人话说明), 旧码从不展示 —— 读者看到 "fuyao_ashare" 这种 id 只能去翻
           配置文件。放最后一列, 保证状态/延迟/错误这些可操作列在窄屏下仍靠左。 -->
      <el-table-column
        prop="description"
        label="说明"
        min-width="200"
        show-overflow-tooltip
      />
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import { copyText } from '@/utils/clipboard'
import { upstreamDisplayName } from '@/constants/upstreams'
import { isUsableUpstream } from '@/api/status'
import type { StatusSummary, UpstreamRow } from '@/api/status'
import {
  rowStatus,
  sortUpstreamRows,
  typeText,
  upstreamEmptyText,
  type UpstreamSortKey,
} from './upstreamTable'

/** 上游总览: 纯展示 + 过滤/排序。**不自动刷新**, 只在手动「刷新」
 *  (emit refresh → 父层拉取并回传 props) 或首次进入时更新。 */
const props = defineProps<{
  /** 已带 name 字段的行 (父层从 {name: info} map 而来)。 */
  rows: UpstreamRow[]
  summary: StatusSummary | null
  loading: boolean
  /** T-M1: 是否曾成功取到过上游状态 —— 空态要区分「没加载成功」与「真没配」。 */
  everLoaded: boolean
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

const filter = ref<'all' | 'healthy' | 'unhealthy'>('all')
// L11: 与列上的 prop 一致 (行字段是 latency_ms, 旧码写 'latency')。
const ALLOWED_SORT_KEYS: readonly UpstreamSortKey[] = ['name', 'status', 'latency_ms']
const sortKey = ref<UpstreamSortKey | null>(null)
const sortAsc = ref(true)

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

const sortedRows = computed(() =>
  sortUpstreamRows(filteredRows.value, sortKey.value, sortAsc.value))

const emptyText = computed(() =>
  upstreamEmptyText({
    rowCount: props.rows.length,
    filteredCount: filteredRows.value.length,
    loading: props.loading,
    everLoaded: props.everLoaded,
  }))

const displayName = upstreamDisplayName

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

/** T-M2: sort-orders 含 null, 所以第三次点击会带 order=null 进来 —— 复位到
 *  后端配置顺序。旧码 sort-orders 只列了升/降两态, onSortChange 里的 null
 *  分支永远走不到 (死代码), 用户一旦排了序就回不去。 */
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
  sortKey.value = payload.prop as UpstreamSortKey
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

.ups-name {
  font-weight: 500;
}

/* 状态徽标: 胶囊底 + 全局 .status-dot 状态点 (T-B7, 见 styles/main.css)。
   列宽 92px 下最宽的「已禁用」= 20 padding + 7 dot + 6 gap + 36 文字 = 69,
   加 small 表格 16px cell padding = 85, 仍有余量。 */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
}

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
