<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">配置信息</h2>
        <div class="toolbar-actions">
          <span
            class="last-refresh"
            :class="{ failed: loadError }"
          >{{ refreshLabel }}<template v-if="proxyRefreshSuffix"> · {{ proxyRefreshSuffix }}</template></span>
          <el-button :icon="DocumentCopy" @click="copyJson" :disabled="!config || copyInFlight" :loading="copyInFlight">复制 JSON</el-button>
          <el-button
            :icon="Refresh"
            :loading="manualLoading"
            @click="load"
          >立即刷新</el-button>
        </div>
      </div>
    </template>

    <div class="alert-stack">
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

      <el-alert
        v-if="proxyError"
        type="warning"
        :closable="false"
        show-icon
        class="load-error"
        :title="`代理设置加载失败：${proxyError}`"
      >
        <el-button size="small" @click="loadProxy">重试</el-button>
      </el-alert>
    </div>

    <!-- subtab 导航 -->
    <nav class="cfg-subtabs" role="tablist" aria-label="配置子标签">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        class="cfg-subtab"
        :class="{ active: activeTab === tab.key }"
        role="tab"
        :aria-selected="activeTab === tab.key"
        @click="selectTab(tab.key)"
      >
        {{ tab.title }}
        <span class="cfg-subtab-count">{{ tab.count }}</span>
      </button>
    </nav>

    <!-- 6 个 panel, 切换只显示 active -->
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
          :load-error="proxyError"
          @change="onProxyChange"
        />
      </section>

      <section
        v-show="activeTab === 'fuyaoCache'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-fuyao-cache"
      >
        <FuyaoCachePanel
          :fuyao-cache="config?.fuyao_cache"
          :config-version="config?.version || ''"
          :all-tools="config?.tools"
          :upstream-tool-mapping="config?.upstream_tool_mapping"
          @saved="onFuyaoCacheSaved"
        />
      </section>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh, DocumentCopy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getConfig } from '@/api/config'
import type { GatewayConfig, ProxySettings } from '@/api/config'
import { getProxySettings } from '@/api/config'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import { formatHMS } from '@/utils/time'
import { TAB_KEYS, TAB_TITLES, type TabKey } from './config/format'
import { parseHashTab } from './_shared/hashTab'
import UpstreamsPanel from './config/UpstreamsPanel.vue'
import MappingPanel from './config/MappingPanel.vue'
import ToolsPanel from './config/ToolsPanel.vue'
import CachePanel from './config/CachePanel.vue'
import ProxyPanel from './config/ProxyPanel.vue'
import FuyaoCachePanel from './config/FuyaoCachePanel.vue'

const route = useRoute()
const router = useRouter()

const config = ref<GatewayConfig | null>(null)
const proxy = ref<ProxySettings | null>(null)
const loading = ref(false)
const proxyLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
/** proxy 失败独立 ref, 不污染全局 loadError (后者只反映 getConfig 状态)。 */
const proxyError = ref('')
const lastRefresh = ref<number | null>(null)
/** proxy 单独的时间戳 — 顶栏让用户区分 "config / 代理" 各自的加载时点。 */
const proxyLastRefresh = ref<number | null>(null)
const copyInFlight = ref(false)

const activeTab = ref<TabKey>('upstreams')

// 用 watch + immediate 一次性处理「初始化 + 后续 hash 变化」, 避免重复 parse。
watch(
  () => route.query.tab,
  (q: LocationQueryValue | LocationQueryValue[]) => {
    activeTab.value = parseHashTab(q, TAB_KEYS, 'upstreams')
  },
  { immediate: true },
)

