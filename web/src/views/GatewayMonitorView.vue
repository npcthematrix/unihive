<template>
  <div class="view-page">
    <!-- 页头 + 子标签共用一个吸顶容器 —— 与「配置信息」「OMNI」「接入指南」
         同构。原先这里用 el-tabs, 标题随内容滚走、只有标签条吸顶, 另外三页
         却是"标题 + 标签一起吸顶", 换页时页面框架会跳。 -->
    <div class="cfg-sticky-head">
      <header class="view-header">
        <h1 class="view-h1">网关运行监控</h1>
        <div class="view-actions">
          <span
            class="last-refresh"
            :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
            :title="loadError || undefined"
          >
            {{ refreshLabel }}
          </span>
          <el-button size="small" :icon="Refresh" :loading="manualLoading" @click="load(true)">
            立即刷新
          </el-button>
          <el-tooltip :content="autoRefreshHint" placement="top">
            <span class="auto-toggle">
              <el-switch v-model="autoRefresh" />
              <span class="auto-label">自动刷新</span>
            </span>
          </el-tooltip>
        </div>
      </header>

      <!-- 子标签 — WAI-ARIA Tabs: roving tabindex + 方向键, 与另三页同款。
           顶层面板带 tabindex="0" (Tab 从标签条出来先落进面板, 读屏才播报
           tabpanel 标题); 隐藏面板由 v-show 置 display:none, 不可聚焦。 -->
      <nav
        class="cfg-subtabs"
        role="tablist"
        aria-label="监控子标签"
        @keydown="onTablistKeydown"
      >
        <button
          v-for="tab in TABS"
          :id="`monitor-tab-${tab.key}`"
          :key="tab.key"
          type="button"
          class="cfg-subtab"
          :class="{ active: activeTab === tab.key }"
          role="tab"
          :aria-selected="activeTab === tab.key"
          :aria-controls="`monitor-panel-${tab.key}`"
          :tabindex="activeTab === tab.key ? 0 : -1"
          @click="selectTab(tab.key)"
        >
          {{ tab.title }}
          <!-- 排队数徽标: 只在并发标签上, 且有排队时才出现。chip 样式与
               「配置信息」的计数角标共用 (main.css .cfg-subtab-count.is-attention),
               但语义不同 —— 那边灰底是条目总数, 这里告警色才是「有待处理」。 -->
          <span v-if="tab.key === 'pipeline' && totalQueued > 0" class="cfg-subtab-count is-attention">
            {{ totalQueued }}
          </span>
        </button>
      </nav>
    </div>

    <div class="cfg-panel-stack view-body">
      <!-- ===== 上游总览 (默认, 不自动刷新) ===== -->
      <section
        v-show="activeTab === 'overview'"
        id="monitor-panel-overview"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="monitor-tab-overview"
      >
        <!-- L1: 首拉失败时旧码这里什么都不渲染 (提示条 v-if 要求已加载过),
             只剩一张空表 + 「暂无匹配的上游」, 把"没加载"说成"没匹配"。 -->
        <el-alert
          v-if="!overviewEverLoaded"
          type="warning"
          title="上游状态尚未加载"
          :description="overviewPendingText"
          show-icon
          :closable="false"
          class="overview-stale-alert"
        />
        <el-alert
          v-else
          type="info"
          :title="`本页数据已冻结 · ${overviewLabel} · 需要新数据请点「刷新」`"
          show-icon
          :closable="false"
          class="overview-stale-alert"
        />
        <UpstreamOverviewPanel
          :rows="upstreamRows"
          :summary="upstreamSummary"
          :loading="overviewLoading"
          :ever-loaded="overviewEverLoaded"
          @refresh="refreshOverview"
        />
      </section>

      <!-- ===== 网关进程 ===== -->
      <section
        v-show="activeTab === 'gateway'"
        id="monitor-panel-gateway"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="monitor-tab-gateway"
      >
        <!-- H2: /api/runtime 失败时下面 6 格是上一次成功读取的冻结快照, 必须
             说出来。旧码这条通道完全静默 (错误不进 loadError), 于是页头报「刷新
             失败」而本标签照常显示几小时前的 PID / 调用数, 用户无从分辨。 -->
        <el-alert
          v-if="runtimeLoadError"
          type="error"
          :title="runtimeLoadError"
          :description="runtimeFrozenText"
          show-icon
          :closable="false"
          class="gateway-alert"
        />
        <div class="metric-grid" role="group" aria-label="网关进程指标">
          <div v-for="s in gatewaySummary" :key="s.label" class="metric-tile">
            <span class="metric-tile-label">{{ s.label }}</span>
            <span class="metric-tile-value" :class="s.tone ? `tone-${s.tone}` : ''">
              <!-- 装饰点 aria-hidden: 状态文案 + 颜色已表意, 点只做强化 -->
              <span
                v-if="s.dot"
                class="metric-dot"
                :class="`dot-${s.tone ?? 'neutral'}`"
                aria-hidden="true"
              />
              <span class="metric-tile-text">
                {{ s.value }}
                <span v-if="s.unit" class="metric-tile-unit">{{ s.unit }}</span>
              </span>
            </span>
          </div>
        </div>
        <p class="metric-note">{{ gatewayFreshText }}</p>
      </section>

      <!-- ===== 并发 ===== -->
      <section
        v-show="activeTab === 'pipeline'"
        id="monitor-panel-pipeline"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="monitor-tab-pipeline"
      >
        <PipelinePanel :mootdx="metrics" :tdx-quant="tdxQuantMetrics" :thsdk="thsdkMetrics" />
      </section>

      <!-- ===== 缓存 ===== -->
      <section
        v-show="activeTab === 'cache'"
        id="monitor-panel-cache"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="monitor-tab-cache"
      >

        <!-- 顶部 summary: 两层缓存 · 速览 (定位哪层出问题, 权威数值在子标签里) -->
        <CacheSummaryCard
          :resp-cache="respCache"
          :resp-foreign="statsFromForeignProcess"
          :breakdown-total-tools="breakdownTotalTools"
          :breakdown-error="breakdownLoadError"
          :metrics="metrics"
          :auto-refresh="autoRefresh"
        />

        <!-- sub-tab 导航: 用 segmented 视觉与外层下划线 tab 分级 (见 .cache-subtabs) -->
        <nav
          class="cfg-subtabs cache-subtabs"
          role="tablist"
          aria-label="缓存子标签"
          @keydown="onCacheSubKeydown"
        >
          <button
            v-for="sub in CACHE_SUBTABS"
            :id="`cache-subtab-${sub.key}`"
            :key="sub.key"
            type="button"
            class="cfg-subtab"
            :class="{ active: activeCacheSub === sub.key }"
            role="tab"
            :aria-selected="activeCacheSub === sub.key"
            :aria-controls="`cache-panel-${sub.key}`"
            :tabindex="activeCacheSub === sub.key ? 0 : -1"
            @click="selectCacheSub(sub.key)"
          >{{ sub.label }}</button>
        </nav>

        <!-- 响应缓存 sub-tab: SessionBar + 例外说明 + RespCacheCard。
             嵌套层的 panel 不给 tabindex="0" —— 外层缓存面板已经是一个 Tab
             停靠点, 再给内层加会变成连着两跳、都停在同一片内容上。 -->
        <section
          v-show="activeCacheSub === 'resp'"
          id="cache-panel-resp"
          role="tabpanel"
          aria-labelledby="cache-subtab-resp"
          class="cache-pane"
        >
          <!-- 市场时段条: 短档此刻的生效语义, 仅响应缓存走交易时段感知 -->
          <SessionBar :session="session" class="cache-session" />

          <!-- 通用缓存模型装不下的两个特例, 避免误读 -->
          <p class="cache-note">
            特例：集合竞价快照 9:15–9:25 走 60s 档、9:25 定格；龙虎榜盘后发布，18:00 前缓存的是前一交易日数据。特殊工具以工具说明为准。
          </p>

          <RespCacheCard
            :cache="respCache"
            :foreign="statsFromForeignProcess"
            :initial-loading="initialLoading"
            :load-error="respLoadError"
            :rate-series="respRateSeries"
            :entries-series="liveEntriesSeries"
            :breakdown-rows="breakdownRows"
            :breakdown-loading="breakdownTableLoading"
            :breakdown-total-tools="breakdownTotalTools"
            :breakdown-error="breakdownLoadError"
            :cleanup-loading="cleanupLoading"
            :vacuum-loading="vacuumLoading"
            :clear-loading="clearLoading"
            @cleanup="onCleanup"
            @vacuum="onVacuum"
            @clear="onClear"
          />
        </section>

        <!-- MooTDX2 L1 sub-tab: 进程内缓存, 无 SessionBar (L1 不走交易时段感知) -->
        <section
          v-show="activeCacheSub === 'l1'"
          id="cache-panel-l1"
          role="tabpanel"
          aria-labelledby="cache-subtab-l1"
          class="cache-pane"
        >
          <Mootdx2L1Card
            :metrics="metrics"
            :initial-loading="initialLoading"
            :load-error="l1LoadError"
            :rate-series="l1RateSeries"
            :size-series="l1SizeSeries"
            :reset-loading="resetL1Loading"
            class="tab-card-gap"
            @reset="onResetL1"
          />
        </section>
      </section>

      <!-- ===== 错误跟踪 ===== -->
      <section
        v-show="activeTab === 'errors'"
        id="monitor-panel-errors"
        role="tabpanel"
        tabindex="0"
        aria-labelledby="monitor-tab-errors"
      >
        <!-- M1: 错误跟踪自带独立轮询, 旧码与页头「自动刷新」开关完全解耦 ——
             关掉开关它照旧每 10s 拉一次。现在一并受控。 -->
        <ErrorTrackingPanel :active="activeTab === 'errors'" :auto-refresh="autoRefresh" />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  FAST_POLL_INTERVAL_MS,
  POLL_INTERVAL_SEC,
  STALE_THRESHOLD_SEC,
} from '@/constants/time'
import { formatHMS } from '@/utils/time'
import { scrollPageTop } from '@/utils/scrollTop'
import { marketSession } from '@/utils/market-session'
import { getStatus as getStatusDirect } from '@/api/status'
import { errorMessage, isCanceledError } from '@/api/client'
import { parseHashTab, nextTabKey } from './_shared/hashTab'
import { deriveGatewaySummary } from './monitor/summary'
import { useGatewayRuntime } from '@/composables/useGatewayRuntime'
import SessionBar from '@/components/SessionBar.vue'
import UpstreamOverviewPanel from '@/components/runtime/UpstreamOverviewPanel.vue'
import { type UpstreamRow, type UpstreamStatus } from '@/api/status'
import CacheSummaryCard from '@/components/runtime/CacheSummaryCard.vue'
import RespCacheCard from '@/components/runtime/RespCacheCard.vue'
import Mootdx2L1Card from '@/components/runtime/Mootdx2L1Card.vue'
import PipelinePanel from '@/components/runtime/PipelinePanel.vue'
import ErrorTrackingPanel from '@/components/runtime/ErrorTrackingPanel.vue'

