<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="pipeline-toolbar">
        <h2 class="card-title">并发
          <small class="sub">容量 · 占用 · 排队 (实时)</small>
        </h2>
        <!-- B1: 排队/等待旧码各给一个色点, 但两点都是 --el-color-warning ——
             图例并排四项里第三、第四项视觉完全相同却标着两个词, 无法区分它声称
             要区分的两件事。这两者在行内本来也不是条形段而是文字, 所以图例直接
             复用行内那两套文字样式, 所见即所得。 -->
        <span class="pipeline-legend">
          <span class="legend-item"><i class="dot dot-active" />执行中</span>
          <span class="legend-item"><i class="dot" />空闲容量</span>
          <span class="legend-item"><span class="bar-queue">+N 排队</span></span>
          <span class="legend-item"><span class="layer-waiters">· N 等待</span></span>
        </span>
      </div>
    </template>

    <p class="pipeline-scope-hint">
      仅展示进程内阻塞调用组件 (MooTDX2 / TDX 量化终端 / THSDK)；
      fuyao (HTTP) 与 omni (SQLite) 为异步 I/O, 不在此列。
    </p>

    <div class="pipeline-list">
      <div v-for="p in pipelines" :key="p.component" class="pipeline-block" :class="{ 'is-primary': p.isPrimary }">
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
            </span>
            <!-- B2: 累计/峰原来挤在 a/c 那个 max-content 格里, 把中间的进度条
                 压短 —— 只有 MooTDX2 上报累计计数, 于是同一页两个组件的条长不一致。
                 改成独立一行后条宽只受容器决定。 -->
            <span v-if="layer.total != null || layer.peak != null" class="layer-cumul">
              累计 {{ layer.total ?? '—' }} · 峰 {{ layer.peak ?? '—' }}
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

/** 并发面板: 每个组件按 线程池/闸门 层展示容量、占用、排队。 */
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
  /** null = 后端未提供该层累计数据 (区别于 0 = 确实无累计)。 */
  total: number | null
  peak: number | null
  activePct: number
}

/** 容量上限下, 已用/上限 的百分比 (1 位小数, 上限 100)。容量非正视为 0 */
function activePctOf(active: number, capacity: number): number {
  if (capacity <= 0) return 0
  return Math.min(100, Number(((active / capacity) * 100).toFixed(1)))
}

/**
 * L8: 「没数据」原来三张卡各写一套 —— MooTDX2/TDX 量化报 summary=未采样 +
 * note=指标未加载, THSDK 却把「指标未加载」写在 summary 里且没有 note, 同一个
 * 状态在不同卡片上既换了位置又换了词。统一成两档, 并给出可行动的 note:
 * props 为 null = 还没轮到这次采样; props 有值但一层都没有 = 后端没上报池快照。
 */
function emptySummary(hasProps: boolean): { summary: string; note: string } {
  return hasProps
    ? { summary: '指标未加载', note: '后端未上报线程池/闸门快照' }
    : { summary: '未采样', note: '本轮尚未取到该组件的指标' }
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
    total: pool.total ?? null,
    peak: pool.peak ?? null,
    activePct: activePctOf(pool.active, pool.capacity),
  }
}

