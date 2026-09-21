<template>
  <div class="l1-cache-page">
    <!-- 页头: 全页唯一的刷新/自动刷新控件 -->
    <header class="page-header">
      <h1 class="page-title">网关缓存监控</h1>
      <div class="page-actions">
        <span
          class="last-refresh"
          :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
        >
          {{ refreshLabel }}
        </span>
        <el-button :icon="Refresh" :loading="manualLoading" @click="load(true)">
          立即刷新
        </el-button>
        <el-tooltip content="每 10 秒自动刷新全部卡片" placement="top">
          <span class="auto-toggle">
            <el-switch v-model="autoRefresh" />
            <span class="auto-label">自动刷新</span>
          </span>
        </el-tooltip>
      </div>
    </header>

    <!-- 上海市场时段条: 通用短档此刻的生效语义 -->
    <div class="session-bar" :class="`phase-${session.phase}`">
      <span class="session-dot" />
      <span class="session-name">{{ session.label }}</span>
      <span class="session-range">{{ session.range }}</span>
      <span class="session-spacer" />
      <span v-if="session.shortTtlActive" class="session-ttl">
        短档按配置值生效 · 距 {{ session.boundaryLabel }} {{ formatCountdown(session.secondsToBoundary) }}
      </span>
      <span v-else class="session-ttl">
        短档缓存到 {{ session.boundaryLabel }} · 还有 {{ formatCountdown(session.secondsToBoundary) }}
      </span>
    </div>
    <!-- 通用模型装不下的两个特例, 常驻一行小字, 避免误读 -->
    <p class="session-note">
      特例：集合竞价快照 9:15–9:25 走 60s 档、9:25 定格；龙虎榜盘后发布，18:00 前缓存的是前一交易日数据。特殊工具以工具说明为准。
    </p>

    <!-- ============ 响应缓存 (网关级, 居首) ============ -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">
            响应缓存 <small class="sub">SQLite · logs/cache.db</small>
          </h2>
          <div class="mgmt-actions">
            <el-button size="small" :loading="cleanupLoading" @click="onCleanup">
              清理过期
            </el-button>
            <el-button
              size="small"
              :loading="vacuumLoading"
              :disabled="!hasReclaimablePages"
              @click="onVacuum"
            >
              整理 (VACUUM)
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              class="danger-action"
              :disabled="(respCache?.entries ?? 0) === 0"
              :loading="clearLoading"
              @click="onClear"
            >
              清空全部
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="respLoadError"
        type="error"
        :title="respLoadError"
        show-icon
        :closable="false"
        class="card-alert"
      />

      <div v-loading="initialLoading" class="stat-grid">
        <div class="hitrate-card">
          <div class="hitrate-head">
            <span class="mini-label">命中率</span>
            <span class="hitrate-value">{{ respHitRateText }}</span>
          </div>
          <el-progress
            v-if="respHitRatePct !== null"
            :percentage="respHitRatePct"
            :stroke-width="10"
            :show-text="false"
            :color="respHitRateColor"
          />
          <div v-else class="hitrate-bar-empty" />
          <div class="hitrate-meta">
            <span><strong>{{ respCache?.hits ?? 0 }}</strong> 命中</span>
            <span><strong>{{ respCache?.misses ?? 0 }}</strong> 未命中</span>
            <span class="hint">{{ respHitRateHint }}</span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">状态</div>
          <div class="mini-value">
            <span
              class="status-dot"
              :class="respCache?.enabled ? 'status-online' : 'status-disabled'"
            />
            <span :class="respCache?.enabled ? 'on' : 'off'">
              {{ respCache?.enabled ? '启用' : '停用' }}
            </span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">活跃条目</div>
          <div class="mini-value">{{ respLiveEntriesText }}</div>
          <div class="mini-hint">未过期</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">过期条目</div>
          <div class="mini-value">{{ respExpiredText }}</div>
          <div class="mini-hint">待清理</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">总条目</div>
          <div class="mini-value">{{ respEntriesText }}</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">数据库</div>
          <div class="mini-value">{{ respDbSizeText }}</div>
          <div class="mini-hint">WAL {{ respWalText }}</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">死页</div>
          <div class="mini-value">{{ respFreelistText }}</div>
          <div class="mini-hint">VACUUM 可回收</div>
        </div>
      </div>

      <div class="trend-grid">
        <div class="trend-row">
          <span class="trend-label">命中率趋势</span>
          <Sparkline
            :points="respRateSeries"
            color="var(--el-color-success)"
            :aria-label="rateAria('响应缓存命中率', respRateSeries)"
          />
        </div>
        <div class="trend-row">
          <span class="trend-label">活跃条目</span>
          <Sparkline
            :points="liveEntriesSeries"
            color="var(--el-color-primary)"
            :aria-label="countAria('活跃条目', liveEntriesSeries)"
          />
        </div>
      </div>

      <CacheBreakdownTable
        :rows="breakdownRows"
        :loading="breakdownLoading"
        :total-entries="respCache?.entries ?? null"
      />
      <p v-if="breakdownNote" class="trunc-note">{{ breakdownNote }}</p>

      <p v-if="respCache && !respCache.enabled" class="off-hint">
        {{ respCache.error || '缓存数据库未创建' }}
      </p>
    </el-card>

    <!-- ============ MooTDX2 L1 (单上游内存缓存) ============ -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">
            MooTDX2 L1 缓存 <small class="sub">get_daily_local</small>
          </h2>
          <el-button
            size="small"
            :loading="resetL1Loading"
            @click="onResetL1"
          >
            重置 L1 缓存
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="l1LoadError"
        type="error"
        :title="l1LoadError"
        show-icon
        :closable="false"
        class="card-alert"
      />

      <div v-loading="initialLoading" class="stat-grid">
        <div class="hitrate-card">
          <div class="hitrate-head">
            <span class="mini-label">命中率</span>
            <span class="hitrate-value">{{ hitRateText }}</span>
          </div>
          <el-progress
            v-if="hitRatePct !== null"
            :percentage="hitRatePct"
            :stroke-width="10"
            :show-text="false"
            :color="hitRateColor"
          />
          <div v-else class="hitrate-bar-empty" />
          <div class="hitrate-meta">
            <span><strong>{{ dailyL1?.hits ?? 0 }}</strong> 命中</span>
            <span><strong>{{ dailyL1?.misses ?? 0 }}</strong> 未命中</span>
            <span class="hint">{{ hitRateHint }}</span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">状态</div>
          <div class="mini-value">
            <span
              class="status-dot"
              :class="dailyL1?.enabled ? 'status-online' : 'status-disabled'"
            />
            <span :class="dailyL1?.enabled ? 'on' : 'off'">
              {{ dailyL1?.enabled ? '启用' : '停用' }}
            </span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">当前 size</div>
          <div class="mini-value">{{ sizeText }}</div>
          <div class="mini-hint">/ {{ dailyL1?.maxsize ?? 2048 }} 上限</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">并发上限</div>
          <div class="mini-value">{{ dailyL1?.reader_semaphore_limit ?? 4 }}</div>
          <div class="mini-hint">Reader 并发控制</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">TTL</div>
          <div class="mini-value">{{ ttlText }}</div>
          <div class="mini-hint">到次日 9:00</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">总请求数</div>
          <div class="mini-value">{{ metrics?.total_requests ?? 0 }}</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">扫描 L1 条目</div>
          <div class="mini-value">{{ scanL1Size }}</div>
          <div class="mini-hint">板块 + 异动</div>
        </div>
      </div>

      <div class="trend-grid">
        <div class="trend-row">
          <span class="trend-label">命中率趋势</span>
          <Sparkline
            :points="l1RateSeries"
            color="var(--el-color-primary)"
            :aria-label="rateAria('L1 命中率', l1RateSeries)"
          />
        </div>
        <div class="trend-row">
          <span class="trend-label">缓存 size</span>
          <Sparkline
            :points="l1SizeSeries"
            color="var(--el-color-warning)"
            :aria-label="countAria('L1 缓存条目', l1SizeSeries)"
          />
        </div>
      </div>

      <div class="l1-extra-grid">
        <L1CacheBlock
          title="板块扫描 L1"
          :sub="blockSub('stock_top_board', metrics?.top_board_l1)"
          :stats="metrics?.top_board_l1"
        />
        <L1CacheBlock
          title="异动扫描 L1"
          :sub="blockSub('stock_unusual', metrics?.unusual_l1)"
          :stats="metrics?.unusual_l1"
        />
      </div>

      <div v-if="poolRows.length" class="pool-row">
        <span class="pool-title">连接池</span>
        <span v-for="row in poolRows" :key="row.label" class="pool-item">
          <span class="pool-label">{{ row.label }}</span>
          <strong>{{ row.value }}</strong>
        </span>
      </div>

      <details class="constants-details">
        <summary>配置常量 · get_daily_local L1</summary>
        <div class="kv-grid">
          <div class="kv-key">缓存算法</div>
          <div class="kv-val"><code>TTLCache(maxsize={{ dailyL1?.maxsize ?? 2048 }}, ttl=次日9:00)</code> — 当日有效</div>
          <div class="kv-key">缓存 key</div>
          <div class="kv-val"><code>(date, symbol, adjust, start_date, end_date)</code> — 仅 <code>adjust="none"</code> 写入</div>
          <div class="kv-key">value 形状</div>
          <div class="kv-val"><code>list[dict]</code> — get_daily_sync 原始 records</div>
          <div class="kv-key">过期策略</div>
          <div class="kv-val">次日 9:00 自动过期，避免陈旧行情</div>
          <div class="kv-key">并发控制</div>
          <div class="kv-val">
            <code>asyncio.Semaphore({{ dailyL1?.reader_semaphore_limit ?? 4 }})</code> 限制 Reader 并发执行
          </div>
        </div>
      </details>

      <p v-if="metrics && !dailyL1?.enabled" class="off-hint">
        L1 缓存未启用 (cachetools 缺失或代码已禁用)
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { getUpstreamMetrics } from '@/api/upstream-metrics'
import type { UpstreamMetrics, L1CacheStats } from '@/api/upstream-metrics'
import { getStatus, type CacheStats } from '@/api/status'
import {
  type CacheToolBreakdownRow,
  cleanupCache,
  clearCache,
  getCacheToolBreakdown,
  resetUpstreamL1,
  vacuumCache,
} from '@/api/cache'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { STALE_THRESHOLD_SEC } from '@/constants/time'
import { formatRefreshLabel } from '@/utils/time'
import { formatPercent, formatTtl } from '@/utils/number'
import { formatCountdown, marketSession } from '@/utils/market-session'
import Sparkline from '@/components/Sparkline.vue'
import L1CacheBlock from '@/components/L1CacheBlock.vue'
import CacheBreakdownTable from '@/components/CacheBreakdownTable.vue'