const {
  metrics,
  gatewayStatus,
  respCache,
  runtime,
  thsdkMetrics,
  tdxQuantMetrics,
  loadError,
  l1LoadError,
  respLoadError,
  runtimeLoadError,
  initialLoading,
  manualLoading,
  statsFromForeignProcess,
  autoRefresh,
  nowSec,
  ageSec,
  refreshLabel,
  l1RateSeries,
  l1SizeSeries,
  respRateSeries,
  liveEntriesSeries,
  breakdownRows,
  breakdownTotalTools,
  breakdownTableLoading,
  breakdownLoadError,
  resetL1Loading,
  cleanupLoading,
  vacuumLoading,
  clearLoading,
  load,
  loadComponentMetrics,
  fastPollActive,
  onResetL1,
  onCleanup,
  onVacuum,
  onClear,
} = useGatewayRuntime()

// 1s 时钟驱动交易时段条带倒计时
const session = computed(() => marketSession(new Date(nowSec.value * 1000)))

/**
 * L3: 开关提示的秒数一律从常量派生。旧码这里写死「每 10 秒」, 而同页
 * gatewayFreshText 却从 POLL_INTERVAL_MS 算 —— 改常量后两处说法会分家。
 * M1/H2: 同时把「错误跟踪」和「并发」快采纳进说明, 旧文案漏了这两条,
 * 用户以为关掉开关就全停了。
 */
