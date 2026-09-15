<template>
  <div class="l1-cache-page">
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">
            MooTDX2 L1 缓存 <small class="sub">get_daily_local</small>
          </h2>
          <div class="toolbar-actions">
            <span
              class="last-refresh"
              :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
            >
              {{ refreshLabel }}
            </span>
            <el-button :icon="Refresh" :loading="manualLoading" @click="load(true)">
              立即刷新
            </el-button>
            <el-tooltip content="每 10 秒自动刷新" placement="top">
              <span class="auto-toggle">
                <el-switch v-model="autoRefresh" />
                <span class="auto-label">自动刷新</span>
              </span>
            </el-tooltip>
          </div>
        </div>
      </template>

      <el-alert
        v-if="loadError"
        type="error"
        :title="loadError"
        show-icon
        :closable="false"
        class="error-banner"
      />

      <div v-loading="initialLoading" class="stat-grid">
        <!-- 命中率主卡 -->
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
            <span><strong>{{ metrics?.daily_l1?.hits ?? 0 }}</strong> 命中</span>
            <span><strong>{{ metrics?.daily_l1?.misses ?? 0 }}</strong> 未命中</span>
            <span class="hint">{{ hitRateHint }}</span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">状态</div>
          <div class="mini-value">
            <span
              class="status-dot"
              :class="metrics?.daily_l1?.enabled ? 'status-online' : 'status-disabled'"
            />
            <span :class="metrics?.daily_l1?.enabled ? 'on' : 'off'">
              {{ metrics?.daily_l1?.enabled ? '启用' : '停用' }}
            </span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">当前 size</div>
          <div class="mini-value">{{ sizeText }}</div>
          <div class="mini-hint">/ {{ metrics?.daily_l1?.maxsize ?? 2048 }} 上限</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">Inflight keys</div>
          <div class="mini-value">{{ metrics?.daily_l1?.inflight_keys ?? 0 }}</div>
          <div class="mini-hint">single-flight 在途请求</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">TTL</div>
          <div class="mini-value">{{ ttlText }}</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">总请求数</div>
          <div class="mini-value">{{ metrics?.total_requests ?? 0 }}</div>
        </div>
      </div>

      <p v-if="metrics && !metrics.daily_l1?.enabled" class="off-hint">
        L1 缓存未启用 (cachetools 缺失或代码已禁用)
      </p>
    </el-card>

    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">配置常量 <small class="sub">get_daily_local L1</small></h2>
        </div>
      </template>
      <div class="kv-grid">
        <div class="kv-key">缓存算法</div>
        <div class="kv-val"><code>TTLCache(maxsize=2048, ttl=7200s)</code></div>
        <div class="kv-key">缓存 key</div>
        <div class="kv-val"><code>(reader_symbol, adjust)</code> — 仅 <code>adjust="none"</code> 写入</div>
        <div class="kv-key">value 形状</div>
        <div class="kv-val"><code>list[dict]</code> — get_daily_sync 原始 records</div>
        <div class="kv-key">过滤位置</div>
        <div class="kv-val">start_date / end_date 过滤在缓存 records 上做 (O(N) dict 遍历)</div>
        <div class="kv-key">single-flight</div>
        <div class="kv-val">
          <code>asyncio.Future</code> 字典 + <code>asyncio.Lock</code> 索引互斥,
          同 key 并发 miss 只跑 1 次 _get_daily_sync
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { getUpstreamMetrics } from '@/api/upstream-metrics'
import type { UpstreamMetrics } from '@/api/upstream-metrics'

const STALE_THRESHOLD_SEC = 30

const metrics = ref<UpstreamMetrics | null>(null)
const initialLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
const lastFetchSec = ref(0)
const nowSec = ref(Math.floor(Date.now() / 1000))
const autoRefresh = ref(false)

let clockTimer: ReturnType<typeof setInterval> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let inFlight = false

const ageSec = computed(() =>
  lastFetchSec.value ? Math.max(0, nowSec.value - lastFetchSec.value) : 0,
)

const refreshLabel = computed(() => {
  if (loadError.value && lastFetchSec.value === 0) return `刷新失败 (${loadError.value})`
  if (!lastFetchSec.value) return '从未刷新'
  const sec = ageSec.value
  if (sec < 5) return '刚刚刷新'
  if (sec < 60) return `${sec} 秒前刷新`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前刷新`
  return `${Math.floor(sec / 3600)} 小时前刷新`
})

const hitRatePct = computed(() => {
  const rate = metrics.value?.daily_l1?.hit_rate
  return rate == null ? null : Number((rate * 100).toFixed(1))
})

const hitRateText = computed(() => {
  const rate = metrics.value?.daily_l1?.hit_rate
  return rate == null ? '—' : `${(rate * 100).toFixed(1)}%`
})

const hitRateHint = computed(() => {
  const l1 = metrics.value?.daily_l1
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
  const s = metrics.value?.daily_l1?.size
  return s == null ? '—' : String(s)
})

const ttlText = computed(() => {
  const t = metrics.value?.daily_l1?.ttl_seconds
  if (t == null) return '—'
  if (t >= 3600) return `${Math.floor(t / 3600)}h`
  if (t >= 60) return `${Math.floor(t / 60)}min`
  return `${t}s`
})

async function load(manual = false): Promise<void> {
  if (inFlight) {
    if (manual) manualLoading.value = true
    return
  }
  inFlight = true
  const firstLoad = metrics.value === null
  if (firstLoad) initialLoading.value = true
  if (manual) manualLoading.value = true
  loadError.value = ''
  try {
    const resp = await getUpstreamMetrics('mootdx2')
    metrics.value = resp.metrics
    lastFetchSec.value = Math.floor(Date.now() / 1000)
  } catch (error) {
    loadError.value = errorMessage(error, '请检查网关是否运行')
    lastFetchSec.value = 0
  } finally {
    initialLoading.value = false
    manualLoading.value = false
    inFlight = false
  }
}

watch(autoRefresh, (on) => {
  if (!on) {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    return
  }
  pollTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    void load(false)
  }, 10_000)
})

function onVisibility(): void {
  if (!document.hidden) {
    nowSec.value = Math.floor(Date.now() / 1000)
    if (lastFetchSec.value && nowSec.value - lastFetchSec.value > STALE_THRESHOLD_SEC) {
      void load(false)
    }
  }
}

onMounted(() => {
  void load(true)
  clockTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    nowSec.value = Math.floor(Date.now() / 1000)
  }, 1000)
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (pollTimer) clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<style scoped>
.l1-cache-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
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

.toolbar-actions {
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

.error-banner {
  margin-bottom: 12px;
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
  color: var(--el-text-color-placeholder);
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

.off-hint {
  margin: 10px 2px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kv-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 10px 14px;
}

.kv-key {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.kv-val {
  font-size: 13px;
}

@media (max-width: 700px) {
  .hitrate-card {
    grid-column: span 1;
  }
}
</style>