const metrics = ref<UpstreamMetrics | null>(null)
const respCache = ref<CacheStats | null>(null)
const initialLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
const l1LoadError = ref('')
const respLoadError = ref('')

let inFlight = false

/** 自动刷新 + 时钟 + tab 可见时陈旧重拉 — 见 composables/useAutoRefresh.ts */
const { autoRefresh, nowSec, lastFetchSec, ageSec, markFetched, resetFetched } = useAutoRefresh({
  onPoll: () => { void load(false) },
})

// 1s 时钟复用 composable 的 nowSec, 驱动时段倒计时
const session = computed(() => marketSession(new Date(nowSec.value * 1000)))

// 趋势采样环 (最多 60 点, 与 10s 轮询配合 ≈ 最近 10 分钟);
// 源加载失败时对应字段为 null, Sparkline 在该时段留缺口。
interface TrendSample {
  l1Rate: number | null
  l1Size: number | null
  respRate: number | null
  liveEntries: number | null
}
const MAX_SAMPLES = 60
const samples = ref<TrendSample[]>([])
const breakdownRows = ref<CacheToolBreakdownRow[]>([])
const breakdownTotalTools = ref(0)
const breakdownLoading = ref(false)

// 管理操作 loading
const resetL1Loading = ref(false)
const cleanupLoading = ref(false)
const vacuumLoading = ref(false)
const clearLoading = ref(false)