const autoRefreshHint = computed(
  () =>
    `每 ${POLL_INTERVAL_SEC} 秒自动刷新 网关进程 / 并发 / 缓存 / 错误跟踪；`
    + `「并发」标签另按 ${FAST_POLL_INTERVAL_MS / 1000} 秒快采线程池快照。`
    + '「上游总览」是手动快照, 请用该标签内的刷新按钮。',
)

// ===== 子标签 ↔ URL (?tab=) 双向同步, 参照 OmniView =====
const route = useRoute()
const router = useRouter()
const TAB_KEYS = ['overview', 'gateway', 'pipeline', 'cache', 'errors'] as const
type MonitorTab = (typeof TAB_KEYS)[number]

// 初始标签从 URL 解析 (支持刷新/分享链接定位), 非法值回退 overview
const activeTab = ref<MonitorTab>(
  parseHashTab(route.query.tab, TAB_KEYS, 'overview'),
)

// 浏览器前进/后退或外部改 query → 同步标签
watch(
  () => route.query.tab,
  (q: LocationQueryValue | LocationQueryValue[]) => {
    const tab = parseHashTab(q, TAB_KEYS, 'overview')
    if (tab !== activeTab.value) activeTab.value = tab
  },
)

/**
 * 顶层子标签定义 (驱动 v-for + 无障碍 id 关联)。由 TAB_KEYS 派生: 旧写法在
 * TAB_KEYS 与 TABS 里各列一遍 5 个 key, 加标签时漏一处不会报错, 只会让那个
 * 标签永远点不到 (URL ?tab= 能解析到, 标签条上却没有)。
 */
