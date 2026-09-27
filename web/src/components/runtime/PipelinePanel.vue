<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="pipeline-toolbar">
        <h2 class="card-title">并发管线
          <small class="sub">容量 · 占用 · 排队 (实时)</small>
        </h2>
        <span class="pipeline-legend">
          <span class="legend-item"><i class="dot dot-active" />执行中</span>
          <span class="legend-item"><i class="dot" />空闲容量</span>
          <span class="legend-item"><i class="dot dot-queue" />排队</span>
        </span>
      </div>
    </template>

    <div class="pipeline-list">
      <div v-for="p in pipelines" :key="p.component" class="pipeline-block">
        <div class="pipeline-head">
          <span class="pipeline-name">{{ p.component }}</span>
          <span class="pipeline-summary">{{ p.summary }}</span>
        </div>
        <div class="layer-list">
          <div v-for="layer in p.layers" :key="layer.label" class="layer-row">
            <span class="layer-label">{{ layer.label }}</span>
            <div class="layer-bar">
              <div class="bar-track">
                <div
                  class="bar-active"
                  :style="{ width: `${layer.activePct}%` }"
                />
              </div>
              <span v-if="layer.queued > 0" class="bar-queue">
                +{{ layer.queued }} 排队
              </span>
            </div>
            <span class="layer-num" :class="{ busy: layer.active > 0 || layer.queued > 0 }">
              {{ layer.active }}/{{ layer.capacity }}
              <span v-if="layer.waiters > 0" class="layer-waiters">
                · {{ layer.waiters }} 等待
              </span>
              <span v-if="layer.total > 0 || layer.peak > 0" class="layer-cumul">
                累计 {{ layer.total }} · 峰 {{ layer.peak }}
              </span>
            </span>
          </div>
        </div>
        <p v-if="p.note" class="pipeline-note">{{ p.note }}</p>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type {
  UpstreamMetrics,
  ThsdkMetrics,
  ExecutorSnapshot,
} from '@/api/upstream-metrics'

/** 并发管线面板: 每个组件按 线程池/闸门 层展示容量、占用、排队。 */
const props = defineProps<{
  mootdx: UpstreamMetrics | null
  tdxQuant: UpstreamMetrics | null
  thsdk: ThsdkMetrics | null
}>()

interface Layer {
  label: string
  capacity: number
  active: number
  queued: number
  waiters: number
  total: number
  peak: number
  activePct: number
}

/** 容量上限下, 已用/上限 的百分比 (1 位小数, 上限 100)。容量非正视为 0 */
function activePctOf(active: number, capacity: number): number {
  if (capacity <= 0) return 0
  return Math.min(100, Number(((active / capacity) * 100).toFixed(1)))
}

function poolLayer(label: string, pool?: ExecutorSnapshot | null): Layer | null {
  if (!pool) return null
  // 容量是上限; 已创建 worker 可能 < 容量, 进度仍按容量算口径统一。
  return {
    label,
    capacity: pool.capacity,
    active: pool.active,
    queued: pool.queued,
    waiters: 0,
    total: pool.total ?? 0,
    peak: pool.peak ?? 0,
    activePct: activePctOf(pool.active, pool.capacity),
  }
}

