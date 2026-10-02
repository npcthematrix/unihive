<template>
  <div class="hitrate-card">
    <div class="hitrate-head">
      <span class="mini-label">命中率</span>
      <span class="hitrate-head-right">
        <span class="hitrate-value">{{ text }}</span>
        <!-- H1: 非颜色 fallback (色盲/灰度) -->
        <span
          v-if="tierLabel"
          class="hitrate-tier"
          :class="`tier-${tier}`"
          :aria-label="`${tierLabel} (${text})`"
        >{{ tierLabel }}</span>
      </span>
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
    /** H1: 文字 tier (良好/一般/较低) — 非颜色 fallback, 色盲/灰度可读 */
    tier?: 'good' | 'mid' | 'low' | null
    tierLabel?: string | null
    hits: number | string
    misses: number | string
    hint: string
  }>(),
  { color: undefined, tier: undefined, tierLabel: null },
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

.hitrate-head-right {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.hitrate-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

/* H1: tier 文字徽标 — 颜色与 rate 类同色, 兼顾视觉一致与非颜色 fallback */
.hitrate-tier {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid currentColor;
  line-height: 1.4;
}

.hitrate-tier.tier-good { color: var(--el-color-success); }
.hitrate-tier.tier-mid  { color: var(--el-color-warning); }
.hitrate-tier.tier-low  { color: var(--el-color-danger); }

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
