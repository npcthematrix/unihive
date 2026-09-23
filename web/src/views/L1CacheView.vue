<template>
  <div class="l1-cache-page">
    <!-- 页头: 全页唯一的刷新/自动刷新控件 -->
    <header class="page-header">
      <h1 class="page-title">网关运行监控</h1>
      <div class="page-actions">
        <span
          class="last-refresh"
          :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
        >
          {{ refreshLabel }}
        </span>
        <el-button :icon="Refresh" :loading="manualLoading" @click="load(true)">
          立即刷新
        </el-button>
        <el-tooltip content="每 10 秒自动刷新全部卡片" placement="top">
          <span class="auto-toggle">
            <el-switch v-model="autoRefresh" />
            <span class="auto-label">自动刷新</span>
          </span>
        </el-tooltip>
      </div>
    </header>

    <SessionBar :session="session" />

    <RuntimeStrip :stats="runtime" />

    <RespCacheCard
      :cache="respCache"
      :foreign="statsFromForeignProcess"
      :initial-loading="initialLoading"
      :load-error="respLoadError"
      :rate-series="respRateSeries"
      :entries-series="liveEntriesSeries"
      :breakdown-rows="breakdownRows"
      :breakdown-loading="breakdownTableLoading"
      :breakdown-total-tools="breakdownTotalTools"
      :cleanup-loading="cleanupLoading"
      :vacuum-loading="vacuumLoading"
      :clear-loading="clearLoading"
      @cleanup="onCleanup"
      @vacuum="onVacuum"
      @clear="onClear"
    />

    <MootdxL1Card
      :metrics="metrics"
      :initial-loading="initialLoading"
      :load-error="l1LoadError"
      :rate-series="l1RateSeries"
      :size-series="l1SizeSeries"
      :reset-loading="resetL1Loading"
      @reset="onResetL1"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { STALE_THRESHOLD_SEC } from '@/constants/time'
import { marketSession } from '@/utils/market-session'
import { useGatewayRuntime } from '@/composables/useGatewayRuntime'
import SessionBar from '@/components/SessionBar.vue'
import RuntimeStrip from '@/components/RuntimeStrip.vue'
import RespCacheCard from '@/components/runtime/RespCacheCard.vue'
import MootdxL1Card from '@/components/runtime/MootdxL1Card.vue'

const {
  metrics,
  respCache,
  runtime,
  loadError,
  l1LoadError,
  respLoadError,
  initialLoading,
  manualLoading,
  statsFromForeignProcess,
  autoRefresh,
  nowSec,
  ageSec,
  refreshLabel,
  l1RateSeries,
  l1SizeSeries,
  respRateSeries,
  liveEntriesSeries,
  breakdownRows,
  breakdownTotalTools,
  breakdownTableLoading,
  resetL1Loading,
  cleanupLoading,
  vacuumLoading,
  clearLoading,
  load,
  onResetL1,
  onCleanup,
  onVacuum,
  onClear,
} = useGatewayRuntime()

// 1s 时钟驱动交易时段条带倒计时
const session = computed(() => marketSession(new Date(nowSec.value * 1000)))
</script>

<style scoped>
.l1-cache-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 页头 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.last-refresh {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.last-refresh.failed {
  color: var(--el-color-danger);
}

.last-refresh.stale {
  color: var(--el-color-warning);
}

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: default;
}
</style>
