<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">配置信息</h2>
        <div class="toolbar-actions">
          <span
            class="last-refresh"
            :class="{ failed: loadError }"
          >{{ refreshLabel }}</span>
          <el-button :icon="Refresh" @click="copyJson" :disabled="!config">复制 JSON</el-button>
          <el-button
            :icon="Refresh"
            :loading="manualLoading"
            @click="load"
          >立即刷新</el-button>
        </div>
      </div>
    </template>

    <el-alert
      v-if="loadError"
      type="error"
      :closable="false"
      show-icon
      class="load-error"
      :title="`加载失败：${loadError}`"
    >
      <el-button size="small" @click="load">重试</el-button>
    </el-alert>

    <!-- subtab 导航 -->
    <nav class="cfg-subtabs" aria-label="配置子标签">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        class="cfg-subtab"
        :class="{ active: activeTab === tab.key }"
        :aria-current="activeTab === tab.key ? 'page' : undefined"
        @click="selectTab(tab.key)"
      >
        {{ tab.title }}
        <span class="cfg-subtab-count">{{ tab.count }}</span>
      </button>
    </nav>

    <!-- 5 个 panel, 切换只显示 active -->
    <div v-loading="loading && !config" class="cfg-panel-stack">
      <section
        v-show="activeTab === 'upstreams'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-upstreams"
      >
        <UpstreamsPanel :config="config" />
      </section>

      <section
        v-show="activeTab === 'mapping'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-mapping"
      >
        <MappingPanel :config="config" />
      </section>

      <section
        v-show="activeTab === 'tools'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-tools"
      >
        <ToolsPanel :config="config" />
      </section>

      <section
        v-show="activeTab === 'cache'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-cache"
      >
        <CachePanel :config="config" />
      </section>

      <section
        v-show="activeTab === 'proxy'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-proxy"
      >
        <ProxyPanel
          :proxy="proxy"
          :loading="proxyLoading"
          @change="onProxyChange"
        />
      </section>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getConfig } from '@/api/config'
import type { GatewayConfig, ProxySettings } from '@/api/config'
import { getProxySettings } from '@/api/config'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import { TAB_KEYS, TAB_TITLES, type TabKey } from './config/format'
import UpstreamsPanel from './config/UpstreamsPanel.vue'
import MappingPanel from './config/MappingPanel.vue'
import ToolsPanel from './config/ToolsPanel.vue'
import CachePanel from './config/CachePanel.vue'
import ProxyPanel from './config/ProxyPanel.vue'

const route = useRoute()
const router = useRouter()

const config = ref<GatewayConfig | null>(null)
const proxy = ref<ProxySettings | null>(null)
const loading = ref(false)
const proxyLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
const lastRefresh = ref<number | null>(null)

const activeTab = ref<TabKey>('upstreams')

/** 从 hash query ?tab= 解析初始 subtab */
function parseTabFromQuery(q: unknown): TabKey {
  const candidate = Array.isArray(q) ? q[0] : q
  const value = typeof candidate === 'string' ? candidate : ''
  return (TAB_KEYS as readonly string[]).includes(value) ? (value as TabKey) : 'upstreams'
}

activeTab.value = parseTabFromQuery(route.query.tab)

watch(
  () => route.query.tab,
  (q) => {
    activeTab.value = parseTabFromQuery(q)
  },
)

function selectTab(tab: TabKey) {
  if (activeTab.value === tab) return
  activeTab.value = tab
  // 只替换目标 slot, 保留其他 query 参数 (与阶段 3 深链经验一致)
  void router.replace({ query: { ...route.query, tab } })
}

const upstreamsCount = computed(() => Object.keys(config.value?.upstreams || {}).length)
const mappingCount = computed(() => Object.keys(config.value?.upstream_tool_mapping || {}).length)
const toolsCount = computed(() => (config.value?.tools || []).length)
const ttlCount = computed(() => Object.keys(config.value?.cache?.ttl || {}).length)
const coverageCount = computed(() => proxy.value?.coverage?.length ?? 0)

const TABS = computed(() => [
  { key: 'upstreams' as const, title: TAB_TITLES.upstreams, count: upstreamsCount.value },
  { key: 'mapping' as const, title: TAB_TITLES.mapping, count: mappingCount.value },
  { key: 'tools' as const, title: TAB_TITLES.tools, count: toolsCount.value },
  { key: 'cache' as const, title: TAB_TITLES.cache, count: ttlCount.value },
  { key: 'proxy' as const, title: TAB_TITLES.proxy, count: coverageCount.value },
])

async function loadProxy() {
  proxyLoading.value = true
  try {
    proxy.value = await getProxySettings()
  } catch (e) {
    // proxy tab 单独失败不影响 config 渲染; 用户切到该 tab 时再感知。
    // 静默记录, 不弹全局错误。
    loadError.value = errorMessage(e, '加载代理设置失败')
  } finally {
    proxyLoading.value = false
  }
}

async function load(isManual = false) {
  if (isManual) manualLoading.value = true
  else loading.value = true
  loadError.value = ''
  try {
    const [cfg] = await Promise.all([getConfig(), loadProxy()])
    config.value = cfg
    lastRefresh.value = Date.now()
  } catch (e) {
    loadError.value = errorMessage(e, '加载配置失败')
  } finally {
    loading.value = false
    manualLoading.value = false
  }
}

function onProxyChange(updated: ProxySettings) {
  proxy.value = updated
}

const refreshLabel = computed(() => {
  if (loadError.value) return '加载失败'
  if (!lastRefresh.value) return '尚未加载'
  return `更新于 ${formatTime(lastRefresh.value)}`
})

function formatTime(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function copyJson() {
  if (!config.value) {
    ElMessage.error('暂无配置数据')
    return
  }
  try {
    const text = JSON.stringify(config.value, null, 2)
    await copyText(text, '')
    ElMessage.success('配置 JSON 已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败, 请手动复制')
  }
}

onMounted(() => {
  void load()
})

/** 暴露给 Playwright / 调试的兜底接口 */
defineExpose({ load })
</script>

<style scoped>
.cfg-subtabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.cfg-panel-stack {
  min-height: 200px;
}
</style>