const refreshLabel = computed(() =>
  formatRefreshLabel({
    loadError: loadError.value,
    lastFetchSec: lastFetchSec.value,
    ageSec: ageSec.value,
  }),
)

// ---------------------------------------------------------------------------
// MooTDX2 L1 派生 (指标来自进程内 client, 恒为当前进程, 无跨进程问题)
// ---------------------------------------------------------------------------

const dailyL1 = computed(() => metrics.value?.daily_l1 ?? null)

const hitRatePct = computed(() => {
  const rate = dailyL1.value?.hit_rate
  return rate == null ? null : Number((rate * 100).toFixed(1))
})

const hitRateText = computed(() => {
  const rate = dailyL1.value?.hit_rate
  return rate == null ? '—' : formatPercent(rate)
})

const hitRateHint = computed(() => {
  const l1 = dailyL1.value
  if (!l1?.enabled) return l1 ? '缓存已停用' : '尚未拉到指标'
  const total = (l1.hits ?? 0) + (l1.misses ?? 0)
  return total > 0
    ? `共 ${total} 次请求 · 并发未命中已折叠`
    : '网关启动后累计 (尚未请求)'
})

const hitRateColor = computed(() => {
  const pct = hitRatePct.value
  if (pct === null) return undefined
  if (pct > 70) return 'var(--el-color-success)'
  if (pct > 40) return 'var(--el-color-warning)'
  return 'var(--el-color-danger)'
})

