<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">OMNI板块管理</h2>
        <div class="toolbar-actions">
          <span
            class="last-refresh"
            :class="{ failed: loadError }"
          >{{ refreshLabel }}</span>
          <el-button :icon="Refresh" @click="refreshAll" :loading="manualLoading">
            立即刷新
          </el-button>
        </div>
      </div>
    </template>

    <!-- 子标签导航 -->
    <nav class="cfg-subtabs" role="tablist" aria-label="OMNI 子标签">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        class="cfg-subtab"
        :class="{ active: activeTab === tab.key }"
        role="tab"
        :aria-selected="activeTab === tab.key"
        @click="selectTab(tab.key)"
      >{{ tab.title }}</button>
    </nav>

    <!-- 3 个 panel, 切换只显示 active -->
    <div v-loading="loading && !hasData" class="omni-panel-stack">
      <section
        v-show="activeTab === 'stats'"
        class="cfg-panel"
        aria-label="数据统计"
      >
        <StatsPanel
          :stats="stats"
          :recent-logs="recentLogs"
          :loading="loading"
          :load-error="loadError"
          @reload="loadStats(true)"
        />
      </section>

      <section
        v-show="activeTab === 'query'"
        class="cfg-panel"
        aria-label="板块查询"
      >
        <QueryPanel
          ref="queryPanelRef"
          :sectors="sectors"
          :total-count="totalCount"
          :page="queryPage"
          :page-size="queryPageSize"
          :loading="queryLoading"
          :load-error="queryError"
          :has-searched="hasSearched"
          :query-filters="queryFilters"
          @search="onSearch"
          @page-change="onPageChange"
        />
      </section>

      <section
        v-show="activeTab === 'sync'"
        class="cfg-panel"
        aria-label="同步管理"
      >
        <SyncPanel
          ref="syncPanelRef"
          :recent-logs="recentLogs"
          :loading="loading"
          @synced="onSyncCompleted"
        />
      </section>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import type {
  OmniSector,
  OmniSource,
  OmniStatRow,
  OmniSyncLog,
} from '@/api/omni'
import {
  getOmniStats,
  queryOmniSectors,
  type OmniQueryParams,
} from '@/api/omni'
import { errorMessage, isCanceledError } from '@/api/client'
import { formatHMS } from '@/utils/time'
import { TAB_KEYS, TAB_TITLES, type OmniTabKey } from './omni/format'
import { parseHashTab } from './_shared/hashTab'
import StatsPanel from './omni/StatsPanel.vue'
import QueryPanel from './omni/QueryPanel.vue'
import SyncPanel from './omni/SyncPanel.vue'

const route = useRoute()
const router = useRouter()

const activeTab = ref<OmniTabKey>('stats')

const stats = ref<OmniStatRow[]>([])
const recentLogs = ref<OmniSyncLog[]>([])
const loading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
const lastRefresh = ref<number | null>(null)

const sectors = ref<OmniSector[]>([])
const totalCount = ref(0)
const queryPage = ref(1)
const queryPageSize = ref(50)
const queryLoading = ref(false)
const queryError = ref('')
const hasSearched = ref(false)
const queryFilters = ref<{ source: OmniSource; boardType: string; keyword: string }>({
  source: '',
  boardType: '',
  keyword: '',
})

const hasData = computed(
  () => stats.value.length > 0 || recentLogs.value.length > 0,
)

// ===== single-flight: stats 多个 tab 共享同一个 in-flight =====
let statsInflight: Promise<void> | null = null

async function loadStats(force = false) {
  if (statsInflight && !force) return statsInflight
  const promise = (async () => {
    if (force) manualLoading.value = true
    else loading.value = true
    loadError.value = ''
    try {
      const d = await getOmniStats()
      if (d.status === 'failed') {
        loadError.value = d.message || 'OMNI 后端返回失败'
        stats.value = []
        recentLogs.value = []
      } else {
        stats.value = d.stats || []
        recentLogs.value = d.recent_logs || []
        lastRefresh.value = Date.now()
      }
    } catch (e) {
      loadError.value = errorMessage(e, '加载 OMNI 状态失败')
    } finally {
      loading.value = false
      manualLoading.value = false
      statsInflight = null
    }
  })()
  statsInflight = promise
  return promise
}

