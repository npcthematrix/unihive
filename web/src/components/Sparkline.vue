<template>
  <div class="sparkline" :style="{ height: `${height}px` }">
    <svg
      v-if="geometry"
      :viewBox="`0 0 ${W} ${H}`"
      preserveAspectRatio="none"
      class="sparkline-svg"
      aria-hidden="true"
    >
      <polyline
        :points="geometry.line"
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
 * 数据点 <2 时显示占位。
 */
const props = withDefaults(
  defineProps<{
    points: number[]
    color?: string
    height?: number
  }>(),
  {
    color: 'var(--el-color-primary)',
    height: 30,
  },
)

const W = 100
const H = 30

const geometry = computed(() => {
  const pts = props.points
  if (pts.length < 2) return null
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  // 全程等值时给 ±1 虚拟跨度, 避免画成贴顶直线
  const span = max - min || 1
  const coords = pts.map((value, i) => {
    const x = (i / (pts.length - 1)) * W
    const y = H - 3 - ((value - min) / span) * (H - 6)
    return [Number(x.toFixed(2)), Number(y.toFixed(2))]
  })
  return {
    line: coords.map(([x, y]) => `${x},${y}`).join(' '),
    last: coords[coords.length - 1],
  }
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
