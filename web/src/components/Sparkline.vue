<template>
  <div class="sparkline" :style="{ height: `${height}px` }">
    <svg
      v-if="geometry"
      :viewBox="`0 0 ${W} ${H}`"
      preserveAspectRatio="none"
      class="sparkline-svg"
      role="img"
      :aria-label="ariaLabel ?? defaultAriaLabel"
    >
      <polyline
        v-for="(line, idx) in geometry.segments"
        :key="idx"
        :points="line"
        fill="none"
        :stroke="color"
        stroke-width="1.5"
        vector-effect="non-scaling-stroke"
        stroke-linejoin="round"
      />
      <circle
        :cx="geometry.last[0]"
        :cy="geometry.last[1]"
        r="1.6"
        :fill="color"
      />
    </svg>
    <span v-else class="sparkline-empty">采样不足</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 内联 SVG 迷你趋势线。viewBox 横向固定 100, preserveAspectRatio=none
 * 让线铺满容器宽度; non-scaling-stroke 保证拉伸后线宽不糊。
 *
 * - points 支持 null: null 采样处断线, 且 x 按**全样本序号**定位 —
 *   轮询失败的时段在图上留空, 不会因过滤 null 而把时间轴压缩;
 * - 有效数据点 <2 时显示占位;
 * - role=img + aria-label 给读屏用户首尾值 (默认自动生成, 可覆盖)。
 */
const props = withDefaults(
  defineProps<{
    points: (number | null)[]
    color?: string
    height?: number
    /** 可选: 自定义无障碍标签; 缺省用首尾值自动生成。 */
    ariaLabel?: string
  }>(),
  {
    color: 'var(--el-color-primary)',
    height: 30,
    ariaLabel: undefined,
  },
)

const W = 100
const H = 30

const geometry = computed(() => {
  const pts = props.points
  const defined = pts.filter((v): v is number => v != null)
  if (pts.length < 2 || defined.length < 2) return null
  const min = Math.min(...defined)
  const max = Math.max(...defined)
  // 全程等值时给 ±1 虚拟跨度, 避免画成贴顶直线
  const span = max - min || 1
  const xy = (value: number, index: number): [number, number] => [
    Number(((index / (pts.length - 1)) * W).toFixed(2)),
    Number((H - 3 - ((value - min) / span) * (H - 6)).toFixed(2)),
  ]

  // 连续有效点各自成段; 遇 null 断开, 段间空白即故障/无数据时长。
  const segments: string[] = []
  let run: [number, number][] = []
  let lastPoint: [number, number] | null = null
  pts.forEach((value, index) => {
    if (value == null) {
      if (run.length >= 2) {
        segments.push(run.map(([x, y]) => `${x},${y}`).join(' '))
      }
      run = []
    } else {
      const point = xy(value, index)
      run.push(point)
      lastPoint = point
    }
  })
  if (run.length >= 2) {
    segments.push(run.map(([x, y]) => `${x},${y}`).join(' '))
  }
  if (!segments.length || lastPoint === null) return null
  return {
    segments,
    last: lastPoint,
    firstValue: defined[0],
    lastValue: defined[defined.length - 1],
    count: defined.length,
  }
})

const defaultAriaLabel = computed(() => {
  const g = geometry.value
  if (!g) return '趋势线: 采样不足'
  return `趋势: ${g.firstValue} → ${g.lastValue}, 共 ${g.count} 个采样点`
})
</script>

<style scoped>
.sparkline {
  width: 100%;
}

.sparkline-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.sparkline-empty {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  line-height: 30px;
}
</style>