const sizeText = computed(() => {
  const s = dailyL1.value?.size
  return s == null ? '—' : String(s)
})

const ttlText = computed(() => {
  const t = dailyL1.value?.ttl_seconds
  return t == null ? '—' : formatTtl(t)
})

const scanL1Size = computed(() =>
  (metrics.value?.top_board_l1?.size ?? 0)
  + (metrics.value?.unusual_l1?.size ?? 0),
)

// 序列不再过滤 null: Sparkline 按全样本序号定位, null 即缺口
function seriesOf(pick: (sample: TrendSample) => number | null): (number | null)[] {
  return samples.value.map(pick)
}

const l1RateSeries = computed(() =>
  seriesOf((sample) => sample.l1Rate),
)

const l1SizeSeries = computed(() =>
  seriesOf((sample) => sample.l1Size),
)

// ---------------------------------------------------------------------------
// SQLite 响应缓存派生 (字段语义见后端 console_api.get_cache_stats)
// ---------------------------------------------------------------------------

const gatewayPid = ref(0)

/** stats 文件是否来自异进程 (后端启动已刷盘, 正常情况下不应出现)。 */
const statsFromForeignProcess = computed(() => {
  const c = respCache.value
  return c?.stats_pid != null && c.stats_pid !== gatewayPid.value
})

const respHitRatePct = computed(() => {
  if (statsFromForeignProcess.value) return null
  const rate = respCache.value?.hit_rate
  return rate == null ? null : Number((rate * 100).toFixed(1))
})

const respHitRateText = computed(() => {
  if (statsFromForeignProcess.value) return '—'
  const rate = respCache.value?.hit_rate
  return rate == null ? '—' : formatPercent(rate)
})

const respHitRateHint = computed(() => {
  const c = respCache.value
  if (!c?.enabled) return c?.error || '缓存数据库未创建'
  if (statsFromForeignProcess.value) {
    return '命中统计来自上一网关进程, 不代表当前进程'
  }
  const total = (c.hits ?? 0) + (c.misses ?? 0)
  return total > 0
    ? `共 ${total} 次请求 · 并发未命中已折叠`
    : '网关启动后累计 (尚未请求)'
})

const respHitRateColor = computed(() => {
  const pct = respHitRatePct.value
  if (pct === null) return undefined
  if (pct > 70) return 'var(--el-color-success)'
  if (pct > 40) return 'var(--el-color-warning)'
  return 'var(--el-color-danger)'
})