const TAB_TITLES: Record<MonitorTab, string> = {
  overview: '上游总览',
  gateway: '网关进程',
  pipeline: '并发',
  cache: '缓存',
  errors: '错误跟踪',
}

const TABS: readonly { key: MonitorTab; title: string }[] = TAB_KEYS.map((key) => ({
  key,
  title: TAB_TITLES[key],
}))

/**
 * 切换顶层子标签。URL 写入只发生在用户点击/键盘切换时 —— 与「配置信息」
 * 「OMNI」一致。挂载时不再往 URL 里塞 ?tab=overview: 缺省标签不需要出现在
 * 地址栏, 复制出去的链接也更干净。
 */
function selectTab(tab: MonitorTab): void {
  if (activeTab.value === tab) return
  activeTab.value = tab
  void router.replace({ query: { ...route.query, tab } })
  // 与另三个带标签的页面一致: 各标签长短差异大, 不重置滚动会落到新标签中段。
  scrollPageTop()
}

/**
 * 顶层 tablist 方向键导航 (WAI-ARIA roving tabindex): Left/Right 循环切换,
 * Home/End 跳首尾; 与另三个页面共用 nextTabKey。
 */
function onTablistKeydown(e: KeyboardEvent): void {
  const target = nextTabKey(TAB_KEYS, activeTab.value, e.key)
  if (!target) return
  e.preventDefault()
  selectTab(target)
  void nextTick(() => {
    document.getElementById(`monitor-tab-${target}`)?.focus()
  })
}

// ===== 缓存 sub-tab (?cacheSub=) 双向同步 =====
// 仅当 activeTab==='cache' 时此 ref 才有视觉意义, 但始终同步 URL 以便
// 切到 cache 标签时直接定位到上次的 sub-tab。
const CACHE_SUBTAB_KEYS = ['resp', 'l1'] as const
type CacheSubTab = (typeof CACHE_SUBTAB_KEYS)[number]

const activeCacheSub = ref<CacheSubTab>(
  parseHashTab(route.query.cacheSub, CACHE_SUBTAB_KEYS, 'resp'),
)

watch(
  () => route.query.cacheSub,
  (q: LocationQueryValue | LocationQueryValue[]) => {
    activeCacheSub.value = parseHashTab(q, CACHE_SUBTAB_KEYS, 'resp')
  },
)

function selectCacheSub(tab: CacheSubTab): void {
  if (activeCacheSub.value === tab) return
  activeCacheSub.value = tab
  void router.replace({ query: { ...route.query, cacheSub: tab } })
  // B5: 与顶层 selectTab 同理 —— 响应缓存子页带明细表, 比 L1 子页长得多,
  // 滚到中部再切会落在新子页中段。
  scrollPageTop()
}

/** 缓存子标签定义 (驱动 v-for + 无障碍 id 关联)。 */
/** 与顶层同款: 标签由 CACHE_SUBTAB_KEYS 派生, 标题表穷尽校验。 */
const CACHE_SUBTAB_TITLES: Record<CacheSubTab, string> = {
  resp: '响应缓存',
  l1: 'MooTDX2 L1缓存',
}

