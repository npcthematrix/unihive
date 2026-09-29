<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="error-toolbar">
        <h2 class="card-title">错误跟踪
          <small class="sub">MCP 工具调用失败记录 (不含入参)</small>
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

    <!-- 摘要 -->
    <div class="error-summary">
      <div class="summary-item">
        <span class="summary-num">{{ inMemory }}</span>
        <span class="summary-label">内存近期</span>
      </div>
      <div class="summary-item">
        <span class="summary-num">{{ inDb }}</span>
        <span class="summary-label">SQLite 历史</span>
      </div>
      <div class="summary-item">
        <span class="summary-num">{{ filteredItems.length }}</span>
        <span class="summary-label">当前显示</span>
      </div>
      <div class="summary-item wide">
        <span class="summary-label">最近错误</span>
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
      记录由网关在工具调用失败时自动写入；仅近期内存 ({{ inMemory }}/{{ memoryCap }}),
      满 {{ flushThreshold }} 条批量溢写 SQLite；库内上限 {{ dbCap }} 条自动 LRU 淘汰。
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  getErrors,
  type ErrorItem,
} from '@/api/errors'

/**
 * 错误跟踪面板。自带轻量轮询: 仅在所在 tab 激活时拉取, 离开停止。
 * 与 PipelinePanel 的快采模式同款, 避免父视图多一路请求。
 */
const props = defineProps<{ active: boolean }>()

const loading = ref(false)
const loadError = ref('')
const items = ref<ErrorItem[]>([])
const inMemory = ref(0)
const inDb = ref(0)
const latestTs = ref<string | null>(null)
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

// 与后端 error_buffer 常量对齐 (展示用)
const memoryCap = 200
const flushThreshold = 150
const dbCap = 5000
const POLL_INTERVAL = 10_000
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  loading.value = true
  loadError.value = ''
  try {
    const resp = await getErrors({
      limit: 100,
      history: scope.value === 'history',
    })
    items.value = resp.items.map((it, idx) => ({
      ...it,
      rowKey: `${it.ts}-${it.tool}-${idx}`,
    }))
    inMemory.value = resp.in_memory
    inDb.value = resp.in_db
    latestTs.value = resp.latest_ts
  } catch (e: unknown) {
    loadError.value = e instanceof Error ? e.message : '错误记录拉取失败'
  } finally {
    loading.value = false
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

const latestLabel = computed(() =>
  latestTs.value ? formatTs(latestTs.value) : '—',
)

function formatTs(ts: string): string {
  // 后端给 UTC ISO; 转本地时间显示。若解析失败原样返回。
  const d = new Date(ts)
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

watch(
  () => props.active,
  (active) => {
    if (active) {
      refresh()
      timer = setInterval(refresh, POLL_INTERVAL)
    } else if (timer) {
      clearInterval(timer)
      timer = null
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.panel-card {
  border-radius: 8px;
}

.error-toolbar {
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
  margin-left: 6px;
}

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

.error-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
}

.summary-item.wide {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.summary-num {
  font-size: 22px;
  font-weight: 600;
  color: var(--el-color-primary);
  line-height: 1.2;
}

.summary-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.summary-value {
  font-size: 13px;
  color: var(--el-text-color-primary);
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
}
</style>