const respLiveEntriesText = computed(() => {
  const c = respCache.value
  return c?.enabled && c.live_entries != null ? c.live_entries : '—'
})

const respExpiredText = computed(() => {
  const c = respCache.value
  return c?.enabled && c.expired_entries != null ? c.expired_entries : '—'
})

const respEntriesText = computed(() => {
  const c = respCache.value
  return c?.enabled && c.entries != null ? c.entries : '—'
})

const respDbSizeText = computed(() => {
  const c = respCache.value
  return c?.enabled ? `${(c.db_size_mb || 0).toFixed(2)} MB` : '—'
})

const respWalText = computed(() => {
  const bytes = respCache.value?.wal_size_bytes ?? 0
  return bytes >= 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${bytes} B`
})

const respFreelistText = computed(() => {
  const c = respCache.value
  return c?.enabled && c.freelist_count != null ? c.freelist_count : '—'
})

const hasReclaimablePages = computed(() =>
  Boolean(respCache.value?.enabled)
  && (respCache.value?.freelist_count ?? 0) > 0,
)

const respRateSeries = computed(() =>
  seriesOf((sample) => sample.respRate),
)

const liveEntriesSeries = computed(() =>
  seriesOf((sample) => sample.liveEntries),
)

// Sparkline 无障碍标签: 首尾值 + 采样数
function rateAria(label: string, pts: (number | null)[]): string {
  const defined = pts.filter((v): v is number => v != null)
  if (defined.length < 2) return `${label}: 采样不足`
  return `${label}: ${formatPercent(defined[0])} → ${formatPercent(defined[defined.length - 1])}, 共 ${defined.length} 个采样点`
}

function countAria(label: string, pts: (number | null)[]): string {
  const defined = pts.filter((v): v is number => v != null)
  if (defined.length < 2) return `${label}: 采样不足`
  return `${label}: ${defined[0]} → ${defined[defined.length - 1]}, 共 ${defined.length} 个采样点`
}

// 扫描 L1 块副标题: TTL 取实时指标, 不再硬编码 "10s 档"
function blockSub(prefix: string, stats?: L1CacheStats): string {
  const ttl = stats?.ttl_seconds
  return `${prefix} · ${ttl != null ? formatTtl(ttl) : '固定档'}`
}

// 分布表截断/总数说明
const breakdownNote = computed(() => {
  if (breakdownLoading.value || breakdownTotalTools.value === 0) return ''
  const shown = breakdownRows.value.length
  if (shown < breakdownTotalTools.value) {
    return `仅显示条目最多的 ${shown} 个工具 · 共 ${breakdownTotalTools.value} 个工具有缓存条目`
  }
  return `共 ${breakdownTotalTools.value} 个工具有缓存条目`
})

// ---------------------------------------------------------------------------
// mootdx2 连接池摘要 (pool_stats 可能为空对象, 届时整行隐藏)
// ---------------------------------------------------------------------------

const poolRows = computed(() => {
  const pool = metrics.value?.pool_stats
  if (!pool || typeof pool !== 'object' || !Object.keys(pool).length) return []
  const rows: { label: string; value: string }[] = []
  // 0ms 只在确实发过请求时才有意义; 无流量时 avg_latency=0 是"无数据"。
  // 池请求数与上方"总请求数"是两个口径, 不再重复展示。
  const totalRequests = typeof pool.total_requests === 'number' ? pool.total_requests : 0
  const latency = pool.avg_latency_ms
  if (totalRequests > 0 && typeof latency === 'number') {
    rows.push({ label: '平均延迟', value: `${latency} ms` })
  }
  if (typeof pool.total_errors === 'number') {
    rows.push({ label: '池错误', value: String(pool.total_errors) })
  }
  const health = pool.server_health
  if (health && typeof health === 'object') {
    rows.push({ label: '已登记服务器', value: String(Object.keys(health).length) })
  }
  return rows
})

// ---------------------------------------------------------------------------
// load + management actions
// ---------------------------------------------------------------------------

async function load(manual = false): Promise<void> {
  if (inFlight) {
    if (manual) manualLoading.value = true
    return
  }
  inFlight = true
  const firstLoad = metrics.value === null && respCache.value === null
  if (firstLoad) initialLoading.value = true
  if (manual) manualLoading.value = true
  loadError.value = ''

  // 两源独立结算: 单上游指标失败不再拖垮网关级响应缓存卡片。
  const [metricsResult, statusResult] = await Promise.allSettled([
    getUpstreamMetrics('mootdx2'),
    getStatus(),
  ])

  const sample: TrendSample = {
    l1Rate: null,
    l1Size: null,
    respRate: null,
    liveEntries: null,
  }

  if (metricsResult.status === 'fulfilled') {
    const m = metricsResult.value.metrics
    metrics.value = m
    l1LoadError.value = ''
    sample.l1Rate = m.daily_l1?.hit_rate ?? null
    sample.l1Size = m.daily_l1?.size ?? null
  } else {
    l1LoadError.value = errorMessage(metricsResult.reason, '无法加载 MooTDX2 指标')
  }

  if (statusResult.status === 'fulfilled') {
    const status = statusResult.value
    respCache.value = status.cache
    gatewayPid.value = status.pid ?? 0
    respLoadError.value = ''
    const c = status.cache
    // 异进程 stats 不进趋势, 避免旧比率污染当前序列
    const foreign = c.stats_pid != null && c.stats_pid !== (status.pid ?? 0)
    sample.respRate = !foreign && c.hit_rate != null ? c.hit_rate : null
    sample.liveEntries = c.live_entries ?? null
    markFetched()
  } else {
    respLoadError.value = errorMessage(statusResult.reason, '请检查网关是否运行')
    loadError.value = respLoadError.value
    resetFetched()
  }

  samples.value = [...samples.value, sample].slice(-MAX_SAMPLES)
  initialLoading.value = false
  manualLoading.value = false
  inFlight = false

  // breakdown 端点独立加载: 旧网关无此路由时不拖垮整页
  breakdownLoading.value = true
  try {
    const resp = await getCacheToolBreakdown()
    breakdownRows.value = resp.tools
    breakdownTotalTools.value = resp.total_tools
  } catch {
    breakdownRows.value = []
    breakdownTotalTools.value = 0
  } finally {
    breakdownLoading.value = false
  }
}

async function onResetL1(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '确认清空 mootdx2 全部 L1 内存缓存 (日K / 板块扫描 / 异动扫描)? 下次请求将重新从通达信拉取。',
      '重置 L1 缓存',
      { confirmButtonText: '重置', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  resetL1Loading.value = true
  try {
    await resetUpstreamL1('mootdx2')
    ElMessage.success('L1 缓存已重置')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error, '重置失败'))
  } finally {
    resetL1Loading.value = false
  }
}

async function onCleanup(): Promise<void> {
  cleanupLoading.value = true
  try {
    const resp = await cleanupCache()
    ElMessage.success(`已清理 ${resp.deleted} 条过期条目`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error, '清理失败'))
  } finally {
    cleanupLoading.value = false
  }
}

async function onVacuum(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      'VACUUM 会短暂锁定缓存数据库并重建文件, 确认整理?',
      '整理缓存数据库',
      { confirmButtonText: '整理', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  vacuumLoading.value = true
  try {
    const resp = await vacuumCache()
    ElMessage.success(`整理完成, 数据库 ${resp.db_size_mb} MB`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error, '整理失败'))
  } finally {
    vacuumLoading.value = false
  }
}

async function onClear(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '将删除 logs/cache.db 中的全部缓存条目并归零命中率 (包括历史冻结数据), 操作不可恢复。确认清空?',
      '清空全部响应缓存',
      {
        confirmButtonText: '全部清空',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  clearLoading.value = true
  try {
    await clearCache()
    ElMessage.success('响应缓存已清空')
    samples.value = []
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error, '清空失败'))
  } finally {
    clearLoading.value = false
  }
}
</script>

<style scoped>
.l1-cache-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 页头 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

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

/* 时段条: 按时相给整条轻底色 + 同色边框, 不只是圆点变色 */
.session-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
  font-size: 13px;
}

.phase-morning.session-bar,
.phase-afternoon.session-bar {
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-7);
}

.phase-pre_open.session-bar,
.phase-auction.session-bar,
.phase-lunch.session-bar {
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-7);
}

.phase-closed.session-bar,
.phase-weekend.session-bar {
  background: var(--el-color-info-light-9);
  border-color: var(--el-color-info-light-7);
}

.session-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
}

.session-name {
  font-weight: 600;
}

.session-range {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.session-spacer {
  flex: 1;
}

.session-ttl {
  font-size: 12px;
  color: var(--el-text-color-regular);
  font-variant-numeric: tabular-nums;
}

.phase-morning .session-dot,
.phase-afternoon .session-dot {
  background: var(--el-color-success);
  box-shadow: 0 0 0 3px var(--el-color-success-light-9);
}

.phase-pre_open .session-dot,
.phase-auction .session-dot,
.phase-lunch .session-dot {
  background: var(--el-color-warning);
  box-shadow: 0 0 0 3px var(--el-color-warning-light-9);
}

.phase-closed .session-dot,
.phase-weekend .session-dot {
  background: var(--el-color-info);
  box-shadow: 0 0 0 3px var(--el-color-info-light-9);
}

.session-note {
  margin: -4px 2px 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.panel-card {
  border-radius: 8px;
}

.card-alert {
  margin-bottom: 12px;
}

.card-toolbar {
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

.mgmt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

/* 危险按钮与常规操作拉开层级, 折行时自动靠右 */
.danger-action {
  margin-left: auto;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  min-height: 96px;
}

.hitrate-card {
  grid-column: span 2;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.hitrate-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.hitrate-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.hitrate-bar-empty {
  height: 10px;
  border-radius: 5px;
  background: var(--el-fill-color-light);
}

.hitrate-meta {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.hitrate-meta .hint {
  color: var(--el-text-color-secondary);
}

.mini-stat {
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.mini-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.mini-value {
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 7px;
  font-variant-numeric: tabular-nums;
}

.mini-value .on { color: var(--el-color-success); }
.mini-value .off { color: var(--el-text-color-secondary); }

.mini-hint {
  font-size: 11px;
  /* placeholder(#a8abb2) 在 11px 下对比度仅 2.4:1, 提到 regular */
  color: var(--el-text-color-regular);
  margin-top: 2px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.status-online {
  background: var(--el-color-success);
  box-shadow: 0 0 0 3px var(--el-color-success-light-9);
}

.status-disabled {
  background: var(--el-text-color-placeholder);
}

/* 趋势: 两卡统一为同高两列; 网格内 trend-row 不再叠加自身 margin */
.trend-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

.trend-grid .trend-row {
  margin-top: 0;
}

.trend-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.trend-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  width: 70px;
  flex-shrink: 0;
}

.trend-row .sparkline {
  flex: 1;
}

/* 额外 L1 块 */
.l1-extra-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

/* 连接池: 窄屏可换行, 不再溢出 */
.pool-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  font-size: 12px;
}

.pool-title {
  font-weight: 600;
}

.pool-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.pool-label {
  color: var(--el-text-color-secondary);
}

/* 分布表截断说明 */
.trunc-note {
  margin: 8px 2px 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* 折叠的配置常量 */
.constants-details {
  margin-top: 12px;
}

.constants-details summary {
  cursor: pointer;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.constants-details summary:hover {
  color: var(--el-text-color-primary);
}

.off-hint {
  margin: 10px 2px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kv-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 10px 14px;
  margin-top: 10px;
}

.kv-key {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.kv-val {
  font-size: 13px;
}

@media (max-width: 860px) {
  .trend-grid,
  .l1-extra-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 700px) {
  .hitrate-card {
    grid-column: span 1;
  }

  .session-spacer {
    display: none;
  }

  .session-bar {
    flex-wrap: wrap;
  }
}
</style>
