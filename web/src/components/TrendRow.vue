<template>
  <div class="trend-row">
    <span class="trend-label">{{ label }}</span>
    <Sparkline :points="points" :color="color" :aria-label="chartAriaLabel" />
  </div>
</template>

<script setup lang="ts">
import Sparkline from '@/components/Sparkline.vue'

/** 一行迷你趋势: 固定宽标签 + 自适应 Sparkline。 */
defineProps<{
  label: string
  points: (number | null)[]
  color: string
  /** 不用 ariaLabel: 调用处写 aria-label 会被 vue-tsc 当原生 aria 属性,
   * 不映射回 prop。 */
  chartAriaLabel: string
}>()
</script>

<style scoped>
.trend-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.trend-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  width: 70px;
  flex-shrink: 0;
}

.trend-row :deep(.sparkline) {
  flex: 1;
}
</style>
