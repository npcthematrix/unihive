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

    <!-- 上海市场时段条: 短档此刻的生效语义 -->
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

    <el-alert
      v-if="loadError"
      type="error"
      :title="loadError"
      show-icon
      :closable="false"
      class="error-banner"
    />

    <!-- ================= MooTDX2 L1 ================= -->
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
      </div>

      <div v-if="dailyL1?.enabled" class="trend-row">
        <span class="trend-label">命中率趋势</span>
        <Sparkline :points="l1RateSeries" color="var(--el-color-primary)" />
      </div>

      <div class="l1-extra-grid">
        <L1CacheBlock
          title="板块扫描 L1"
          sub="stock_top_board · 10s 档"
          :stats="metrics?.top_board_l1"
        />
        <L1CacheBlock
          title="异动扫描 L1"
          sub="stock_unusual · 10s 档"
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
          <div class="kv-val"><code>TTLCache(maxsize=2048, ttl=次日9:00)</code> — 当日有效</div>
          <div class="kv-key">缓存 key</div>
          <div class="kv-val"><code>(date, symbol, adjust, start_date, end_date)</code> — 仅 <code>adjust="none"</code> 写入</div>
          <div class="kv-key">value 形状</div>
          <div class="kv-val"><code>list[dict]</code> — get_daily_sync 原始 records</div>
          <div class="kv-key">过期策略</div>
          <div class="kv-val">次日 9:00 自动过期，避免陈旧行情</div>
          <div class="kv-key">并发控制</div>
          <div class="kv-val">
            <code>asyncio.Semaphore(4)</code> 限制最多 4 个 Reader 并发执行
          </div>
        </div>
      </details>

      <p v-if="metrics && !dailyL1?.enabled" class="off-hint">
        L1 缓存未启用 (cachetools 缺失或代码已禁用)
      </p>
    </el-card>

    <!-- ================= SQLite 响应缓存 ================= -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">
            响应缓存 <small class="sub">SQLite · logs/cache.db</small>
          </h2>
          <div class="mgmt-actions">
            <span class="timestamp">{{ respCacheTimeText }}</span>
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
            <el-button size="small" type="danger" plain :loading="clearLoading" @click="onClear">
              清空全部
            </el-button>
          </div>
        </div>
      </template>

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
          <Sparkline :points="respRateSeries" color="var(--el-color-success)" />
        </div>
        <div class="trend-row">
          <span class="trend-label">活跃条目</span>
          <Sparkline :points="liveEntriesSeries" color="var(--el-color-primary)" />
        </div>
      </div>

      <CacheBreakdownTable :rows="breakdownRows" :loading="breakdownLoading" />

      <p v-if="respCache && !respCache.enabled" class="off-hint">
        {{ respCache.error || '缓存数据库未创建' }}
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { getUpstreamMetrics } from '@/api/upstream-metrics'
import type { UpstreamMetrics } from '@/api/upstream-metrics'
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
import { formatCountdown, marketSession } from '@/utils/market-session'
import Sparkline from '@/components/Sparkline.vue'
import L1CacheBlock from '@/components/L1CacheBlock.vue'
import CacheBreakdownTable from '@/components/CacheBreakdownTable.vue'

const metrics = ref<UpstreamMetrics | null>(null)
const respCache = ref<CacheStats | null>(null)
const initialLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')

let inFlight = false

/** 自动刷新 + 时钟 + tab 可见时陈旧重拉 — 见 composables/useAutoRefresh.ts */
const { autoRefresh, lastFetchSec, ageSec, markFetched, resetFetched } = useAutoRefresh({
  onPoll: () => { void load(false) },
})

// 1s 心跳: 驱动时段倒计时
const nowTick = ref(new Date())
const tickTimer = window.setInterval(() => { nowTick.value = new Date() }, 1000)
onUnmounted(() => window.clearInterval(tickTimer))
const session = computed(() => marketSession(nowTick.value))

// 趋势采样环 (最多 60 点, 与 10s 轮询配合 ≈ 最近 10 分钟)
interface TrendSample {
  l1Rate: number | null
  respRate: number | null
  liveEntries: number | null
}
const MAX_SAMPLES = 60
const samples = ref<TrendSample[]>([])
const breakdownRows = ref<CacheToolBreakdownRow[]>([])
const breakdownLoading = ref(false)

// 管理操作 loading
const resetL1Loading = ref(false)
const cleanupLoading = ref(false)
const vacuumLoading = ref(false)
const clearLoading = ref(false)

