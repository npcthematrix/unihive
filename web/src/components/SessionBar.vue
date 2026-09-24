<template>
  <div>
    <!-- 上海市场时段条: 通用短档此刻的生效语义 -->
    <div class="session-bar" :class="`phase-${session.phase}`">
      <span class="session-dot" />
      <span class="session-name">{{ session.label }}</span>
      <span class="session-range">{{ session.range }}</span>
      <span class="session-spacer" />
      <span v-if="session.shortTtlActive" class="session-ttl">
        短档按配置值生效 · 距 {{ session.boundaryLabel }}
        {{ formatCountdown(session.secondsToBoundary) }}
      </span>
      <span v-else class="session-ttl">
        短档缓存到 {{ session.boundaryLabel }} · 还有
        {{ formatCountdown(session.secondsToBoundary) }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { formatCountdown, marketSession } from '@/utils/market-session'

/** 市场时段条 + 特例说明。session 由视图的 1s 时钟驱动。 */
defineProps<{
  session: ReturnType<typeof marketSession>
}>()
</script>

<style scoped>
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

.phase-morning.session-bar,
.phase-afternoon.session-bar {
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-7);
}

.phase-pre_open.session-bar,
.phase-auction.session-bar,
.phase-lunch.session-bar {
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-7);
}

.phase-closed.session-bar,
.phase-weekend.session-bar {
  background: var(--el-color-info-light-9);
  border-color: var(--el-color-info-light-7);
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
.phase-auction .session-dot,
.phase-lunch .session-dot {
  background: var(--el-color-warning);
  box-shadow: 0 0 0 3px var(--el-color-warning-light-9);
}

.phase-closed .session-dot,
.phase-weekend .session-dot {
  background: var(--el-color-info);
  box-shadow: 0 0 0 3px var(--el-color-info-light-9);
}

@media (max-width: 700px) {
  .session-spacer {
    display: none;
  }

  .session-bar {
    flex-wrap: wrap;
  }
}
</style>
