<template>
  <div class="view-page">
    <header class="view-header">
      <h1 class="view-h1">MCP APIs</h1>
      <div class="view-actions">
        <span class="ifs-meta">{{ metaText }}</span>
        <span v-if="timestampText" class="view-time">{{ timestampText }}</span>
        <el-button size="small" @click="copyAll">复制全部 tool 名</el-button>
      </div>
    </header>

    <el-card shadow="never" class="panel-card">
      <div class="ifs-layout">
      <!-- 左侧: 搜索 + 过滤器 + 分组导航 -->
      <aside class="ifs-sidebar">
        <el-input
          ref="searchInputRef"
          v-model="rawQuery"
          class="ifs-search"
          placeholder="搜索工具... (按 / 聚焦)"
          autocomplete="off"
          clearable
          :prefix-icon="Search"
        />

        <div class="ifs-toggles">
          <el-checkbox v-model="toggles.dangerous">
            仅显示危险工具
            <span class="toggle-count">{{ countDangerous }}</span>
          </el-checkbox>
          <el-checkbox v-model="toggles.cache">
            仅显示有缓存
            <span class="toggle-count">{{ countCache }}</span>
          </el-checkbox>
          <el-checkbox v-model="toggles.noparam">
            仅显示无参数
            <span class="toggle-count">{{ countNoParam }}</span>
          </el-checkbox>
        </div>

        <el-radio-group v-model="dataSourceFilter" size="small" class="dst-chips">
          <span class="chips-label">在线状态:</span>
          <el-radio-button value="">全部 {{ tools.length }}</el-radio-button>
          <el-radio-button value="online">在线 {{ countOnline }}</el-radio-button>
          <el-radio-button value="offline">离线 {{ countOffline }}</el-radio-button>
          <el-radio-button value="mixed">混合 {{ countMixed }}</el-radio-button>
        </el-radio-group>

        <nav class="ifs-nav" aria-label="接口分组">
          <button
            v-for="item in navItems"
            :key="item.key"
            type="button"
            class="ifs-nav-item"
            :class="{ active: item.active }"
            :disabled="item.disabled"
            :aria-current="item.active ? 'true' : undefined"
            @click="selectGroup(item.key)"
          >
            <span v-if="item.key !== ALL_KEY" class="dot" :class="dotClass(item.key)" />
            <span class="nav-label" :title="item.title">{{ item.title }}</span>
            <span class="nav-count" :class="item.countCls">{{ item.countText }}</span>
          </button>
        </nav>
      </aside>

      <!-- 右侧: 工具表 -->
      <section class="ifs-content" v-loading="loading">
        <el-alert
          v-if="loadError"
          type="error"
          :closable="false"
          show-icon
          class="load-error"
          :title="`加载失败：${loadError}`"
        >
          <el-button size="small" @click="load">重试</el-button>
        </el-alert>

        <div v-else-if="filtersActive && visibleTools.length === 0" class="ifs-empty">
          <div class="ifs-empty-icon">无结果</div>
          <div class="ifs-empty-text">此分组无匹配工具</div>
          <el-button size="small" @click="clearFilters">清空全部筛选</el-button>
        </div>

        <el-table
          v-else
          ref="tableRef"
          :data="visibleTools"
          row-key="name"
          class="ifs-table"
          size="small"
          @expand-change="onExpandChange"
        >
          <el-table-column type="expand" width="38">
            <template #default="{ row }">
              <ToolDetailPanel :tool="row" />
            </template>
          </el-table-column>

          <el-table-column label="接口" min-width="220">
            <template #default="{ row }">
              <div class="tool-name-line">
                <span class="tool-name"><HighlightText :text="row.name" :query="appliedQuery" /></span>
                <span class="tool-badges">
                  <el-tag v-if="row.dangerous" size="small" type="danger" effect="plain">
                    dangerous
                  </el-tag>
                  <el-tag
                    v-if="row.data_source_type"
                    size="small"
                    effect="plain"
                    :type="dstTagType(row.data_source_type)"
                  >
                    {{ row.data_source_type }}
                  </el-tag>
                  <el-tag
                    v-if="row.cache_ttl_key"
                    size="small"
                    type="warning"
                    effect="plain"
                    :title="`cache_ttl_key=${row.cache_ttl_key}`"
                  >
                    {{ row.cache_ttl_key }}
                  </el-tag>
                </span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="描述" min-width="320">
            <template #default="{ row }">
              <div class="tool-desc">
                <HighlightText :text="row.description || '-'" :query="appliedQuery" />
              </div>
              <div v-if="paramPreview(row).length" class="tool-params">
                <HighlightText :text="paramPreview(row).slice(0, 3).join(', ')" :query="appliedQuery" />
                <span v-if="paramPreview(row).length > 3" class="params-more">
                  +{{ paramPreview(row).length - 3 }} more
                </span>
              </div>
            </template>
          </el-table-column>

          <el-table-column v-if="activeGroup === ALL_KEY" width="150" label="上游分组">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                :disabled="!canJump(row.source)"
                @click="selectGroup(row.source)"
              >
                {{ row.source || '-' }} →
              </el-button>
            </template>
          </el-table-column>

          <el-table-column width="120" label="操作">
            <template #default="{ row }">
              <el-button link size="small" @click="copyName(row.name)">
                {{ copiedName === row.name ? '已复制' : '复制' }}
              </el-button>
              <el-button
                link
                size="small"
                :type="expandedNames.has(row.name) ? 'info' : 'primary'"
                @click="toggleDetail(row)"
              >
                {{ expandedNames.has(row.name) ? '收起' : '详情' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { TableInstance } from 'element-plus'
import { getInterfaces, type DataSourceType, type ToolSpec } from '@/api/interfaces'
import { getStatus, type UpstreamStatusValue } from '@/api/status'
import { copyText } from '@/utils/clipboard'
import HighlightText from '@/components/HighlightText.vue'
import ToolDetailPanel from './interfaces/ToolDetailPanel.vue'
import {
  ALL_KEY,
  GROUP_TITLE,
  applyToolsFilter,
  bucketByGroup,
  buildGroupOrder,
  firstGroupWithMatch,
  hasActiveFilters,
  hasCache,
  hasNoParams,
  previewParamNames,
  type ToolToggles,
} from './interfaces/tools'

const route = useRoute()
const router = useRouter()

const tools = ref<ToolSpec[]>([])
const statusMap = ref<Record<string, UpstreamStatusValue>>({})
const loading = ref(false)
const loadError = ref('')
const timestamp = ref<number | null>(null)

const rawQuery = ref('')
const appliedQuery = ref('')
const toggles = reactive<ToolToggles>({ dangerous: false, cache: false, noparam: false })
const dataSourceFilter = ref<DataSourceType | ''>('')
const activeGroup = ref<string>(ALL_KEY)

const expandedNames = ref<Set<string>>(new Set())
const copiedName = ref('')
const searchInputRef = ref<{ focus: () => void } | null>(null)
const tableRef = ref<TableInstance | null>(null)

let debounceTimer: ReturnType<typeof setTimeout> | null = null
let copiedTimer: ReturnType<typeof setTimeout> | null = null

const groupOrder = computed(() =>
  buildGroupOrder([...new Set(tools.value.map((t) => t.source).filter(Boolean))]),
)

const filtersActive = computed(() =>
  hasActiveFilters(appliedQuery.value, toggles, dataSourceFilter.value),
)

const filteredTools = computed(() =>
  applyToolsFilter(tools.value, appliedQuery.value, toggles, dataSourceFilter.value),
)

const filteredBuckets = computed(() => bucketByGroup(filteredTools.value))
const allBuckets = computed(() => bucketByGroup(tools.value))

const visibleTools = computed(() =>
  activeGroup.value === ALL_KEY
    ? filteredTools.value
    : filteredBuckets.value.get(activeGroup.value) || [],
)

const timestampText = computed(() =>
  timestamp.value ? new Date(timestamp.value * 1000).toLocaleTimeString('zh-CN') : '',
)

const metaText = computed(() => `${GROUP_TITLE[activeGroup.value] ?? activeGroup.value} · ${visibleTools.value.length} 个工具`)

// ---- 左侧导航 ----
interface NavItem {
  key: string
  title: string
  active: boolean
  disabled: boolean
  countText: string
  countCls: string
}

const navItems = computed<NavItem[]>(() => {
  const keys = [ALL_KEY, ...groupOrder.value]
  return keys.map((key) => {
    const total =
      key === ALL_KEY ? tools.value.length : (allBuckets.value.get(key) || []).length
    const matched =
      key === ALL_KEY ? filteredTools.value.length : (filteredBuckets.value.get(key) || []).length
    const filtering = filtersActive.value
    return {
      key,
      title: GROUP_TITLE[key] ?? key,
      active: key === activeGroup.value,
      disabled: filtering && matched === 0,
      countText: filtering && total > 0 ? `${matched}/${total}` : `${total}`,
      countCls:
        filtering && matched === 0 ? 'zero' : filtering && matched < total ? 'has-match' : '',
    }
  })
})

function canJump(source: string): boolean {
  return Boolean(source) && groupOrder.value.includes(source)
}

function dotClass(key: string): string {
  switch (statusMap.value[key]) {
    case 'online':
      return 'dot-online'
    case 'degraded':
      return 'dot-degraded'
    case 'offline':
    case 'unknown':
      return 'dot-offline'
    default:
      return 'dot-configured'
  }
}

// ---- 计数 ----
const countDangerous = computed(() => tools.value.filter((t) => t.dangerous).length)
const countCache = computed(() => tools.value.filter(hasCache).length)
const countNoParam = computed(() => tools.value.filter(hasNoParams).length)
const countOnline = computed(() => tools.value.filter((t) => t.data_source_type === 'online').length)
const countOffline = computed(() => tools.value.filter((t) => t.data_source_type === 'offline').length)
const countMixed = computed(() => tools.value.filter((t) => t.data_source_type === 'mixed').length)

function dstTagType(dst: DataSourceType): 'success' | 'info' | 'warning' {
  if (dst === 'online') return 'success'
  if (dst === 'offline') return 'info'
  return 'warning'
}

// 每个 row 在模板里被访问 4 次 (slice + length + 更多), 这里 memo 一份。
const paramPreviews = computed(() => {
  const m = new Map<string, string[]>()
  for (const t of visibleTools.value) m.set(t.name, previewParamNames(t))
  return m
})

function paramPreview(tool: ToolSpec): string[] {
  return paramPreviews.value.get(tool.name) || []
}

// ---- 搜索 / 分组 / 深链 ----
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

watch(appliedQuery, () => {
  // 旧版仅搜索触发自动切组 (开关/chip 不触发)
  if (!appliedQuery.value) return
  if (activeGroup.value !== ALL_KEY) {
    const current = filteredBuckets.value.get(activeGroup.value) || []
    if (current.length > 0) return
  }
  activeGroup.value = firstGroupWithMatch(filteredBuckets.value, groupOrder.value)
})

watch(activeGroup, (key) => {
  // 不按 group 重挂载 el-table (无 :key): 行对象引用稳定 (filter 不拷贝),
  // el-table 的展开态以 row 对象为准, 切组/过滤后再回来自动恢复展开。
  syncQuery(key)
})

// 仅在"全量工具清单"里消失的 name 才剪掉 (数据刷新后某工具真的不存在了)。
// 不能按当前可见行裁剪 —— 否则切分组/勾过滤会把跨分组保留的展开态永久删掉。
watch(tools, (all) => {
  const live = new Set(all.map((r) => r.name))
  let changed = false
  for (const name of [...expandedNames.value]) {
    if (!live.has(name)) {
      expandedNames.value.delete(name)
      changed = true
    }
  }
  if (changed) expandedNames.value = new Set(expandedNames.value)
}, { flush: 'post' })

watch(
  () => route.query.tab,
  (val) => {
    const key = typeof val === 'string' ? val : ''
    if (!key) {
      if (activeGroup.value !== ALL_KEY) activeGroup.value = ALL_KEY
      return
    }
    if (key !== activeGroup.value && (key === ALL_KEY || groupOrder.value.includes(key))) {
      activeGroup.value = key
    }
  },
)

function syncQuery(key: string): void {
  const wanted = key === ALL_KEY ? '' : key
  const current = (route.query.tab as string | undefined) || ''
  if (current === wanted) return
  // 仅改 tab slot, 保留其他 query (如过滤参数)
  const { tab: _drop, ...rest } = route.query
  const next = wanted ? { ...rest, tab: wanted } : rest
  void router.replace({ path: '/interfaces', query: next })
}

function selectGroup(key: string): void {
  activeGroup.value = key
}

/** 清空全部筛选 (搜索词 + 3 个 toggle + 在线状态), 纯 toggle 造成的空态也能恢复。 */
function clearFilters(): void {
  rawQuery.value = ''
  appliedQuery.value = ''
  toggles.dangerous = false
  toggles.cache = false
  toggles.noparam = false
  dataSourceFilter.value = ''
  searchInputRef.value?.focus()
}

// ---- 展开 / 复制 ----
function onExpandChange(row: ToolSpec, expandedRows: ToolSpec[] | ToolSpec): void {
  const expanded = Array.isArray(expandedRows)
    ? expandedRows.some((r) => r.name === row.name)
    : Boolean(expandedRows)
  const next = new Set(expandedNames.value)
  if (expanded) next.add(row.name)
  else next.delete(row.name)
  expandedNames.value = next
}

function toggleDetail(row: ToolSpec): void {
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
  const names = visibleTools.value.map((t) => t.name)
  if (names.length === 0) {
    ElMessage.error('暂无可复制的工具')
    return
  }
  await copyText(names.join('\n'), `已复制 ${names.length} 个 tool 名`)
}

// ---- 数据加载 ----
async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  const [ifsRes, statusRes] = await Promise.allSettled([getInterfaces(), getStatus()])
  if (statusRes.status === 'fulfilled') {
    const map: Record<string, UpstreamStatusValue> = {}
    for (const [name, info] of Object.entries(statusRes.value.upstreams)) {
      map[name] = info.status
    }
    statusMap.value = map
  }
  if (ifsRes.status === 'rejected') {
    loadError.value =
      ifsRes.reason instanceof Error ? ifsRes.reason.message : '请检查网关是否运行'
    loading.value = false
    return
  }
  tools.value = ifsRes.value.tools || []
  // 刷新后是一批全新 row 对象, el-table 内部展开态必然全部折叠;
  // 同步清空 name 集, 避免按钮文案残留为"收起"。
  expandedNames.value = new Set()
  timestamp.value = ifsRes.value.timestamp
  const wanted = typeof route.query.tab === 'string' ? route.query.tab : ''
  activeGroup.value = wanted === ALL_KEY || groupOrder.value.includes(wanted) ? wanted : ALL_KEY
  loading.value = false
}

// ---- 键盘快捷键 (组件挂载=位于接口页, 卸载自动失效) ----
function onKeydown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  const tag = (target?.tagName || '').toLowerCase()
  const inField = tag === 'input' || tag === 'textarea' || target?.isContentEditable

  if (event.key === '/' && !inField && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    searchInputRef.value?.focus()
    return
  }

  if (event.key === 'Escape') {
    if (inField || filtersActive.value) {
      event.preventDefault()
      clearFilters()
      ;(target as HTMLInputElement | null)?.blur?.()
    }
    return
  }

  if (inField) return
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
  event.preventDefault()
  const enabled = navItems.value.filter((item) => !item.disabled).map((item) => item.key)
  if (enabled.length === 0) return
  const idx = enabled.indexOf(activeGroup.value)
  const nextIdx =
    event.key === 'ArrowDown'
      ? Math.min(enabled.length - 1, idx + 1)
      : Math.max(0, idx - 1)
  if (enabled[nextIdx] && enabled[nextIdx] !== activeGroup.value) {
    selectGroup(enabled[nextIdx])
  }
}

