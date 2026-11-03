<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="error-toolbar">
        <h2 class="card-title">错误跟踪
          <small class="sub">MCP 工具调用失败记录 (含入参, 已截断)</small>
        </h2>
        <div class="error-actions">
          <el-radio-group v-model="scope" size="small">
            <el-radio-button value="recent">仅近期 (内存)</el-radio-button>
            <el-radio-button value="history">含历史 (SQLite)</el-radio-button>
          </el-radio-group>
          <el-select
            v-model="kindFilter"
            size="small"
            placeholder="全部类别"
            class="kind-filter"
          >
            <el-option label="全部类别" value="" />
            <el-option
              v-for="k in KIND_OPTIONS"
              :key="k.value"
              :label="k.label"
              :value="k.value"
            />
          </el-select>
          <el-select
            v-model="toolFilter"
            size="small"
            placeholder="全部工具"
            clearable
            class="tool-filter"
          >
            <el-option label="全部工具" value="" />
            <el-option
              v-for="t in toolOptions"
              :key="t"
              :label="t"
              :value="t"
            />
          </el-select>
          <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
        </div>
      </div>
    </template>

    <!-- 摘要 — .sum-grid/.sum-cell/.sum-num/.sum-label 与「上游总览」共用
         (styles/main.css), 旧码两页各写一套 (居中 vs 左对齐、6 列 vs 4 列、
         断点 1100 vs 768), 同一页三个标签的摘要格子版式全不一样 (B7)。
         M5: 第三格的分母是「最近 PAGE_LIMIT 条」而非全量, 旧码只写「当前显示」,
         与旁边两格的全量计数并排会被读成同一个分母。
         B3: 第四格是横排文案, 占两列 —— 旧码挤在四分之一宽里, 19 字符时间会折行。 -->
    <div class="sum-grid error-summary">
      <div class="sum-cell">
        <span class="sum-num">{{ inMemory }}</span>
        <span class="sum-label">内存近期</span>
      </div>
      <div class="sum-cell">
        <span class="sum-num">{{ inDb }}</span>
        <span class="sum-label">SQLite 历史</span>
      </div>
      <div class="sum-cell">
        <span class="sum-num">{{ filteredItems.length }}</span>
        <span class="sum-label">当前显示 · 最近 {{ PAGE_LIMIT }} 条内</span>
      </div>
      <div class="sum-cell wide">
        <span class="sum-label">最近错误</span>
        <span class="summary-value">{{ latestLabel }}</span>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="warning"
      :closable="false"
      show-icon
      class="error-load-alert"
    />

    <!-- M7: 选了「含历史」而库里一条都没有时, 旧码静默返回内存结果, 用户以为
         看的是历史。history_available 在这里帮不上忙 —— 后端算的是
         `in_db > 0 or history`, history=true 时恒为真 (见 api/errors.ts)。 -->
    <el-alert
      v-if="historyEmpty"
      type="info"
      :title="`历史库暂无溢写记录 (内存满 ${flushThreshold} 条才批量写入), 下面列出的仍是内存近期`"
      :closable="false"
      show-icon
      class="error-load-alert"
    />

    <el-table
      :data="filteredItems"
      v-loading="loading"
      class="error-table"
      row-key="rowKey"
      size="small"
      :empty-text="loading ? '加载中…' : '暂无错误记录'"
      stripe
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="hops-detail">
            <p v-if="!row.hops.length" class="hops-empty">无逐跳信息</p>
            <table v-else class="hops-table">
              <thead>
                <tr>
                  <th>上游</th>
                  <th>结果</th>
                  <th>耗时</th>
                  <th>错误</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(h, i) in row.hops" :key="i">
                  <td class="hop-source">{{ h.source }}</td>
                  <td>
                    <el-tag
                      :type="hopTagType(h)"
                      size="small"
                      effect="plain"
                    >{{ hopLabel(h) }}</el-tag>
                  </td>
                  <td class="hop-duration">{{ h.duration_ms ?? '—' }} ms</td>
                  <td class="hop-error">{{ h.error || '—' }}</td>
                </tr>
              </tbody>
            </table>

            <!-- 传入参数 (便于复现) -->
            <div v-if="hasParams(row.params)" class="params-detail">
              <p class="params-title">传入参数</p>
              <pre class="params-json">{{ formatParams(row.params) }}</pre>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="时间" prop="ts" width="180">
        <template #default="{ row }">{{ formatTs(row.ts) }}</template>
      </el-table-column>
      <el-table-column label="类别" width="130">
        <template #default="{ row }">
          <el-tag
            v-if="row.error_kind"
            :type="kindTagType(row.error_kind)"
            size="small"
            effect="light"
          >{{ kindLabel(row.error_kind) }}</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="工具" prop="tool" min-width="240">
        <template #default="{ row }">
          <span class="tool-name">{{ row.tool }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最终上游" prop="source" width="130">
        <template #default="{ row }">
          <el-tag v-if="row.source" size="small" effect="plain">{{ row.source }}</el-tag>
          <span v-else class="muted">全链失败</span>
        </template>
      </el-table-column>
      <el-table-column label="错误" min-width="320">
        <template #default="{ row }">
          <span class="error-msg" :title="row.error">{{ row.error }}</span>
        </template>
      </el-table-column>
    </el-table>

    <p class="error-hint">
      记录由网关在工具调用失败时自动写入 (含已截断的入参, 便于复现)；本表最多显示最近
      {{ PAGE_LIMIT }} 条, 「全部工具」下拉也只从这 {{ PAGE_LIMIT }} 条里取值。
      仅近期内存 ({{ inMemory }}/{{ memoryCap }}), 满 {{ flushThreshold }} 条批量溢写
      SQLite；库内上限 {{ dbCap }} 条自动 LRU 淘汰。
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { errorMessage } from '@/api/client'
import { POLL_INTERVAL_MS } from '@/constants/time'
import {
  getErrors,
  type ErrorItem,
} from '@/api/errors'

/**
 * 错误跟踪面板。自带轻量轮询: 仅在所在 tab 激活时拉取, 离开停止。
 * 与 PipelinePanel 的快采模式同款, 避免父视图多一路请求。
 * M1: 轮询同时受页头「自动刷新」开关约束 (旧码完全解耦, 关掉开关照拉)。
 */
const props = defineProps<{ active: boolean; autoRefresh: boolean }>()

/** 单次拉取条数上限。表格与「全部工具」下拉都只在这批里取值 (M5)。 */
const PAGE_LIMIT = 100

const loading = ref(false)
const loadError = ref('')
const items = ref<ErrorItem[]>([])
const inMemory = ref(0)
const inDb = ref(0)
// 后端容量常量: 从 API 响应中读取 (旧码硬编码 200/150/5000, 后端调整后前端不一致)
const memoryCap = ref(200)
const flushThreshold = ref(150)
const dbCap = ref(5000)
const scope = ref<'recent' | 'history'>('recent')
const toolFilter = ref('')
const kindFilter = ref('')

/** 类别下拉选项 (与后端 ErrorKind 对齐)。 */
const KIND_OPTIONS = [
  { value: 'invalid_request', label: '参数错误' },
  { value: 'upstream_unavailable', label: '数据源不可用' },
  { value: 'timeout', label: '超时' },
  { value: 'rate_limited', label: '限流' },
  { value: 'no_data', label: '无数据' },
  { value: 'internal_error', label: '内部错误' },
] as const

// 容量常量与后端 error_buffer 对齐 — 从 API 响应动态读取, 不再硬编码。
// 轮询间隔改用全站共享常量 (旧码在本文件另写一份 10_000)。
let timer: ReturnType<typeof setInterval> | null = null
let inFlight = false

async function refresh() {
  // 轮询、手动「刷新」、scope 切换可能撞在一起; 重叠时后返回的旧结果会覆盖新结果。
  if (inFlight) return
  inFlight = true
  loading.value = true
  loadError.value = ''
  try {
    const resp = await getErrors({
      limit: PAGE_LIMIT,
      history: scope.value === 'history',
    })
    items.value = resp.items.map((it) => ({
      ...it,
      // 稳定 key: ts_epoch 是后端附加的 epoch 秒 (排序/去重用), 同一错误
      // 多次拉取时 ts_epoch 不变; 旧码用数组 idx, 数据顺序变化时 rowKey
      // 变化 → el-table row-key 展开行状态错乱 (展开了错误的行)。
      rowKey: `${it.ts_epoch}-${it.tool}`,
    }))
    inMemory.value = resp.in_memory
    inDb.value = resp.in_db
    // 从后端响应同步容量常量 (后端调整后前端自动跟进)
    if (resp.memory_cap) memoryCap.value = resp.memory_cap
    if (resp.flush_threshold) flushThreshold.value = resp.flush_threshold
    if (resp.db_cap) dbCap.value = resp.db_cap
  } catch (e: unknown) {
    // L5: 走全站 errorMessage()。旧码裸用 e.message, 会把 axios 的英文
    // ("Request failed with status code 500" / "retry exhausted") 直接显示给用户。
    loadError.value = errorMessage(e, '错误记录拉取失败')
  } finally {
    loading.value = false
    inFlight = false
  }
}

const toolOptions = computed(() => {
  const s = new Set<string>()
  for (const it of items.value) s.add(it.tool)
  return [...s].sort()
})

const filteredItems = computed(() =>
  items.value.filter(
    (it) =>
      (!toolFilter.value || it.tool === toolFilter.value) &&
      (!kindFilter.value || it.error_kind === kindFilter.value),
  ),
)

/**
 * M6: 旧码直接展示后端 latest_ts —— 那是全量最近一条, 选了工具/类别过滤后
 * 它仍是别的工具的时间, 与下面的表格无关。改为从当前筛选结果里取最新。
 */
const latestLabel = computed(() => {
  const rows = filteredItems.value
  if (!rows.length) return '—'
  const newest = rows.reduce((a, b) => (b.ts_epoch > a.ts_epoch ? b : a))
  return formatTs(newest.ts)
})

/** M7: 「含历史」但库为空 —— 需要说出来, 否则用户以为看的是历史。 */
const historyEmpty = computed(() => scope.value === 'history' && inDb.value === 0)

function formatTs(ts: string): string {
  // 后端给 UTC ISO 毫秒 (带 Z 后缀); 但 SQLite 行的 ts 列可能缺时区后缀,
  // new Date() 会把无后缀的 ISO 当本地时间 → 偏移 8 小时。
  // 补 Z 后缀确保一律按 UTC 解析, 再转本地显示。
  // L12: 判"已带时区"要看结尾的完整偏移量。旧码用 includes('+') + endsWith('Z'),
  // 负偏移 (…-05:00) 两条都不中 → 被追加 Z 变成非法串。
  const normalized = /(Z|[+-]\d{2}:?\d{2})$/.test(ts) ? ts : ts + 'Z'
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return ts
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function hopTagType(h: ErrorItem['hops'][number]) {
  if (h.success === true) return 'success'
  if (h.success === false) return 'danger'
  return 'info'
}
function hopLabel(h: ErrorItem['hops'][number]) {
  if (h.success === true) return '成功'
  if (h.success === false) return '失败'
  return '—'
}

/** 类别 → el-tag 类型 (颜色)。 */
function kindTagType(kind: string) {
  switch (kind) {
    case 'invalid_request':
      return 'warning'
    case 'timeout':
      return 'info'
    case 'rate_limited':
      return 'warning'
    case 'no_data':
      return 'info'
    case 'upstream_unavailable':
      return 'danger'
    default:
      return 'danger' // internal_error
  }
}

/** 类别 → 中文标签。 */
function kindLabel(kind: string): string {
  const found = KIND_OPTIONS.find((k) => k.value === kind)
  return found ? found.label : kind
}

/** params 是否有内容可展示。 */
function hasParams(params: unknown): boolean {
  return (
    !!params &&
    typeof params === 'object' &&
    Object.keys(params as Record<string, unknown>).length > 0
  )
}

/** 把入参渲染成缩进 JSON (已是截断后的安全值)。 */
function formatParams(params: unknown): string {
  try {
    return JSON.stringify(params, null, 2)
  } catch {
    return String(params)
  }
}

/**
 * H1: scope 旧码只在 refresh() 里被读, 没有任何 watch —— 点「仅近期/含历史」
 * 表格纹丝不动, 要等下一次 10s 轮询或手动点「刷新」才生效, 主控看起来是坏的。
 */
watch(scope, () => {
  // 换 scope 后可选工具集合会变, 已选的工具可能不在新集合里, 留着会筛出空表。
  toolFilter.value = ''
  void refresh()
})

/**
 * 激活即拉一次 (进入标签总该有数据, 不看开关); 开关开着才挂轮询。
 * M1: 旧码轮询与页头「自动刷新」完全解耦, 关掉开关照拉。
 * 回调内跳过隐藏页, 与主轮询 (useGatewayRuntime) 同策略。
 */
watch(
  () => [props.active, props.autoRefresh] as const,
  ([active, auto]) => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    if (!active) return
    void refresh()
    if (!auto) return
    timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      void refresh()
    }, POLL_INTERVAL_MS)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
/* .panel-card 圆角 / .error-toolbar / .card-title / .sub 已收敛到
   styles/main.css (全局)。 */

.error-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tool-filter {
  width: 180px;
}

.kind-filter {
  width: 150px;
}

/* L1: 窄屏工具栏控件收缩, 避免换行后独占整行 */
@media (max-width: 768px) {
  .tool-filter,
  .kind-filter {
    width: auto;
    flex: 1 1 120px;
  }
}

/* B7: 格子本体 (.sum-grid/.sum-cell/.sum-num/.sum-label) 已收敛到
   styles/main.css, 与「上游总览」摘要共用一套。这里只留本页特有的部分。 */
.error-summary {
  margin-bottom: 12px;
}

/* 旧 .summary-num 用 primary 蓝强调计数; 收敛后全局 .sum-num 默认 primary 色,
   这里显式重申以免将来全局改中性色时本页静默漂移。 */
.error-summary .sum-num {
  color: var(--el-color-primary);
}

/* B3: 第四格是横排文案 (标签 + 19 字符时间), 占两列; 挤在四分之一宽里会折行。 */
.error-summary .wide {
  grid-column: span 2;
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.summary-value {
  font-size: 13px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}

.error-load-alert {
  margin-bottom: 12px;
}

.tool-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
}

.error-msg {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 12.5px;
  color: var(--el-text-color-regular);
}

.muted {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.hops-detail {
  padding: 8px 12px;
}

.hops-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.hops-table th,
.hops-table td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  vertical-align: top;
}

.hops-table th {
  color: var(--el-text-color-secondary);
  font-weight: 500;
  width: auto;
}

.hop-source {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: nowrap;
}

.hop-duration {
  white-space: nowrap;
  color: var(--el-text-color-secondary);
}

.hop-error {
  color: var(--el-text-color-regular);
}

.hops-empty {
  margin: 0;
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.params-detail {
  margin-top: 12px;
}

.params-title {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-placeholder);
}

.params-json {
  margin: 0;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow: auto;
}

.error-hint {
  margin: 10px 2px 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 768px) {
  .error-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  /* 两列时横排那格独占一整行, 否则 span 2 正好占满、与上面两格挤成一行 */
  .error-summary .wide {
    grid-column: 1 / -1;
  }
}
</style>