const refreshLabel = computed(() => {
  if (loadError.value && lastFetchSec.value === 0) return `刷新失败 (${loadError.value})`
  if (!lastFetchSec.value) return '从未刷新'
  const sec = ageSec.value
  if (sec < 5) return '刚刚刷新'
  if (sec < 60) return `${sec} 秒前刷新`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前刷新`
  return `${Math.floor(sec / 3600)} 小时前刷新`
})

const dailyL1 = computed(() => metrics.value?.daily_l1 ?? null)

const hitRatePct = computed(() => {
  const rate = dailyL1.value?.hit_rate
  return rate == null ? null : Number((rate * 100).toFixed(1))
})

const hitRateText = computed(() => {
  const rate = dailyL1.value?.hit_rate
  return rate == null ? '—' : `${(rate * 100).toFixed(1)}%`
})

const hitRateHint = computed(() => {
  const l1 = dailyL1.value
  if (!l1?.enabled) return l1 ? '缓存已停用' : '尚未拉到指标'
  const total = (l1.hits ?? 0) + (l1.misses ?? 0)
  return total > 0 ? `共 ${total} 次请求` : '网关启动后累计 (尚未请求)'
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
  if (t == null) return '—'
  if (t >= 3600) return `${Math.floor(t / 3600)}h`
  if (t >= 60) return `${Math.floor(t / 60)}min`
  return `${t}s`
})

// 趋势序列: 过滤掉 null 采样
function definedSeries(pick: (sample: TrendSample) => number | null): number[] {
  return samples.value
    .map(pick)
    .filter((value): value is number => value != null)
}

const l1RateSeries = computed(() =>
  definedSeries((sample) => sample.l1Rate),
)

// ---------------------------------------------------------------------------
// SQLite 响应缓存派生 (字段语义见后端 console_api.get_cache_stats)
// ---------------------------------------------------------------------------

const gatewayPid = ref(0)

const respCacheTimeText = computed(() =>
  respCache.value?.timestamp
    ? new Date(respCache.value.timestamp * 1000).toLocaleTimeString('zh-CN')
    : '-',
)

const respHitRatePct = computed(() => {
  const rate = respCache.value?.hit_rate
  return rate == null ? null : Number((rate * 100).toFixed(1))
})

const respHitRateText = computed(() => {
  const rate = respCache.value?.hit_rate
  return rate == null ? '—' : `${(rate * 100).toFixed(1)}%`
})

const respHitRateHint = computed(() => {
  const c = respCache.value
  if (!c?.enabled) return c?.error || '缓存数据库未创建'
  if (c.stats_pid != null && c.stats_pid !== gatewayPid.value) {
    return '命中统计来自上一网关进程'
  }
  const total = (c.hits ?? 0) + (c.misses ?? 0)
  return total > 0 && c.hit_rate != null
    ? `共 ${total} 次请求`
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
  definedSeries((sample) => sample.respRate),
)

const liveEntriesSeries = computed(() =>
  definedSeries((sample) => sample.liveEntries),
)

// ---------------------------------------------------------------------------
// mootdx2 连接池摘要 (pool_stats 可能为空对象, 届时整行隐藏)
// ---------------------------------------------------------------------------

const poolRows = computed(() => {
  const pool = metrics.value?.pool_stats
  if (!pool || typeof pool !== 'object' || !Object.keys(pool).length) return []
  const rows: { label: string; value: string }[] = []
  const latency = pool.avg_latency_ms
  if (typeof latency === 'number') {
    rows.push({ label: '平均延迟', value: `${latency} ms` })
  }
  if (typeof pool.total_requests === 'number') {
    rows.push({ label: '池请求', value: String(pool.total_requests) })
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
  try {
    // L1 指标与 SQLite 缓存统计同源并发, 任一失败统一进错误面
    const [metricsResp, status] = await Promise.all([
      getUpstreamMetrics('mootdx2'),
      getStatus(),
    ])
    metrics.value = metricsResp.metrics
    respCache.value = status.cache
    gatewayPid.value = status.pid ?? 0
    const sample: TrendSample = {
      l1Rate: metricsResp.metrics.daily_l1?.hit_rate ?? null,
      respRate: status.cache.hit_rate ?? null,
      liveEntries: status.cache.live_entries ?? null,
    }
    samples.value = [...samples.value, sample].slice(-MAX_SAMPLES)
    markFetched()
  } catch (error) {
    loadError.value = errorMessage(error, '请检查网关是否运行')
    resetFetched()
  } finally {
    initialLoading.value = false
    manualLoading.value = false
    inFlight = false
  }

  // breakdown 端点独立加载: 旧网关无此路由时不拖垮整页
  breakdownLoading.value = true
  try {
    const resp = await getCacheToolBreakdown()
    breakdownRows.value = resp.tools
  } catch {
    breakdownRows.value = []
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
      '将删除 logs/cache.db 中的全部缓存条目 (包括历史冻结数据), 操作不可恢复。确认清空?',
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

/* 时段条 */
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
.phase-lunch .session-dot {
  background: var(--el-color-warning);
  box-shadow: 0 0 0 3px var(--el-color-warning-light-9);
}

.error-banner {
  margin-bottom: 0;
}

.panel-card {
  border-radius: 8px;
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

/* 趋势 */
.trend-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
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

/* 连接池 */
.pool-row {
  display: flex;
  align-items: center;
  gap: 16px;
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

.timestamp {
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