const CACHE_SUBTABS: readonly { key: CacheSubTab; label: string }[] = CACHE_SUBTAB_KEYS.map(
  (key) => ({ key, label: CACHE_SUBTAB_TITLES[key] }),
)

/**
 * 缓存子 tablist 方向键导航 (WAI-ARIA roving tabindex): Left/Right 循环切换,
 * Home/End 跳首尾; 与 ConfigView 顶层 tablist 共用 nextTabKey。
 */
function onCacheSubKeydown(e: KeyboardEvent): void {
  const target = nextTabKey(CACHE_SUBTAB_KEYS, activeCacheSub.value, e.key)
  if (!target) return
  e.preventDefault()
  selectCacheSub(target)
  void nextTick(() => {
    document.getElementById(`cache-subtab-${target}`)?.focus()
  })
}

// ---- 上游总览: 独立快照, 不随自动刷新更新 ----
// gatewayStatus 是自动刷新数据 (供 网关摘要 用), 总览表用自己这份冻结副本,
// 只在首次进入或手动「刷新」时更新。
const upstreamRows = ref<UpstreamRow[]>([])
const upstreamSummary = ref(gatewayStatus.value?.summary ?? null)
const overviewLoading = ref(false)
// 总览是否已拿到过数据 (首次自动同步后置位, 之后自动刷新不再覆盖)
const overviewEverLoaded = ref(false)
// 总览自己的快照时刻 (sec)。与全页 refreshLabel 解耦: 全页每 10s 刷新,
// 但总览冻结不动, 用全页时戳会显示"刚刚刷新"而误导用户以为总览也更新了。
// L2: 一律取服务端 /api/status 的 timestamp, 不取客户端 nowSec —— 同页
// 「网关进程」的新鲜度文案用的就是 status.timestamp, 两个时戳必须同源。
const overviewFetchedAt = ref(0)
// 提示条只在 overviewEverLoaded 为真时渲染, 此处不再需要 '尚未加载' 兜底 (L1)。
const overviewLabel = computed(() => `快照于 ${formatHMS(overviewFetchedAt.value * 1000)}`)

// L1: 首拉失败时旧码整条提示条都不渲染 (v-if 要求已加载过), 只剩一张空表配
// 「暂无匹配的上游」。这里补上说明, 并区分"失败"与"还在路上"。
const overviewPendingText = computed(() =>
  loadError.value
    ? `拉取失败 (${loadError.value})。可点面板内「刷新」单独重试。`
    : '正在等待首次 /api/status 返回。',
)

// L4: 总览手动刷新自己的 AbortController。不复用主轮询那条 (它由 composable
// 在 unmount 时 abort), 但同样要在 unmount 时取消在途请求。
const overviewAc = new AbortController()

// 管线标签激活时 2.5s 快速采样 (历史K线突发 <1s, 10s 抓不到),
// 离开标签立即停止, 不增加后台负担。
let fastTimer: ReturnType<typeof setInterval> | null = null

function stopFastPoll(): void {
  if (fastTimer) {
    clearInterval(fastTimer)
    fastTimer = null
  }
}

/**
 * H2: 快采同时受「当前标签」与页头「自动刷新」开关约束, 回调内再跳过隐藏页。
 * 旧码只按标签起停: 关掉开关它照跑, 浏览器切到后台也照跑 —— 只要停在「并发」
 * 标签就是无限期 1.2 req/s 打后端, 与开关文案说的完全相反。
 */
function syncFastPoll(): void {
  stopFastPoll()
  const want = activeTab.value === 'pipeline' && autoRefresh.value
  fastPollActive.value = want
  if (!want) return
  void loadComponentMetrics()
  fastTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    void loadComponentMetrics()
  }, FAST_POLL_INTERVAL_MS)
}

// immediate 保留: 深链 (如 ?tab=pipeline) 直接落到管线标签时, 非 immediate 的
// watch 不触发, fastPollActive 停在 false 且快采定时器从不启动, 页面退回 10s
// 粗粒度刷新, 抓不到 <1s 的历史K线突发。
// URL 写回不在这里做 (由 selectTab 负责): 否则首屏就会把缺省标签塞进地址栏。
watch([activeTab, autoRefresh], syncFastPoll, { immediate: true })

onBeforeUnmount(() => {
  stopFastPoll()
  overviewAc.abort()
})

