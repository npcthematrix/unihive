<template>
  <div class="view-page">
    <!-- 标题 + 子标签整体吸顶: 面板内容很长, 滚到下半屏仍可直接切 tab -->
    <div class="cfg-sticky-head">
      <header class="view-header">
        <h1 class="view-h1">OMNI板块管理</h1>
        <div class="view-actions">
          <span
            class="last-refresh"
            :class="{ failed: loadError }"
          >{{ refreshLabel }}</span>
          <el-button size="small" :icon="Refresh" @click="refreshAll" :loading="manualLoading">
            立即刷新
          </el-button>
        </div>
      </header>

      <!-- 子标签导航 — WAI-ARIA Tabs: roving tabindex + 方向键 -->
      <nav
        class="cfg-subtabs"
        role="tablist"
        aria-label="OMNI 子标签"
        @keydown="onTablistKeydown"
      >
        <button
          v-for="tab in TABS"
          :id="`omni-tab-${tab.key}`"
          :key="tab.key"
          type="button"
          class="cfg-subtab"
          :class="{ active: activeTab === tab.key }"
          role="tab"
          :aria-selected="activeTab === tab.key"
          :aria-controls="`omni-panel-${tab.key}`"
          :tabindex="activeTab === tab.key ? 0 : -1"
          @click="selectTab(tab.key)"
        >{{ tab.title }}</button>
      </nav>
    </div>

    <!-- 面板直接铺在页面底色上, 不再套一层 el-card —— 与「配置信息」「网关
         运行监控」同构。外壳卡片会把面板内容整体内缩 20px (另两页为 0),
         切换页面时内容左缘对不齐; 且 StatsPanel 自己的 stat-card 本就是白底
         描边卡, 套在白色外壳里等于白压白。 -->
    <div class="cfg-panel-stack view-body">
      <!-- 3 个 panel, 切换只显示 active -->
      <!-- 首屏加载由各 panel 内部 skeleton 承担, 不套外层遮罩 (避免 mask + skeleton 同屏) -->
      <section
        v-show="activeTab === 'stats'"
        id="omni-panel-stats"
        role="tabpanel"
        aria-labelledby="omni-tab-stats"
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
        id="omni-panel-query"
        role="tabpanel"
        aria-labelledby="omni-tab-query"
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
        id="omni-panel-sync"
        role="tabpanel"
        aria-labelledby="omni-tab-sync"
      >
        <SyncPanel
          ref="syncPanelRef"
          :recent-logs="recentLogs"
          :loading="loading"
          @synced="onSyncCompleted"
          @poll="onPoll"
        />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import type {
  OmniSector,
  OmniSource,
  OmniStatRow,
  OmniStatsResponse,
  OmniSyncLog,
} from '@/api/omni'
import {
  getOmniStats,
  queryOmniSectors,
  type OmniQueryParams,
} from '@/api/omni'
import { errorMessage, isCanceledError } from '@/api/client'
import { formatLastRefreshLabel } from '@/utils/time'
import { scrollPageTop } from '@/utils/scrollTop'
import { TAB_KEYS, TAB_TITLES, type OmniTabKey } from './omni/format'
import { nextTabKey, parseHashTab } from './_shared/hashTab'
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
/** 是否已经成功落过一次 stats —— 不能用 stats.length 判定 (空库时永远为 false,
 *  导致每次切 tab 都重拉)。 */
const statsLoaded = ref(false)

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

// ===== stats 加载 =====
// single-flight: 非 force 调用共享同一个 in-flight; force 走独立请求, 用
// generation 计数做归属守卫 —— 否则并发 force 时旧响应可能覆盖新响应、
// 并提前把 loading 复位。
let statsInflight: Promise<void> | null = null
let statsGeneration = 0

/**
 * 落盘 stats 响应。
 * @param keepOnFailure 失败时保留上一次的有效数据 (轮询路径用), 避免一次
 *   瞬时失败把已展示的统计清空。
 */
function applyStats(d: OmniStatsResponse, keepOnFailure = false): void {
  if (d.status === 'failed') {
    loadError.value = d.message || 'OMNI 后端返回失败'
    if (!keepOnFailure) {
      stats.value = []
      recentLogs.value = []
    }
    return
  }
  loadError.value = ''
  stats.value = d.stats || []
  recentLogs.value = d.recent_logs || []
  lastRefresh.value = Date.now()
  // 只有真正拿到数据才算「拉过」; 失败时保持 false, 让下次切 tab 再试一次。
  statsLoaded.value = true
}

async function loadStats(force = false) {
  if (statsInflight && !force) return statsInflight
  const gen = ++statsGeneration
  const promise = (async () => {
    if (force) manualLoading.value = true
    else loading.value = true
    if (force) loadError.value = ''
    try {
      const d = await getOmniStats()
      if (gen !== statsGeneration) return // 已被更新的请求取代
      applyStats(d)
    } catch (e) {
      if (gen !== statsGeneration) return
      loadError.value = errorMessage(e, '加载 OMNI 状态失败')
    } finally {
      if (gen === statsGeneration) {
        loading.value = false
        manualLoading.value = false
        statsInflight = null
      }
    }
  })()
  statsInflight = promise
  return promise
}

async function refreshAll() {
  // 刷新保留当前查询页；写死第 1 页会把翻到后半的用户静默拉回。
  await Promise.all([
    loadStats(true),
    hasSearched.value ? runQuery(queryPage.value, 'refresh') : Promise.resolve(),
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
    // 后端业务失败是 HTTP 200 + {status:'failed',message}，需显式提示而非空表。
    if (d?.status === 'failed') {
      queryError.value = d.message || '查询失败'
      sectors.value = []
      return
    }
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

/** 轮询已带回 payload: 直接落盘, 不再重复 GET, 也不点亮顶栏 loading。 */
function onPoll(payload: OmniStatsResponse): void {
  applyStats(payload, true)
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

function onTablistKeydown(e: KeyboardEvent): void {
  const target = nextTabKey(TAB_KEYS, activeTab.value, e.key)
  if (!target) return
  e.preventDefault()
  void selectTab(target)
  void nextTick(() => {
    document.getElementById(`omni-tab-${target}`)?.focus()
  })
}

async function selectTab(tab: OmniTabKey): Promise<void> {
  if (activeTab.value === tab) return
  activeTab.value = tab
  void router.replace({ query: { ...route.query, tab } })
  // 三个 panel 长短差异大 (统计网格 / 查询表 / 同步表单), 不重置滚动会落到
  // 新面板中段; 与配置信息 / 接入指南一致。
  scrollPageTop()

  // 离开查询页时收起成分股弹窗 —— 弹窗 teleport 到 body, v-show 藏不住它。
  // 注意不要在这里取消同步: v-show 下 SyncPanel 保持挂载, 轮询要继续跑
  // 才能让「中止等待 / 接管」态收敛。
  if (tab !== 'query') queryPanelRef.value?.closeModal()

  if (tab === 'stats' || tab === 'sync') {
    // 共享 stats 数据, 切换 tab 不重新拉 (除非从未拉过)
    if (!statsLoaded.value) await loadStats()
  }
}

const refreshLabel = computed(() =>
  formatLastRefreshLabel(loadError.value, lastRefresh.value),
)

onMounted(() => {
  // 首屏只加载 stats (query 等待用户操作)
  void loadStats()
})

onBeforeUnmount(() => {
  // 卸载时取消所有 in-flight 请求, 避免响应写回已卸载组件
  queryAbort?.abort()
  syncPanelRef.value?.cancelInFlight(true)
  queryPanelRef.value?.closeModal()
})
</script>
