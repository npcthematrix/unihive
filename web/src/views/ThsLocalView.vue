<template>
  <div class="ths-local-page">
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">THS Local 网关</h2>
          <div class="toolbar-actions">
            <el-tag :type="connected ? 'success' : 'danger'" size="large">
              {{ connected ? '已连接' : '未连接' }}
            </el-tag>
            <el-button :icon="Refresh" :loading="loading" @click="load">
              刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="loadError"
        type="error"
        :title="loadError"
        show-icon
        :closable="false"
        class="error-banner"
      />

      <div class="stats-row">
        <div class="mini-stat">
          <div class="mini-label">工具数量</div>
          <div class="mini-value">{{ toolCount }}</div>
        </div>
        <div class="mini-stat">
          <div class="mini-label">上游名称</div>
          <div class="mini-value">THS-LOCAL-GATEWAY</div>
        </div>
      </div>

      <div class="ths-table-scroll">
        <el-table :data="tools" v-loading="loading" style="width: 100%" :max-height="tableMaxHeight">
          <el-table-column prop="name" label="工具名称" min-width="200" />
          <el-table-column prop="title" label="标题" min-width="140" />
          <el-table-column label="参数" min-width="240">
            <template #default="{ row }">
              <div v-if="row.inputSchema?.properties" class="params-list">
                <div v-for="(info, paramName) in row.inputSchema.properties" :key="paramName" class="param-item">
                  <span class="param-name">{{ paramName }}</span>
                  <span class="param-type">{{ info.type }}</span>
                  <span v-if="info.description" class="param-desc">{{ info.description }}</span>
                </div>
              </div>
              <span v-else class="no-params">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="240">
            <template #default="{ row }">
              <span class="desc-text">{{ row.description }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { getThsLocalStatus, type ThsLocalTool } from '@/api/ths-local'

const connected = ref(false)
const toolCount = ref(0)
const tools = ref<ThsLocalTool[]>([])
const loading = ref(false)
const loadError = ref('')

const tableMaxHeight = computed(() =>
  typeof window !== 'undefined' && window.innerWidth < 768 ? 400 : 600,
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const status = await getThsLocalStatus()
    connected.value = status.connected
    toolCount.value = status.toolCount
    tools.value = status.tools
  } catch (e: unknown) {
    loadError.value = errorMessage(e, '请检查网关是否运行')
    connected.value = false
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.ths-local-page {
  padding: 16px;
}

.ths-table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.stats-row {
  display: flex;
  gap: 24px;
  margin-bottom: 16px;
}

.mini-stat {
  background: var(--el-fill-color-light);
  padding: 12px 20px;
  border-radius: 8px;
}

.mini-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.mini-value {
  font-size: 20px;
  font-weight: 600;
}

.error-banner {
  margin-bottom: 12px;
}

.desc-text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.params-list {
  font-size: 12px;
}

.param-item {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 4px;
}

.param-name {
  font-weight: 600;
  color: var(--el-color-primary);
}

.param-type {
  color: var(--el-color-success);
  font-size: 11px;
}

.param-desc {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.no-params {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}
</style>