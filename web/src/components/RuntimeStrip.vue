<template>
  <div class="runtime-strip" aria-label="网关运行态">
    <div class="rt-cell">
      <span class="rt-label">网关进程</span>
      <span class="rt-value">PID {{ stats?.pid ?? '—' }}</span>
    </div>
    <div class="rt-cell">
      <span class="rt-label">运行时长</span>
      <span class="rt-value">{{ uptimeText }}</span>
    </div>
    <div class="rt-cell">
      <span class="rt-label">在途请求</span>
      <span
        class="rt-value"
        :class="(stats?.active_requests ?? 0) > 0 ? 'rt-busy' : ''"
      >
        {{ stats?.active_requests ?? '—' }}
      </span>
    </div>
    <div class="rt-cell">
      <span class="rt-label">MCP 工具</span>
      <span class="rt-value">{{ stats?.tool_count ?? '—' }}</span>
    </div>
    <div class="rt-cell">
      <span class="rt-label">近 1 分钟调用</span>
      <span class="rt-value">
        {{ stats?.calls_last_1m ?? '—' }}
        <small class="rt-unit">次/分</small>
      </span>
    </div>
    <div class="rt-cell">
      <span class="rt-label">近 5 分钟调用</span>
      <span class="rt-value">{{ stats?.calls_last_5m ?? '—' }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RuntimeStats } from '@/api/runtime'
import { formatUptime } from '@/utils/number'

/** 网关运行态条带 (GET /api/runtime)。六格固定等分, 行宽随断点收敛。 */
const props = defineProps<{
  stats: RuntimeStats | null
}>()

const uptimeText = computed(() => {
  const seconds = props.stats?.uptime_seconds
  return seconds == null ? '—' : formatUptime(seconds)
})
</script>

<style scoped>
.runtime-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.rt-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.rt-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.rt-value {
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.rt-value.rt-busy {
  color: var(--el-color-primary);
}

.rt-unit {
  font-size: 11px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

@media (max-width: 1200px) {
  .runtime-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 700px) {
  .runtime-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