onMounted(() => {
  void load()
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  if (debounceTimer) clearTimeout(debounceTimer)
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<style scoped>
/* 统一页面外壳: 标题行 + 卡片 */
/* view-* 类已抽到 web/src/styles/view-header.css (全局) */

.view-time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* .panel-card 圆角已收敛到 styles/main.css (全局) */

.ifs-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ifs-layout {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.ifs-sidebar {
  flex: 0 0 264px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: sticky;
  top: 0;
  /* 矮视口下侧栏 (筛选卡 + 289 工具分组导航) 会比视口高, sticky 失效且
     整页被侧栏撑高; 限制为视口高并在侧栏内部滚动。 */
  max-height: calc(100vh - 24px);
  overflow-y: auto;
  overscroll-behavior: contain;
}

.ifs-toggles {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.toggle-count {
  margin-left: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.dst-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.chips-label {
  flex: 1 1 100%;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
}

.ifs-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ifs-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--el-text-color-regular);
  text-align: left;
}

.ifs-nav-item:hover:not(:disabled) {
  background: var(--el-fill-color-light);
}

.ifs-nav-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}

.ifs-nav-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.nav-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.nav-count.has-match {
  color: var(--el-color-primary);
}

.nav-count.zero {
  color: var(--el-text-color-placeholder);
}

.dot {
  flex: 0 0 8px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.dot-online {
  background: var(--el-color-success);
}

.dot-degraded {
  background: var(--el-color-warning);
}

.dot-offline {
  background: var(--el-color-danger);
}

.dot-configured {
  background: var(--el-text-color-placeholder);
}

.ifs-content {
  flex: 1;
  min-width: 0;
  min-height: 300px;
}

.tool-name-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.tool-name {
  font-weight: 600;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 12.5px;
}

.tool-badges {
  display: inline-flex;
  gap: 4px;
  flex-wrap: wrap;
}

.tool-desc {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.tool-params {
  margin-top: 3px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: 'JetBrains Mono', 'Consolas', monospace;
}

.params-more {
  margin-left: 6px;
  color: var(--el-text-color-placeholder);
}

.ifs-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 60px 0;
  color: var(--el-text-color-secondary);
}

.ifs-empty-icon {
  font-size: 13px;
  padding: 4px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 999px;
}

.load-error {
  margin-bottom: 10px;
}

@media (max-width: 1100px) {
  .ifs-sidebar {
    flex: 0 0 220px;
  }
}

@media (max-width: 900px) {
  .ifs-layout {
    flex-direction: column;
  }

  .ifs-sidebar {
    flex-basis: auto;
    width: 100%;
    position: static;
  }

  .ifs-table {
    overflow-x: auto;
  }
}
</style>
