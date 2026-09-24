<template>
  <div class="mini-stat">
    <div class="mini-label">{{ label }}</div>
    <div class="mini-value">
      <slot name="value">{{ value }}</slot>
    </div>
    <div v-if="hint" class="mini-hint">{{ hint }}</div>
  </div>
</template>

<script setup lang="ts">
/** 统计网格里的小格: 默认直接显示 value, 复杂内容走 #value 插槽。 */
withDefaults(
  defineProps<{
    label: string
    value?: string | number
    hint?: string
  }>(),
  { value: '—', hint: undefined },
)
</script>

<style scoped>
.mini-stat {
  /* 网格项默认 stretch: 填满行高, 与较高的 HitRateCard 底部对齐 */
  display: flex;
  flex-direction: column;
  height: 100%;
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
  /* 占据 label 以下的剩余空间并垂直居中, 留白在上下均匀分布而非堆在底部 */
  flex: 1;
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 7px;
  font-variant-numeric: tabular-nums;
}

.mini-hint {
  font-size: 11px;
  /* placeholder(#a8abb2) 在 11px 下对比度仅 2.4:1, 提到 regular */
  color: var(--el-text-color-regular);
  margin-top: 6px;
}
</style>
