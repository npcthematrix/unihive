<template>
  <div class="view-page">
    <!-- 页头: 全页唯一的刷新/自动刷新控件 -->
    <header class="view-header">
      <h1 class="view-h1">网关运行监控</h1>
      <div class="view-actions">
        <span
          class="last-refresh"
          :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
        >
          {{ refreshLabel }}
        </span>
        <el-button :icon="Refresh" :loading="manualLoading" @click="load(true)">
          立即刷新
        </el-button>
        <el-tooltip
          content="每 10 秒自动刷新 网关进程 / 并发管线 / 缓存；「上游总览」不自动刷新 (请用该标签内的刷新按钮)"
          placement="top"
        >
          <span class="auto-toggle">
            <el-switch v-model="autoRefresh" />
            <span class="auto-label">自动刷新</span>
          </span>
        </el-tooltip>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="monitor-tabs">
      <!-- ===== 上游总览 (默认, 不自动刷新) ===== -->
      <el-tab-pane name="overview">
        <template #label>
          <span class="tab-label">上游总览</span>
        </template>
        <UpstreamOverviewPanel
          :rows="upstreamRows"
          :summary="upstreamSummary"
          :loading="overviewLoading"
          @refresh="refreshOverview"
        />
      </el-tab-pane>

      <!-- ===== 网关进程 ===== -->
      <el-tab-pane name="gateway">
        <template #label>
          <span class="tab-label">网关进程</span>
        </template>
        <RuntimeStrip :stats="runtime" />
        <div class="gateway-summary">
          <div v-for="s in gatewaySummary" :key="s.label" class="gs-item">
            <span class="gs-label">{{ s.label }}</span>
            <span class="gs-value" :class="s.tone ? `tone-${s.tone}` : ''">
              <span v-if="s.dot" class="gs-dot" :class="`dot-${s.tone}`" />
              {{ s.value }}
            </span>
          </div>
        </div>
      </el-tab-pane>

      <!-- ===== 并发管线 ===== -->
      <el-tab-pane name="pipeline">
        <template #label>
          <span class="tab-label">
            并发管线
            <span v-if="totalQueued > 0" class="tab-badge">{{ totalQueued }}</span>
          </span>
        </template>
        <PipelinePanel :mootdx="metrics" :thsdk="thsdkMetrics" />
      </el-tab-pane>

      <!-- ===== 缓存与存储 ===== -->
      <el-tab-pane name="cache">
        <template #label>
          <span class="tab-label">缓存与存储</span>
        </template>

        <!-- 两层缓存定位: 上层 SQLite 持久化, 下层进程内内存 -->
        <p class="cache-layers-hint">
          本标签含两层缓存：<strong>响应缓存</strong> 为 SQLite 持久化（重启保留，下方第一张卡）；
          <strong>MooTDX2 L1</strong> 为进程内内存缓存（重启清空，最后一张卡）。
        </p>

        <!-- 市场时段条: 短档此刻的生效语义, 随缓存页展示 -->
        <SessionBar :session="session" class="cache-session" />

        <!-- 通用缓存模型装不下的两个特例, 随缓存页展示, 避免误读 -->
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
          :cleanup-loading="cleanupLoading"
          :vacuum-loading="vacuumLoading"
          :clear-loading="clearLoading"
          @cleanup="onCleanup"
          @vacuum="onVacuum"
          @clear="onClear"
        />

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
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { STALE_THRESHOLD_SEC } from '@/constants/time'
import { marketSession } from '@/utils/market-session'
import { getStatus as getStatusDirect } from '@/api/status'
import { parseHashTab } from './_shared/hashTab'
import { useGatewayRuntime } from '@/composables/useGatewayRuntime'
import SessionBar from '@/components/SessionBar.vue'
import RuntimeStrip from '@/components/RuntimeStrip.vue'
import UpstreamOverviewPanel, { type UpstreamRow } from '@/components/runtime/UpstreamOverviewPanel.vue'
import RespCacheCard from '@/components/runtime/RespCacheCard.vue'
import Mootdx2L1Card from '@/components/runtime/Mootdx2L1Card.vue'
import PipelinePanel from '@/components/runtime/PipelinePanel.vue'