// ---- 上游总览行: 从 {name: info} 拆成带 name 的数组 ----
function toUpstreamRows(
  upstreams: Record<string, UpstreamStatus> | undefined,
): UpstreamRow[] {
  return Object.entries(upstreams ?? {}).map(([name, info]) => ({ name, ...info }))
}

// 首次自动刷新拿到 status 后, 同步一次总览 (仅当总览还没加载过);
// 之后自动刷新不再覆盖, 保持"不自动刷新"语义。
// H1: 置位 overviewEverLoaded —— 旧码只在手动 refreshOverview() 里置位, 首次
// 自动同步填充了总览却不置位, 于是"数据已冻结"提示条在最该出现的首屏反而
// 不显示; 要等用户手动点一次刷新才冒出来。此处补上, 并记下快照时刻。
watch(gatewayStatus, (s) => {
  if (s && overviewEverLoaded.value === false) {
    upstreamRows.value = toUpstreamRows(s.upstreams)
    upstreamSummary.value = s.summary
    overviewFetchedAt.value = s.timestamp
    overviewEverLoaded.value = true
  }
})

// 手动「刷新」总览: 单独拉一次 status (复用网关 getStatus), 只更新总览副本。
async function refreshOverview(): Promise<void> {
  // L4: 连点「刷新」旧码会并发多个 getStatus(), 返回顺序不定 → 快照可能落在
  // 较旧那一次上。按钮本身带 :loading, 这里再补一道在途守卫。
  if (overviewLoading.value) return
  overviewLoading.value = true
  try {
    const data = await getStatusDirect({ signal: overviewAc.signal })
    upstreamRows.value = toUpstreamRows(data.upstreams)
    upstreamSummary.value = data.summary
    overviewFetchedAt.value = data.timestamp
    overviewEverLoaded.value = true
  } catch (e) {
    // M1: 旧码只有 try/finally, 失败无反馈 + unhandled rejection, 用户误以为刷新成功。
    // 主动取消 (unmount) 不弹错。
    if (!isCanceledError(e)) ElMessage.error(errorMessage(e, '上游状态刷新失败'))
  } finally {
    overviewLoading.value = false
  }
}

// ---- 网关进程标签: 全部指标 tile (上游健康 2 + 运行态 6) ----
// 派生逻辑抽到 views/monitor/summary.ts (纯函数, 可脱离 plugin-vue 单测)。
// M3/B1: 旧版这里同时渲染 <RuntimeStrip> (6 格) + gatewaySummary (2 格), 两套
// 网格、两套断点、两种 tile 样式, 且 PID / 调用数重复出现。现在合成一张网格。
const gatewaySummary = computed(() =>
  deriveGatewaySummary(gatewayStatus.value, runtime.value),
)

/** 秒级时间戳 → 「HH:MM:SS」, 空值回退占位。 */
function freshAt(tsSec: number | null | undefined): string {
  return tsSec ? formatHMS(tsSec * 1000) : '—'
}

/**
 * B3: 两个标签的数据新鲜度各自标注。上游总览有独立的「快照于」提示条,
 * 网关进程此前只有页头一个全页 refreshLabel —— 它只跟踪 /api/status, 运行态
 * 通道失败或滞后时看不出来, 所以这里分别列出两个来源自己的时刻。
 */
const gatewayFreshText = computed(() => {
  const statusAt = freshAt(gatewayStatus.value?.timestamp)
  const runtimeAt = freshAt(runtime.value?.timestamp)
  const cadence = autoRefresh.value
    ? `每 ${POLL_INTERVAL_SEC} 秒自动刷新`
    : '自动刷新已关闭, 数据不会自行更新'
  return `上游健康取自 ${statusAt} · 运行态取自 ${runtimeAt} · ${cadence}。`
    + '「上游总览」是手动快照, 需在该标签内单独点「刷新」。'
})

/** 运行态失败提示的补充说明: 明确下面 6 格是旧数据还是从没读到过。 */
const runtimeFrozenText = computed(() =>
  runtime.value
    ? `下方 6 格运行态指标已冻结于 ${freshAt(runtime.value.timestamp)}, 是上一次成功读取的值, 不是实时数据。`
    : '下方运行态指标从未成功加载, 一律显示「—」(与「读到 0」区分)。',
)

