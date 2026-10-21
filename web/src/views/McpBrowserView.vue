<script setup lang="ts">
/**
 * MCP 浏览器 —— 展示 "自身即 MCP server" 的上游返回的原始 tools/list。
 *
 * 取数是纯按需实时的: 不预取、不缓存, 每次切上游或点刷新都现场打上游。
 * 这是刻意的 —— 拉的是 "此刻上游到底声明了什么", 缓存会立刻让这个页面失去
 * 意义。可见代价是切换时右栏短暂清空, 见 selectUpstream 里的注释。
 *
 * 所有可判断的逻辑都在 ./mcp-browser/tools.ts 里 (纯函数, 被 vitest 覆盖);
 * 本组件只做渲染与转发 —— 仓库的 vitest 挂不了 .vue。
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import {
  getMcpBrowserUpstreams,
  getUpstreamRawTools,
  type McpBrowserUpstream,
  type RawTool,
  type RawToolsResponse,
} from '@/api/mcp-browser'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import HighlightText from '@/components/HighlightText.vue'
import RawSchemaPanel from './mcp-browser/RawSchemaPanel.vue'
import {
  UPSTREAM_ORDER,
  UPSTREAM_TITLE,
  annotationFlags,
  applyToolFilter,
  extraKeys,
  hasActiveFilters,
  hasMeta,
  hasOutputSchema,
  rowKeyOf,
  schemaProperties,
  summarizePayload,
  toolDescription,
  toolName,
  toolTitle,
  type BrowserToggles,
} from '@/views/mcp-browser/tools'

const route = useRoute()
const router = useRouter()

const upstreams = ref<McpBrowserUpstream[]>([])
const selected = ref('')
const tools = ref<RawTool[]>([])
const payload = ref<RawToolsResponse | null>(null)
const loading = ref(false)
const loadError = ref('')
/** 只记录本次会话真正拉取过的上游计数; 没拉过就显示占位, 不猜。 */
const counts = ref<Record<string, number>>({})

const rawQuery = ref('')
const appliedQuery = ref('')
const toggles = reactive<BrowserToggles>({
  meta: false,
  outputSchema: false,
  annotations: false,
})

const tableRef = ref()
const searchInputRef = ref()
const expandedNames = ref<Set<string>>(new Set())
const copiedName = ref('')

let debounceTimer: ReturnType<typeof setTimeout> | undefined
let copiedTimer: ReturnType<typeof setTimeout> | undefined
/**
 * 请求序号。切上游时旧请求可能还在飞, 回来时不能把上一个上游的数据渲染到
 * 新标题下面 —— 这是并发保护, 不是缓存。
 */
let reqSeq = 0

// ---- 派生 ----
const upstreamOrder = computed(() => {
  const names = upstreams.value.map((u) => u.name)
  const known = UPSTREAM_ORDER as readonly string[]
  return [...known.filter((k) => names.includes(k)), ...names.filter((n) => !known.includes(n))]
})

const navItems = computed(() =>
  upstreamOrder.value.map((name) => {
    const info = upstreams.value.find((u) => u.name === name)
    return {
      key: name,
      title: UPSTREAM_TITLE[name] || name,
      count: counts.value[name],
      available: info?.available ?? false,
      active: name === selected.value,
    }
  }),
)

const visibleTools = computed(() => applyToolFilter(tools.value, appliedQuery.value, toggles))

const filtersActive = computed(() => hasActiveFilters(appliedQuery.value, toggles))

const metaText = computed(() => (payload.value ? summarizePayload(payload.value) : ''))

const currentUpstream = computed(() => upstreams.value.find((u) => u.name === selected.value))

// ---- 搜索 ----
watch(rawQuery, (value) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  // 清空立即生效, 不必再等 300ms
  if (!value) {
    appliedQuery.value = ''
    return
  }
  debounceTimer = setTimeout(() => {
    appliedQuery.value = value.trim().toLowerCase()
  }, 300)
})

/** 清空全部筛选 (搜索词 + 3 个形状 toggle), 纯 toggle 造成的空态也能恢复。 */
function clearFilters(): void {
  rawQuery.value = ''
  appliedQuery.value = ''
  toggles.meta = false
  toggles.outputSchema = false
  toggles.annotations = false
  searchInputRef.value?.focus()
}