const pipelines = computed(() => {
  const result: {
    component: string
    layers: Layer[]
    summary: string
    note?: string
  }[] = []

  // ---- MooTDX2 ----
  const mootdxLayers: Layer[] = []
  const tpool = poolLayer('阻塞线程池', props.mootdx?.thread_pool)
  if (tpool) mootdxLayers.push(tpool)
  const gate = props.mootdx?.reader_gate
  if (gate) {
    mootdxLayers.push({
      label: 'Reader 闸门',
      capacity: gate.capacity,
      active: gate.acquired,
      queued: 0,
      waiters: gate.waiters,
      total: gate.total ?? 0,
      peak: gate.peak ?? 0,
      activePct: activePctOf(gate.acquired, gate.capacity),
    })
  }
  const mBusy = mootdxLayers.reduce(
    (sum, l) => sum + l.active + l.queued + l.waiters, 0,
  )
  result.push({
    component: 'MooTDX2',
    layers: mootdxLayers,
    summary: mBusy > 0 ? `${mBusy} 个并发进行/等待中` : '空闲',
    note: mootdxLayers.length ? undefined : '指标未加载',
  })

  // ---- TDX 量化终端 ----
  // Route A: 与 mootdx2 拆开, 用独立阻塞线程池。tdx_quant 不在 client 层维护提交计数,
  // 故无累计/峰值层 (只报实时容量/占用/排队)。
  const tdq = props.tdxQuant
  const tdqPool = poolLayer('阻塞线程池', tdq?.thread_pool)
  const tdqLayers: Layer[] = tdqPool ? [tdqPool] : []
  const tdqBusy = (tdqPool?.active ?? 0) + (tdqPool?.queued ?? 0)
  result.push({
    component: 'TDX 量化终端',
    layers: tdqLayers,
    summary: tdqBusy > 0 ? `${tdqPool?.active} 执行 · ${tdqPool?.queued ?? 0} 排队` : '空闲',
    note: tdqLayers.length ? undefined : '指标未加载',
  })

  // ---- THSDK ----
  const t = props.thsdk
  const thsdkPool = poolLayer('单 worker 池', t?.worker_pool)
  const tLayers: Layer[] = thsdkPool ? [thsdkPool] : []
  const busy = (thsdkPool?.active ?? 0) + (thsdkPool?.queued ?? 0)
  let note: string | undefined
  if (t?.circuit_breaker === 'open') {
    note = `熔断中 · ${t.cb_cooldown_remaining_sec ?? 0}s 后自动探活`
  } else if (t?.circuit_breaker === 'half_open') {
    note = '熔断半开 · 探针执行中'
  } else if (t && t.throttle_remaining_sec > 0) {
    note = `限频冷却 · 剩 ${t.throttle_remaining_sec}s`
  }
  result.push({
    component: 'THSDK',
    layers: tLayers,
    summary: busy > 0 ? `${thsdkPool?.active} 执行 · ${thsdkPool?.queued ?? 0} 排队` : '空闲',
    note: note ?? (tLayers.length ? undefined : '状态未加载'),
  })

  return result
})
</script>

<style scoped>
.panel-card {
  border-radius: 8px;
}

.pipeline-toolbar {
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

.pipeline-legend {
  display: inline-flex;
  gap: 14px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: var(--el-fill-color) ;
  border: 1px solid var(--el-border-color);
}

.dot-active {
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.dot-queue {
  background: var(--el-color-warning);
  border-color: var(--el-color-warning);
}

.pipeline-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pipeline-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.pipeline-name {
  font-size: 14px;
  font-weight: 600;
}

.pipeline-summary {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.layer-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.layer-row {
  display: grid;
  grid-template-columns: 96px 1fr 92px;
  align-items: center;
  gap: 12px;
}

.layer-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.layer-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.bar-track {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  overflow: hidden;
}

.bar-active {
  height: 100%;
  border-radius: 4px;
  background: var(--el-color-primary);
  transition: width 0.3s ease;
}

.bar-queue {
  font-size: 11px;
  color: var(--el-color-warning);
  white-space: nowrap;
}

.layer-num {
  font-size: 12px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.layer-num.busy {
  color: var(--el-color-primary);
  font-weight: 600;
}

.layer-waiters {
  color: var(--el-color-warning);
}

.layer-cumul {
  display: block;
  margin-top: 2px;
  font-size: 10px;
  font-weight: 400;
  color: var(--el-text-color-placeholder);
}

.pipeline-note {
  margin: 8px 2px 0;
  font-size: 11px;
  color: var(--el-color-warning);
}

@media (max-width: 700px) {
  .layer-row {
    grid-template-columns: 84px 1fr;
  }

  .layer-num {
    grid-column: 2;
    text-align: left;
  }
}
</style>