const pipelines = computed(() => {
  const result: {
    component: string
    isPrimary?: boolean
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
      total: gate.total ?? null,
      peak: gate.peak ?? null,
      activePct: activePctOf(gate.acquired, gate.capacity),
    })
  }
  const mActive = mootdxLayers.reduce((sum, l) => sum + l.active, 0)
  const mQueued = mootdxLayers.reduce((sum, l) => sum + l.queued, 0)
  const mWaiters = mootdxLayers.reduce((sum, l) => sum + l.waiters, 0)
  const mBusy = mActive + mQueued + mWaiters
  const mParts = [`${mActive} 执行`, `${mQueued} 排队`]
  if (mWaiters > 0) mParts.push(`${mWaiters} 等待`)
  const mEmpty = mootdxLayers.length ? null : emptySummary(props.mootdx != null)
  result.push({
    component: 'MooTDX2',
    isPrimary: true,
    layers: mootdxLayers,
    // M2: props 未加载/无 layers 是「未采样」, 不是「空闲」(监控页方向性误导)。
    summary: mEmpty ? mEmpty.summary : (mBusy > 0 ? mParts.join(' · ') : '空闲'),
    note: mEmpty?.note,
  })

  // ---- TDX 量化终端 ----
  // 策略进程 = 官方 1.3.0 多实例; DLL 闸门 = call_limit; 阻塞线程池是父进程
  // 等 Pipe/DLL 的线程, 不是 TdxW 并发路数。
  const tdq = props.tdxQuant
  const tdqLayers: Layer[] = []
  const nInst = Number(tdq?.strategy_instances ?? 0)
  const poolSnap = tdq?.strategy_pool
  if (nInst > 0 || poolSnap) {
    const cap = Number(poolSnap?.size ?? nInst)
    const active = poolSnap
      ? (poolSnap.instances || []).filter((row) => row.busy).length
      : Number(tdq?.call_inflight ?? 0)
    // 条上的占用是 busy; 容量本身就是实例数, 空闲时 0/2 也说明开了两路。
    // total/peak = null: 后端策略实例池不提供累计/峰值统计。
    tdqLayers.push({
      label: nInst > 1 ? '策略进程' : '策略实例',
      capacity: cap,
      active,
      queued: 0,
      waiters: 0,
      total: null,
      peak: null,
      activePct: activePctOf(active, cap),
    })
  }
  const callLimit = Number(tdq?.call_limit ?? 0)
  if (callLimit > 0) {
    const inflight = Number(tdq?.call_inflight ?? 0)
    tdqLayers.push({
      label: 'DLL 闸门',
      capacity: callLimit,
      active: inflight,
      queued: 0,
      waiters: 0,
      // total/peak = null: 后端 DLL 闸门不提供累计/峰值统计。
      total: null,
      peak: null,
      activePct: activePctOf(inflight, callLimit),
    })
  }
  const tdqPool = poolLayer('阻塞线程池', tdq?.thread_pool)
  if (tdqPool) tdqLayers.push(tdqPool)
  const tdqActive = tdqLayers.reduce((s, l) => s + l.active, 0)
  const tdqQueued = tdqLayers.reduce((s, l) => s + l.queued, 0)
  const tdqWaiters = tdqLayers.reduce((s, l) => s + l.waiters, 0)
  const tdqBusy = tdqActive + tdqQueued + tdqWaiters
  const tdqEmpty = tdqLayers.length ? null : emptySummary(props.tdxQuant != null)
  let tdqNote = tdqEmpty?.note
  if (!tdqEmpty) {
    if (nInst > 1) {
      tdqNote = '多策略实例: 阻塞线程池只等子进程; 真正 DLL 在各策略进程里。'
    } else if (nInst === 1) {
      tdqNote = '当前是进程内单实例; 配置 strategy_instances>1 并重启后才会拉子进程。'
    }
  }
  result.push({
    component: 'TDX 量化终端',
    layers: tdqLayers,
    // 统一 summary 格式: a 执行 · b 排队 · c 等待 / 空闲 / 未采样
    summary: tdqEmpty
      ? tdqEmpty.summary
      : (tdqBusy > 0
          ? `${tdqActive} 执行 · ${tdqQueued} 排队${tdqWaiters > 0 ? ` · ${tdqWaiters} 等待` : ''}`
          : '空闲'),
    note: tdqNote,
  })

  // ---- THSDK ----
  // 状态优先: 即使 worker_pool 缺失, 熔断/限频状态仍应展示, 不整卡"未采样"。
  const t = props.thsdk
  const thsdkPool = poolLayer('单 worker 池', t?.worker_pool)
  const tLayers: Layer[] = thsdkPool ? [thsdkPool] : []
  const tActive = thsdkPool?.active ?? 0
  const tQueued = thsdkPool?.queued ?? 0
  // L7: ExecutorSnapshot 没有 waiters 字段, poolLayer 也把它硬编码成 0 ——
  // 原先的 `· N 等待` 分支永远不可达, 删掉以免读者以为 THSDK 会上报等待数。
  const tBusy = tActive + tQueued

  let summary: string
  let note: string | undefined
  if (t?.circuit_breaker === 'open') {
    summary = '熔断中'
    note = `${t.cb_cooldown_remaining_sec ?? 0}s 后自动探活`
  } else if (t?.circuit_breaker === 'half_open') {
    summary = '半开探活'
    note = '探针执行中'
  } else if (t?.throttle_remaining_sec && t.throttle_remaining_sec > 0) {
    summary = '限频冷却'
    note = `剩 ${t.throttle_remaining_sec}s`
  } else {
    const tEmpty = tLayers.length ? null : emptySummary(t != null)
    if (tEmpty) {
      summary = tEmpty.summary
      note = tEmpty.note
    } else {
      summary = tBusy > 0 ? `${tActive} 执行 · ${tQueued} 排队` : '空闲'
    }
  }
  result.push({
    component: 'THSDK',
    layers: tLayers,
    summary,
    note,
  })

  return result
})
</script>

<style scoped>
/* .panel-card 圆角 / .pipeline-toolbar / .card-title / .sub 已收敛到
   styles/main.css (全局)。 */

.pipeline-legend {
  display: inline-flex;
  gap: 14px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

/* 空闲容量 = 进度条 track 的灰色未占用部分, 色点须与 track 一致 */
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
}

.dot-active {
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.pipeline-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pipeline-scope-hint {
  margin: 0 2px 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.pipeline-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.pipeline-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-regular);
}

/* M8: is-primary 绑在 .pipeline-block (模板 :17), 旧选择器 .pipeline-name.is-primary
   永不命中 → 主源强调失效。改后代选择器。 */
.pipeline-block.is-primary .pipeline-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.pipeline-summary {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.layer-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* B2: 累计/峰独占第二行, 不再和 a/c 抢那个 max-content 列 —— 否则只有上报
   累计计数的组件 (MooTDX2) 会把条压短, 同页两个组件条长不一致。 */
.layer-row {
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr) minmax(110px, max-content);
  grid-template-areas:
    'label bar num'
    '.     cumul cumul';
  align-items: center;
  column-gap: 12px;
  row-gap: 2px;
}

.layer-label {
  grid-area: label;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.layer-bar {
  grid-area: bar;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.bar-track {
  flex: 1;
  height: 10px;
  border-radius: 5px;
  background: var(--el-fill-color-light);
  overflow: hidden;
}

.bar-active {
  height: 100%;
  border-radius: 5px;
  background: var(--el-color-primary);
  transition: width 0.3s ease;
}

.bar-queue {
  font-size: 11px;
  color: var(--el-color-warning);
  white-space: nowrap;
}

.layer-num {
  grid-area: num;
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
  grid-area: cumul;
  text-align: right;
  /* 11px = 本仓可读下限 (原 10px 过小) */
  font-size: 11px;
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
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
  }
}
</style>