/**
 * L9: 「并发」标签徽标 = 各层 queued + waiters 之和, 与 PipelinePanel 里每张卡
 * summary 的 busy 口径逐项对齐:
 *   MooTDX2   = thread_pool.queued + reader_gate.waiters  (闸门无队列, 只有等待者)
 *   TDX 量化  = thread_pool.queued                        (策略池/DLL 闸门不上报排队)
 *   THSDK     = worker_pool.queued                        (单 worker 池无等待者字段)
 * PipelinePanel 新增任何带 queued/waiters 的层时, 这里必须同步加一项, 否则徽标
 * 会比面板内显示的排队数小 —— 两处都是"当前有多少在等", 不该读出两个值。
 */
const totalQueued = computed(() => {
  const mootdxQueued =
    (metrics.value?.thread_pool?.queued ?? 0)
    + (metrics.value?.reader_gate?.waiters ?? 0)
  const tdxQuantQueued = tdxQuantMetrics.value?.thread_pool?.queued ?? 0
  const thsdkQueued = thsdkMetrics.value?.worker_pool?.queued ?? 0
  return mootdxQueued + tdxQuantQueued + thsdkQueued
})
</script>

<style scoped>
/* view-* 类已抽到 web/src/styles/view-header.css (全局) */
/* .last-refresh 三态同样已上提到 view-header.css (ConfigView / OmniView 共用) */

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: default;
}

/* 页头与子标签由 .cfg-sticky-head 统一吸顶 (全局规则把 .view-header 降级为
   static), 这里不再需要覆写 —— 长标签页 (上游总览 / 缓存) 滚动后标题和标签
   都还在, 与另三页一致。 */

/* 网关进程指标网格: 8 格固定 4 列 (两行), 断点逐级降列。
   不用 repeat(auto-fit, …) —— auto-fit 会折叠空轨道, 上游健康 2 格与运行态
   6 格的 tile 数不同, 每行实际列宽会错位, 两区无法对齐成同一张表。 */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.metric-tile {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.metric-tile-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.metric-tile-value {
  display: inline-flex;
  align-items: baseline;
  gap: 7px;
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.metric-tile-unit {
  font-size: 11px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.metric-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  /* 与 15px 加粗数字并排, baseline 对齐会把小圆点拽到基线上, 这里显式居中 */
  align-self: center;
}

.metric-note {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.dot-good { background: var(--el-color-success); }
.dot-warn { background: var(--el-color-warning); }
.dot-bad { background: var(--el-color-danger); }
/* 「未加载 / 未配置上游」: 不带判断的中性灰, 不能伪装成健康绿点 */
.dot-neutral { background: var(--el-text-color-placeholder); }

.tone-good { color: var(--el-color-success); }
.tone-warn { color: var(--el-color-warning); }
.tone-bad { color: var(--el-color-danger); }
.tone-busy { color: var(--el-color-primary); }

.gateway-alert {
  margin-bottom: 12px;
}

@media (max-width: 1200px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .metric-grid {
    grid-template-columns: 1fr;
  }
}

/* 缓存子标签: 与外层 .cfg-subtabs (下划线 tab) 明确分级 —— 用分段控件
   (segmented) 视觉, 读作"当前标签内的过滤"而非"又一层 tab"。同时补上总览卡
   与子标签之间的间距 (全局 .cfg-subtabs 只有 margin-bottom, 与上方 el-card
   贴死, 上下不对称)。 */
.cache-subtabs {
  gap: 4px;
  margin-top: 12px;
  margin-bottom: 14px;
  padding: 3px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color);
  overflow: visible;
}

.cache-subtabs .cfg-subtab {
  border-bottom: 0;
  margin-bottom: 0;
  padding: 5px 14px;
  border-radius: 6px;
  font-size: 13px;
}

.cache-subtabs .cfg-subtab.active,
.cache-subtabs .cfg-subtab[aria-selected='true'] {
  background: var(--el-bg-color);
  border-bottom-color: transparent;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}

/* 缓存标签内的市场时段条 */
.cache-session {
  margin-bottom: 12px;
}

/* 缓存标签内的特例说明 (左色条 + 浅背景, 与核心 cache-layers-hint 区分) */
.cache-note {
  margin: 0 2px 12px;
  padding: 8px 12px;
  border-radius: 8px;
  border-left: 3px solid var(--el-color-warning-light-5);
  background: var(--el-fill-color-light);
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

/* 缓存标签内两张卡的间距 */
.tab-card-gap {
  margin-top: 12px;
}

/* 上游总览陈旧提示条 */
.overview-stale-alert {
  margin-bottom: 12px;
}
</style>
