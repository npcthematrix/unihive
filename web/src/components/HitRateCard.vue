<template>
  <div class="hitrate-card">
    <div class="hitrate-head">
      <span class="mini-label">命中率</span>
      <span class="hitrate-value">{{ text }}</span>
    </div>
    <el-progress
      v-if="pct !== null"
      :percentage="pct"
      :stroke-width="10"
      :show-text="false"
      :color="color"
    />
    <div v-else class="hitrate-bar-empty" />
    <div class="hitrate-meta">
      <span><strong>{{ hits }}</strong> 命中</span>
      <span><strong>{{ misses }}</strong> 未命中</span>
      <span class="hint">{{ hint }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 命中率卡片: 响应缓存 / MooTDX2 L1 两处共用 (派生逻辑见 useHitRate)。 */
withDefaults(
  defineProps<{
    pct: number | null
    text: string
    color?: string
    hits: number | string
    misses: number | string
    hint: string
  }>(),
  { color: undefined },
)
</script>

<style scoped>
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

.mini-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 560px) {
  .hitrate-card {
    grid-column: span 1;
  }
}
</style>
