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
          >
            {{ refreshLabel }}
          </span>
          <el-button size="small" :icon="Refresh" :loading="manualLoading" @click="load(true)">
            立即刷新
          </el-button>
          <el-tooltip
            content="每 10 秒自动刷新 网关进程 / 并发 / 缓存；「上游总览」不自动刷新 (请用该标签内的刷新按钮)"
            placement="top"
          >
            <span class="auto-toggle">
              <el-switch v-model="autoRefresh" />
              <span class="auto-label">自动刷新</span>
            </span>
          </el-tooltip>
        </div>
      </header>

      <!-- 子标签 — WAI-ARIA Tabs: roving tabindex + 方向键, 与另三页同款 -->
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
          <!-- 排队数徽标: 只在并发标签上, 且有排队时才出现 -->
          <span v-if="tab.key === 'pipeline' && totalQueued > 0" class="tab-badge">
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
        aria-labelledby="monitor-tab-overview"
      >
        <el-alert
          v-if="overviewEverLoaded"
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
          @refresh="refreshOverview"
        />
      </section>

      <!-- ===== 网关进程 ===== -->
      <section
        v-show="activeTab === 'gateway'"
        id="monitor-panel-gateway"
        role="tabpanel"
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
        aria-labelledby="monitor-tab-pipeline"
      >
        <PipelinePanel :mootdx="metrics" :tdx-quant="tdxQuantMetrics" :thsdk="thsdkMetrics" />
      </section>

      <!-- ===== 缓存 ===== -->
      <section
        v-show="activeTab === 'cache'"
        id="monitor-panel-cache"
        role="tabpanel"
        aria-labelledby="monitor-tab-cache"
      >

        <!-- 顶部 summary: 两层缓存 · 总览 (一眼可见健康度) -->
        <CacheSummaryCard
          :resp-cache="respCache"
          :resp-foreign="statsFromForeignProcess"
          :breakdown-total-tools="breakdownTotalTools"
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

        <!-- 响应缓存 sub-tab: SessionBar + 例外说明 + RespCacheCard -->
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
        aria-labelledby="monitor-tab-errors"
      >
        <ErrorTrackingPanel :active="activeTab === 'errors'" />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { POLL_INTERVAL_MS, STALE_THRESHOLD_SEC } from '@/constants/time'
import { formatHMS } from '@/utils/time'
import { scrollPageTop } from '@/utils/scrollTop'
import { marketSession } from '@/utils/market-session'
import { getStatus as getStatusDirect } from '@/api/status'
import { errorMessage } from '@/api/client'
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

/** 顶层子标签定义 (驱动 v-for + 无障碍 id 关联)。 */
const TABS: readonly { key: MonitorTab; title: string }[] = [
  { key: 'overview', title: '上游总览' },
  { key: 'gateway', title: '网关进程' },
  { key: 'pipeline', title: '并发' },
  { key: 'cache', title: '缓存' },
  { key: 'errors', title: '错误跟踪' },
]

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
}

/** 缓存子标签定义 (驱动 v-for + 无障碍 id 关联)。 */
const CACHE_SUBTABS: readonly { key: CacheSubTab; label: string }[] = [
  { key: 'resp', label: '响应缓存' },
  { key: 'l1', label: 'MooTDX2 L1缓存' },
]

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
const overviewFetchedAt = ref(0)
const overviewLabel = computed(() =>
  overviewFetchedAt.value ? `快照于 ${formatHMS(overviewFetchedAt.value * 1000)}` : '尚未加载',
)

// 管线标签激活时 2.5s 快速采样 (历史K线突发 <1s, 10s 抓不到),
// 离开标签立即停止, 不增加后台负担。
let fastTimer: ReturnType<typeof setInterval> | null = null

function stopFastPoll(): void {
  if (fastTimer) {
    clearInterval(fastTimer)
    fastTimer = null
  }
}

// M3: immediate —— 深链 (如 ?tab=pipeline) 直接落到管线标签时, 旧码 watch 不触发,
// fastPollActive 保持 false 且快采定时器从不启动, 页面停在 10s 粗粒度刷新,
// 抓不到 <1s 的历史K线突发。immediate 让首屏即按当前标签决定是否起快采。
// URL 写回不在这里做 (由 selectTab 负责): 否则首屏就会把缺省标签塞进地址栏。
watch(activeTab, (tab) => {
  stopFastPoll()
  fastPollActive.value = tab === 'pipeline'
  if (tab === 'pipeline') {
    void loadComponentMetrics()
    fastTimer = setInterval(() => void loadComponentMetrics(), 2500)
  }
}, { immediate: true })

onBeforeUnmount(stopFastPoll)

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
    overviewFetchedAt.value = nowSec.value
    overviewEverLoaded.value = true
  }
})

// 手动「刷新」总览: 单独拉一次 status (复用网关 getStatus), 只更新总览副本。
async function refreshOverview(): Promise<void> {
  overviewLoading.value = true
  try {
    const data = await getStatusDirect()
    upstreamRows.value = toUpstreamRows(data.upstreams)
    upstreamSummary.value = data.summary
    overviewFetchedAt.value = nowSec.value
    overviewEverLoaded.value = true
  } catch (e) {
    // M1: 旧码只有 try/finally, 失败无反馈 + unhandled rejection, 用户误以为刷新成功。
    ElMessage.error(errorMessage(e, '上游状态刷新失败'))
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
    ? `每 ${Math.round(POLL_INTERVAL_MS / 1000)} 秒自动刷新`
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

// 全组件排队/等待总数 (线程池队列 + 信号量等待者), 驱动标签徽标
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

/* 排队数徽标: 白字压 --el-color-warning (#E6A23C) 仅约 2.2:1, 不达 WCAG AA。
   改深棕字压同底色 → 约 6.4:1; 两主题下 --el-color-warning 均为琥珀色,
   故此处硬编码深色前景是安全的。 */
.tab-badge {
  display: inline-block;
  min-width: 16px;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--el-color-warning);
  color: #3a2a00;
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
}

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