function selectTab(tab: TabKey): void {
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
/** FUYAO 工具级 override 总条数; 0 个显示 0 (扁平 tools dict, 2026-09-15 重构)。*/
const fuyaoOverrideCount = computed(() => {
  const tools = config.value?.fuyao_cache?.tools
  return tools ? Object.keys(tools).length : 0
})

const TABS = computed(() => [
  { key: 'upstreams' as const, title: TAB_TITLES.upstreams, count: upstreamsCount.value },
  { key: 'mapping' as const, title: TAB_TITLES.mapping, count: mappingCount.value },
  { key: 'tools' as const, title: TAB_TITLES.tools, count: toolsCount.value },
  { key: 'cache' as const, title: TAB_TITLES.cache, count: ttlCount.value },
  { key: 'proxy' as const, title: TAB_TITLES.proxy, count: coverageCount.value },
  { key: 'fuyaoCache' as const, title: TAB_TITLES.fuyaoCache, count: fuyaoOverrideCount.value },
])

async function loadProxy() {
  proxyLoading.value = true
  try {
    proxy.value = await getProxySettings()
    proxyError.value = ''
    proxyLastRefresh.value = Date.now()
  } catch (e) {
    // proxy tab 单独失败不影响 config 渲染; 用户切到该 tab 时再感知。
    // 写到 proxyError 独立 ref, 不污染全局 loadError (后者只反映 getConfig 状态)。
    proxyError.value = errorMessage(e, '加载代理设置失败')
  } finally {
    proxyLoading.value = false
  }
}

async function load(isManual = false) {
  if (isManual) manualLoading.value = true
  else loading.value = true
  loadError.value = ''
  try {
    const cfg = await getConfig()
    config.value = cfg
    lastRefresh.value = Date.now()
  } catch (e) {
    loadError.value = errorMessage(e, '加载配置失败')
  } finally {
    loading.value = false
    manualLoading.value = false
  }
  // proxy 单独拉, 失败不影响 config 渲染。
  // 当前在 proxy tab 时同步等待 (用户切到该 tab 应看到立即加载的数据),
  // 否则异步 (fire-and-forget) 不阻塞整体 load 延迟。
  if (activeTab.value === 'proxy') {
    await loadProxy()
  } else {
    void loadProxy()
  }
}

function onProxyChange(updated: ProxySettings) {
  proxy.value = updated
}

/** FUYAO 缓存保存成功 — 把新 fuyao_cache 与 version 回写到本地 config。 */
function onFuyaoCacheSaved(payload: { fuyao_cache: NonNullable<GatewayConfig['fuyao_cache']>; version: string }) {
  if (config.value) {
    config.value = {
      ...config.value,
      fuyao_cache: payload.fuyao_cache,
      version: payload.version,
    }
  }
}

const refreshLabel = computed(() => {
  if (loadError.value) return '加载失败'
  if (!lastRefresh.value) return '尚未加载'
  return `更新于 ${formatHMS(lastRefresh.value)}`
})

/** 代理独立的时间后缀; proxy 加载失败时不展示, 避免误导。 */
const proxyRefreshSuffix = computed(() => {
  if (loadError.value || proxyError.value) return ''
  if (!proxyLastRefresh.value) return ''
  return `代理 ${formatHMS(proxyLastRefresh.value)}`
})

async function copyJson() {
  // 防抖: 复制动作有 ElMessage 反馈, 连点会刷屏, 用 inFlight 串行化
  if (copyInFlight.value) return
  if (!config.value) {
    ElMessage.error('暂无配置数据')
    return
  }
  copyInFlight.value = true
  try {
    const text = JSON.stringify(config.value, null, 2)
    await copyText(text, '')
    ElMessage.success('配置 JSON 已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败, 请手动复制')
  } finally {
    copyInFlight.value = false
  }
}

onMounted(() => {
  void load()
})

/** 暴露给 Playwright / 调试的兜底接口 */
defineExpose({ load })
</script>

<style scoped>
.alert-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 4px;
}

.cfg-panel-stack {
  min-height: 240px;
}

/* 顶栏三个控件在窄屏(< 640px) 拥挤 — 允许换行 + 显式 gap */
:global(.toolbar-actions),
.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
</style>