// ---- 取数 ----
async function selectUpstream(name: string): Promise<void> {
  if (!name) return
  selected.value = name
  syncQuery(name)
  // 先清空再 await: 慢上游绝不能在切换后的标题下渲染出上一个上游的行。
  tools.value = []
  payload.value = null
  expandedNames.value = new Set()
  await fetchTools(name)
}

async function fetchTools(name: string): Promise<void> {
  const seq = ++reqSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await getUpstreamRawTools(name)
    if (seq !== reqSeq) return
    tools.value = res.tools || []
    payload.value = res
    counts.value = { ...counts.value, [name]: res.count }
  } catch (e: unknown) {
    if (seq !== reqSeq) return
    tools.value = []
    payload.value = null
    loadError.value = errorMessage(e, '请检查网关是否运行')
  } finally {
    if (seq === reqSeq) loading.value = false
  }
}

function refresh(): void {
  // 没有已选上游 = 首屏 upstreams 列表就失败了, 重试必须重走 loadUpstreams;
  // 旧实现这里静默 return, 横幅"重试"按钮是死的, 只能整页 F5。
  if (selected.value) void fetchTools(selected.value)
  else void loadUpstreams()
}

async function loadUpstreams(): Promise<void> {
  try {
    const res = await getMcpBrowserUpstreams()
    upstreams.value = res.upstreams || []
  } catch (e: unknown) {
    loadError.value = errorMessage(e, '请检查网关是否运行')
    return
  }
  // 深链里的上游名解析不到就回落到第一个 —— 不要 404。
  const wanted = typeof route.query.tab === 'string' ? route.query.tab : ''
  const resolved = upstreamOrder.value.includes(wanted) ? wanted : upstreamOrder.value[0] || ''
  if (resolved) await selectUpstream(resolved)
}

// ---- 深链 (?tab=<upstream name>) ----
function syncQuery(upstream: string): void {
  const currentUp = (route.query.tab as string | undefined) || ''
  if (currentUp === upstream) return
  const { tab: _u, ...rest } = route.query
  void router.replace({ path: '/mcp-browser', query: { ...rest, tab: upstream } })
}

watch(
  () => route.query.tab,
  (val) => {
    const name = typeof val === 'string' ? val : ''
    if (name && name !== selected.value && upstreamOrder.value.includes(name)) {
      void selectUpstream(name)
    }
  },
)

// ---- 展开 / 复制 ----
function onExpandChange(row: RawTool, expandedRows: RawTool[] | RawTool): void {
  const key = rowKeyOf(row)
  const expanded = Array.isArray(expandedRows)
    ? expandedRows.some((r) => rowKeyOf(r) === key)
    : Boolean(expandedRows)
  const next = new Set(expandedNames.value)
  if (expanded) next.add(key)
  else next.delete(key)
  expandedNames.value = next
}

function toggleDetail(row: RawTool): void {
  tableRef.value?.toggleRowExpansion(row)
}

async function copyName(name: string): Promise<void> {
  const ok = await copyText(name, '')
  if (!ok) return
  copiedName.value = name
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => {
    copiedName.value = ''
  }, 1200)
}

async function copyAll(): Promise<void> {
  const names = visibleTools.value.map(toolName).filter(Boolean)
  if (names.length === 0) {
    ElMessage.error('暂无可复制的工具')
    return
  }
  await copyText(names.join('\n'), `已复制 ${names.length} 个 tool 名`)
}

// ---- 键盘 (组件挂载=位于本页, 卸载自动失效) ----
function onKeydown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  const tag = (target?.tagName || '').toLowerCase()
  const inField = tag === 'input' || tag === 'textarea' || target?.isContentEditable

  if (event.key === '/' && !inField && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    searchInputRef.value?.focus()
    return
  }

  if (event.key === 'Escape' && (inField || filtersActive.value)) {
    event.preventDefault()
    clearFilters()
    ;(target as HTMLInputElement | null)?.blur?.()
  }
}