async function refreshAll() {
  await Promise.all([
    loadStats(true),
    hasSearched.value ? runQuery(1, 'refresh') : Promise.resolve(),
  ])
}

// ===== query =====

type QueryTrigger = 'user' | 'page' | 'refresh'

let queryAbort: AbortController | null = null

/** 从 queryFilters 构建请求参数 (空值不发送, 后端零值不会干扰默认行为)。 */
function buildParams(page: number): OmniQueryParams {
  const params: OmniQueryParams = {
    page,
    page_size: queryPageSize.value,
  }
  const f = queryFilters.value
  if (f.source) params.source = f.source
  if (f.boardType) params.board_type = f.boardType as OmniQueryParams['board_type']
  if (f.keyword) params.keyword = f.keyword
  return params
}

async function runQuery(page: number, trigger: QueryTrigger) {
  queryError.value = ''
  // 取消上一次 in-flight
  queryAbort?.abort()
  const ac = new AbortController()
  queryAbort = ac
  queryLoading.value = true
  try {
    const params = buildParams(page)
    // 只有用户主动搜索才算"已搜索" — 翻页/顶栏刷新不算, refreshAll 已守门
    if (trigger === 'user') hasSearched.value = true
    const d = await queryOmniSectors(params, ac.signal)
    if (ac.signal.aborted) return
    // 后端偶发返回 null (历史缩进 bug 修复前已有, 留兜底避免 .sectors 读取崩溃)。
    sectors.value = d?.sectors || []
    const tc = d?.total_count
    totalCount.value = typeof tc === 'number' ? tc : (d?.total ?? sectors.value.length)
    queryPage.value = d?.page ?? page
  } catch (e) {
    if (isCanceledError(e)) return
    queryError.value = errorMessage(e, '查询失败')
  } finally {
    // 归属守卫: 被新请求取代的陈旧请求落地时, 不得清掉新请求的 loading/abort
    if (queryAbort === ac) {
      queryAbort = null
      queryLoading.value = false
    }
  }
}

function onSearch(params: OmniQueryParams) {
  queryFilters.value = {
    source: (params.source || '') as OmniSource,
    boardType: (params.board_type || '') as string,
    keyword: params.keyword || '',
  }
  void runQuery(1, 'user')
}

function onPageChange(page: number) {
  void runQuery(page, 'page')
}

// ===== sync =====

async function onSyncCompleted() {
  // 同步完成后立即拉一次最新 stats (sync tab + stats tab 都拿到新数据)
  await loadStats(true)
}

const queryPanelRef = ref<InstanceType<typeof QueryPanel> | null>(null)
const syncPanelRef = ref<InstanceType<typeof SyncPanel> | null>(null)

// ===== tab 路由 =====

watch(
  () => route.query.tab,
  (q: LocationQueryValue | LocationQueryValue[]) => {
    activeTab.value = parseHashTab(q, TAB_KEYS, 'stats')
  },
  { immediate: true },
)

const TABS = computed(() =>
  (TAB_KEYS as readonly OmniTabKey[]).map((key) => ({
    key,
    title: TAB_TITLES[key],
  })),
)

async function selectTab(tab: OmniTabKey): Promise<void> {
  if (activeTab.value === tab) return
  activeTab.value = tab
  void router.replace({ query: { ...route.query, tab } })

  if (tab === 'stats' || tab === 'sync') {
    // 共享 stats 数据, 切换 tab 不重新拉 (除非从未拉过)
    if (!hasData.value) await loadStats()
  } else if (tab === 'query') {
    // query tab 默认显示空状态, 不主动拉 (用户点查询才拉)
  }
}

const refreshLabel = computed(() => {
  if (loadError.value) return '加载失败'
  if (!lastRefresh.value) return '尚未加载'
  return `更新于 ${formatHMS(lastRefresh.value)}`
})

onMounted(() => {
  // 首屏只加载 stats (query 等待用户操作)
  void loadStats()
})

onBeforeUnmount(() => {
  // 卸载时取消所有 in-flight 请求, 避免响应写回已卸载组件
  queryAbort?.abort()
  syncPanelRef.value?.cancelInFlight()
  queryPanelRef.value?.closeModal()
})

defineExpose({ refreshAll })
</script>

<style scoped>
.omni-panel-stack {
  min-height: 240px;
}
</style>