const {
  metrics,
  gatewayStatus,
  respCache,
  runtime,
  thsdkMetrics,
  loadError,
  l1LoadError,
  respLoadError,
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
const TAB_KEYS = ['overview', 'gateway', 'pipeline', 'cache'] as const
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

// ---- 上游总览: 独立快照, 不随自动刷新更新 ----
// gatewayStatus 是自动刷新数据 (供 网关摘要 用), 总览表用自己这份冻结副本,
// 只在首次进入或手动「刷新」时更新。
const upstreamRows = ref<UpstreamRow[]>([])
const upstreamSummary = ref(gatewayStatus.value?.summary ?? null)
const overviewLoading = ref(false)
// 总览是否已拿到过数据 (首次自动同步后置位, 之后自动刷新不再覆盖)
const overviewEverLoaded = ref(false)

// 管线标签激活时 2.5s 快速采样 (历史K线突发 <1s, 10s 抓不到),
// 离开标签立即停止, 不增加后台负担。
let fastTimer: ReturnType<typeof setInterval> | null = null

function stopFastPoll(): void {
  if (fastTimer) {
    clearInterval(fastTimer)
    fastTimer = null
  }
}

watch(activeTab, (tab) => {
  // 标签变化写回 URL (仅在不同时, 避免无谓 replace)
  if (route.query.tab !== tab) {
    void router.replace({ query: { ...route.query, tab } })
  }

  stopFastPoll()
  fastPollActive.value = tab === 'pipeline'
  if (tab === 'pipeline') {
    void loadComponentMetrics()
    fastTimer = setInterval(() => void loadComponentMetrics(), 2500)
  }
})

onBeforeUnmount(stopFastPoll)

// ---- 上游总览行: 从 {name: info} 拆成带 name 的数组 ----
function toUpstreamRows(
  upstreams: Record<string, import('@/api/status').UpstreamStatus> | undefined,
): UpstreamRow[] {
  return Object.entries(upstreams ?? {}).map(([name, info]) => ({ name, ...info }))
}

// 首次自动刷新拿到 status 后, 同步一次总览 (仅当总览还没加载过);
// 之后自动刷新不再覆盖, 保持"不自动刷新"语义。
watch(gatewayStatus, (s) => {
  if (s && overviewEverLoaded.value === false) {
    upstreamRows.value = toUpstreamRows(s.upstreams)
    upstreamSummary.value = s.summary
  }
})

// 手动「刷新」总览: 单独拉一次 status (复用网关 getStatus), 只更新总览副本。
async function refreshOverview(): Promise<void> {
  overviewLoading.value = true
  try {
    const data = await getStatusDirect()
    upstreamRows.value = toUpstreamRows(data.upstreams)
    upstreamSummary.value = data.summary
    overviewEverLoaded.value = true
  } finally {
    overviewLoading.value = false
  }
}

// ---- 网关进程标签: 总体摘要 (填均衡首屏, 不使下方大片空白) ----
interface SummaryItem {
  label: string
  value: string
  tone?: 'good' | 'warn' | 'bad'
  dot?: boolean
}

const gatewaySummary = computed<SummaryItem[]>(() => {
  const upstreams = gatewayStatus.value?.upstreams ?? {}
  const entries = Object.values(upstreams)
  const online = entries.filter((u) => u.status === 'online').length
  const problem = entries.filter(
    (u) => u.status === 'offline' || u.status === 'unknown',
  ).length
  const degraded = entries.filter((u) => u.status === 'degraded').length

  let overallTone: 'good' | 'warn' | 'bad' = 'good'
  let overallText = '整体健康'
  if (problem > 0) {
    overallTone = 'bad'
    overallText = `${problem} 个上游异常`
  } else if (degraded > 0) {
    overallTone = 'warn'
    overallText = `${degraded} 个上游降级`
  }

  return [
    { label: '整体状态', value: overallText, tone: overallTone, dot: true },
    { label: '在线上游', value: `${online} / ${entries.length}`, tone: 'good' },
    { label: '在途请求', value: String(runtime.value?.active_requests ?? 0) },
    { label: '近 5 分钟调用', value: String(runtime.value?.calls_last_5m ?? 0) },
  ]
})

// 全组件排队/等待总数 (线程池队列 + 信号量等待者), 驱动标签徽标
const totalQueued = computed(() => {
  const mootdxQueued =
    (metrics.value?.thread_pool?.queued ?? 0)
    + (metrics.value?.reader_gate?.waiters ?? 0)
  const thsdkQueued = thsdkMetrics.value?.worker_pool?.queued ?? 0
  return mootdxQueued + thsdkQueued
})
</script>

<style scoped>
/* view-* 类已抽到 web/src/styles/view-header.css (全局) */

.last-refresh {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.last-refresh.failed {
  color: var(--el-color-danger);
}

.last-refresh.stale {
  color: var(--el-color-warning);
}

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: default;
}

/* 子标签页 */
.monitor-tabs {
  margin-top: 2px;
}

.monitor-tabs :deep(.el-tabs__content) {
  padding-top: 4px;
}

.tab-label {
  font-weight: 500;
}

.tab-badge {
  display: inline-block;
  min-width: 16px;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--el-color-warning);
  color: #fff;
  font-size: 11px;
  line-height: 16px;
  text-align: center;
}

/* 网关进程摘要条 */
.gateway-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.gs-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.gs-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.gs-value {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 15px;
  font-weight: 600;
}

.gs-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.dot-good { background: var(--el-color-success); }
.dot-warn { background: var(--el-color-warning); }
.dot-bad { background: var(--el-color-danger); }

.tone-good { color: var(--el-color-success); }
.tone-warn { color: var(--el-color-warning); }
.tone-bad { color: var(--el-color-danger); }

@media (max-width: 860px) {
  .gateway-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .gateway-summary {
    grid-template-columns: 1fr;
  }
}

/* 两层缓存定位说明 */
.cache-layers-hint {
  margin: 0 2px 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

/* 缓存标签内的市场时段条 */
.cache-session {
  margin-bottom: 12px;
}

/* 缓存标签内的特例说明 */
.cache-note {
  margin: 0 2px 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

/* 缓存标签内两张卡的间距 */
.tab-card-gap {
  margin-top: 12px;
}
</style>