onMounted(() => {
  void loadUpstreams()
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  if (debounceTimer) clearTimeout(debounceTimer)
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<template>
  <div class="view-page">
    <header class="view-header">
      <h1 class="view-h1">
        MCP 浏览器
        <span v-if="currentUpstream" class="view-h1-sub">
          {{ UPSTREAM_TITLE[currentUpstream.name] || currentUpstream.name }}
          <el-tag
            size="small"
            effect="plain"
            :type="currentUpstream.available ? 'success' : 'danger'"
          >
            {{ currentUpstream.available ? currentUpstream.status : '不可用' }}
          </el-tag>
        </span>
      </h1>
      <div class="view-actions">
        <span v-if="metaText" class="view-meta">{{ metaText }}</span>
        <el-button size="small" @click="copyAll">复制全部 tool 名</el-button>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="refresh">
          刷新
        </el-button>
      </div>
    </header>

    <el-card shadow="never" class="panel-card view-body">
      <div class="mcp-layout">
      <!-- 左侧: 搜索 + 形状开关 + 上游导航 -->
      <aside class="mcp-sidebar">
        <el-input
          ref="searchInputRef"
          v-model="rawQuery"
          class="mcp-search"
          placeholder="搜索工具... (按 / 聚焦)"
          autocomplete="off"
          clearable
          :prefix-icon="Search"
        />

        <div class="mcp-toggles">
          <el-checkbox v-model="toggles.meta">
            有 _meta
            <span class="toggle-count">{{ tools.filter(hasMeta).length }}</span>
          </el-checkbox>
          <el-checkbox v-model="toggles.outputSchema">
            有 outputSchema
            <span class="toggle-count">{{ tools.filter(hasOutputSchema).length }}</span>
          </el-checkbox>
          <el-checkbox v-model="toggles.annotations">
            有 annotations
            <span class="toggle-count">{{ tools.filter((t) => annotationFlags(t).length).length }}</span>
          </el-checkbox>
        </div>

        <nav class="mcp-nav" aria-label="MCP 上游">
          <button
            v-for="item in navItems"
            :key="item.key"
            type="button"
            class="mcp-nav-item"
            :class="{ active: item.active }"
            :aria-current="item.active ? 'true' : undefined"
            @click="selectUpstream(item.key)"
          >
            <span class="dot" :class="item.available ? 'dot-online' : 'dot-offline'" />
            <span class="nav-label" :title="item.key">{{ item.title }}</span>
            <!-- 没拉过的上游不显示计数: 编造或复用陈旧数字比一个破折号更糟 -->
            <span class="nav-count">{{ item.count ?? '—' }}</span>
          </button>
        </nav>
      </aside>

      <!-- 右侧: 原始工具表 -->
      <section class="mcp-content" v-loading="loading">
        <el-alert
          v-if="loadError"
          type="error"
          :closable="false"
          show-icon
          class="mcp-banner"
          :title="`上游请求失败：${loadError}`"
        >
          <div class="mcp-error-actions">
            <el-button size="small" @click="refresh">重试</el-button>
          </div>
        </el-alert>

        <el-alert
          v-else-if="payload?.truncated"
          type="warning"
          :closable="false"
          show-icon
          class="mcp-banner"
          :title="`已达安全上限 (${payload.truncatedReason})，仅显示前 ${payload.count} 个工具`"
        />

        <!-- 上游合法地返回 0 个工具 —— 与 "请求失败" 是两回事, 文案必须说清楚 -->
        <div v-if="!loadError && !loading && tools.length === 0" class="mcp-empty">
          <div class="mcp-empty-title">上游没有返回任何工具</div>
          <div class="mcp-empty-text">
            该上游本身就没有注册工具，这不代表请求失败。
          </div>
        </div>

        <div
          v-else-if="!loadError && filtersActive && visibleTools.length === 0"
          class="mcp-empty"
        >
          <div class="mcp-empty-title">无匹配工具</div>
          <el-button size="small" @click="clearFilters">清空全部筛选</el-button>
        </div>

        <div v-else-if="!loadError" class="mcp-table-scroll">
          <el-table
            ref="tableRef"
            :key="selected"
            :data="visibleTools"
            :row-key="rowKeyOf"
            class="mcp-table"
            size="small"
            @expand-change="onExpandChange"
          >
            <el-table-column type="expand" width="38">
              <template #default="{ row }">
                <RawSchemaPanel :tool="row" />
              </template>
            </el-table-column>

            <el-table-column label="工具名" min-width="230">
              <template #default="{ row }">
                <div class="tool-name-line">
                  <span class="tool-name">
                    <HighlightText :text="toolName(row)" :query="appliedQuery" />
                  </span>
                  <el-tag
                    v-if="extraKeys(row).length"
                    size="small"
                    type="warning"
                    effect="plain"
                    :title="extraKeys(row).join(', ')"
                  >
                    +{{ extraKeys(row).length }} 字段
                  </el-tag>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="标题" min-width="120">
              <template #default="{ row }">
                <HighlightText :text="toolTitle(row) || '-'" :query="appliedQuery" />
              </template>
            </el-table-column>

            <el-table-column label="参数" min-width="220">
              <template #default="{ row }">
                <div v-if="schemaProperties(row).length" class="params-list">
                  <div v-for="p in schemaProperties(row)" :key="p.name" class="param-item">
                    <span class="param-name">{{ p.name }}</span>
                    <span v-if="p.type" class="param-type">{{ p.type }}</span>
                    <el-tag v-if="p.required" size="small" type="danger" effect="plain">
                      required
                    </el-tag>
                  </div>
                </div>
                <span v-else class="no-params">-</span>
              </template>
            </el-table-column>

            <el-table-column label="描述" min-width="240">
              <template #default="{ row }">
                <span class="desc-text">
                  <HighlightText :text="toolDescription(row)" :query="appliedQuery" />
                </span>
              </template>
            </el-table-column>

            <el-table-column label="标记" min-width="150">
              <template #default="{ row }">
                <span class="tool-badges">
                  <el-tag v-if="hasMeta(row)" size="small" type="info" effect="plain">_meta</el-tag>
                  <el-tag v-if="hasOutputSchema(row)" size="small" effect="plain">
                    outputSchema
                  </el-tag>
                  <el-tag
                    v-for="f in annotationFlags(row).filter((f) => f.value)"
                    :key="f.key"
                    size="small"
                    type="success"
                    effect="plain"
                  >
                    {{ f.label }}
                  </el-tag>
                </span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text @click="copyName(toolName(row))">
                  {{ copiedName === toolName(row) ? '已复制' : '复制' }}
                </el-button>
                <el-button size="small" text @click="toggleDetail(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </section>
    </div>
    </el-card>
  </div>
</template>

<style scoped>
/* 统一页面外壳: 标题行 + 卡片 */
/* view-* 类已抽到 web/src/styles/view-header.css (全局) */

.mcp-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.mcp-sidebar {
  flex: 0 0 264px;
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  /* 同 InterfacesView: 矮视口下侧栏内部滚动, 不把整页撑高。
     180 = 主区内距 32 + 页头 ~82 + 页面间距 20 + 卡片内距 40 + 余量 6 */
  max-height: calc(100dvh - 180px);
  overflow-y: auto;
  overscroll-behavior: contain;
}

.mcp-content {
  flex: 1;
  min-width: 0;
  min-height: 320px;
}

.mcp-banner {
  margin-bottom: 12px;
}

.mcp-error-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
}

.mcp-toggles {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.toggle-count {
  margin-left: 6px;
  font-size: 11px;
  /* placeholder 在 11px 下对比度不足, 用 regular */
  color: var(--el-text-color-regular);
}

.mcp-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 8px;
}

.mcp-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.mcp-nav-item:hover {
  background: var(--el-fill-color-light);
}

.mcp-nav-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}

.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-count {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  font-variant-numeric: tabular-nums;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 8px;
}

.dot-online {
  background: var(--el-color-success);
}

.dot-offline {
  background: var(--el-color-danger);
}

.mcp-table-scroll {
  overflow-x: auto;
}

.tool-name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tool-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.tool-badges {
  display: inline-flex;
  gap: 4px;
  flex-wrap: wrap;
}

.desc-text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.params-list {
  font-size: 12px;
}

.param-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  margin-bottom: 2px;
}

.param-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--el-color-primary);
}

.param-type {
  color: var(--el-color-success);
  font-size: 11px;
}

.no-params {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.mcp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 16px;
  color: var(--el-text-color-secondary);
}

.mcp-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.mcp-empty-text {
  font-size: 12px;
}

@media (max-width: 1100px) {
  .mcp-sidebar {
    flex: 0 0 220px;
  }
}

@media (max-width: 900px) {
  .mcp-layout {
    flex-direction: column;
  }

  .mcp-sidebar {
    flex: none;
    width: 100%;
    position: static;
  }
}
</style>
