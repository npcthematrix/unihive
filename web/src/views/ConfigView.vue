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
        <span v-if="tab.count !== ''" class="cfg-subtab-count">{{ tab.count }}</span>
      </button>
    </nav>

    <!-- 8 个 panel, 切换只显示 active -->
    <div v-loading="loading && !config" class="cfg-panel-stack">
      <section
        v-show="activeTab === 'upstreams'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-upstreams"
      >
        <UpstreamsPanel
          :config="displayConfig"
          :editable="isDbMode"
          @changed="onUpstreamEnabled"
          @connection-saved="onUpstreamConnectionSaved"
        />
      </section>

      <section
        v-show="activeTab === 'mapping'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-mapping"
      >
        <MappingPanel :config="displayConfig" />
      </section>

      <section
        v-show="activeTab === 'tools'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-tools"
      >
        <ToolsPanel
          :config="displayConfig"
          :editable="isDbMode"
          @changed="onToolEdited"
        />
      </section>

      <section
        v-show="activeTab === 'cache'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-cache"
      >
        <!-- entities 未加载完时默认按 db 措辞, 避免逃生模式 banner 闪烁 -->
        <CachePanel
          :config="displayConfig"
          :source="entities?.source === 'yaml' ? 'yaml' : 'db'"
        />
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
          :fuyao-cache="displayConfig?.fuyao_cache"
          :config-version="displayConfig?.version"
          :all-tools="displayConfig?.tools"
          :upstream-tool-mapping="displayConfig?.upstream_tool_mapping"
          @saved="onFuyaoCacheSaved"
        />
      </section>

      <section
        v-show="activeTab === 'database'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-database"
      >
        <DatabasePanel :entities="entities" />
      </section>

      <section
        v-show="activeTab === 'updateCenter'"
        class="cfg-panel"
        aria-labelledby="cfg-tab-update-center"
      >
        <UpdateCenterPanel :entities="entities" @applied="onFactoryUpgradeApplied" />
      </section>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh, DocumentCopy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getConfig, getConfigEntities } from '@/api/config'
import type {
  ConfigEntities,
  ConfigRevision,
  ConfigTool,
  GatewayConfig,
  ProxySettings,
} from '@/api/config'
import { getProxySettings } from '@/api/config'
import { getInterfaces } from '@/api/interfaces'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import { formatHMS } from '@/utils/time'
import { TAB_KEYS, TAB_TITLES, type TabKey } from './config/format'
import type { UpstreamEnabledChanged } from './config/upstreams'
import { userModifiedCount } from './config/entities'
import { parseHashTab } from './_shared/hashTab'
import UpstreamsPanel from './config/UpstreamsPanel.vue'
import MappingPanel from './config/MappingPanel.vue'
import ToolsPanel from './config/ToolsPanel.vue'
import CachePanel from './config/CachePanel.vue'
import ProxyPanel from './config/ProxyPanel.vue'
import FuyaoCachePanel from './config/FuyaoCachePanel.vue'
import DatabasePanel from './config/DatabasePanel.vue'
import UpdateCenterPanel from './config/UpdateCenterPanel.vue'

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

/** config.db v2 实体浏览摘要 (db 模式才有实体字段; yaml 逃生模式只回 source)。 */
const entities = ref<ConfigEntities | null>(null)
/**
 * /api/config 在 db 模式不携带 290 条 spec (避免快照臃肿); 工具规格面板与
 * FUYAO TTL 面板改吃 /api/interfaces 的全量 env 门控清单, 在此合成。
 */
const interfaceTools = ref<ConfigTool[]>([])

/**
 * 面板统一吃的 config: 快照自带 tools (yaml 模式) 时原样, 否则用 interfaces
 * 合成的清单补齐。不可变派生, 写回只动原始 config ref。
 */
const displayConfig = computed<GatewayConfig | null>(() => {
  const current = config.value
  if (!current) return null
  if (current.tools && current.tools.length) return current
  return { ...current, tools: interfaceTools.value }
})

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
const toolsCount = computed(() => (displayConfig.value?.tools || []).length)
const ttlCount = computed(() => Object.keys(config.value?.cache?.ttl || {}).length)
const coverageCount = computed(() => proxy.value?.coverage?.length ?? 0)
/** FUYAO 工具级 override 总条数; 0 个显示 0 (扁平 tools dict, 2026-09-15 重构)。*/
const fuyaoOverrideCount = computed(() => {
  const tools = config.value?.fuyao_cache?.tools
  return tools ? Object.keys(tools).length : 0
})
/** 配置库 tab 角标: 用户改动实体行数 (yaml 逃生模式 0)。 */
const userModifiedEntitiesCount = computed(() => userModifiedCount(entities.value))

/** db 实体模式才允许热编辑 (yaml 逃生模式隐藏编辑入口)。 */
const isDbMode = computed(() => entities.value?.source === 'db')

const TABS = computed(() => [
  { key: 'upstreams' as const, title: TAB_TITLES.upstreams, count: upstreamsCount.value },
  { key: 'mapping' as const, title: TAB_TITLES.mapping, count: mappingCount.value },
  { key: 'tools' as const, title: TAB_TITLES.tools, count: toolsCount.value },
  { key: 'cache' as const, title: TAB_TITLES.cache, count: ttlCount.value },
  { key: 'proxy' as const, title: TAB_TITLES.proxy, count: coverageCount.value },
  { key: 'fuyaoCache' as const, title: TAB_TITLES.fuyaoCache, count: fuyaoOverrideCount.value },
  { key: 'database' as const, title: TAB_TITLES.database, count: userModifiedEntitiesCount.value },
  // 出厂更新无静态角标计数 (模板 hash 信号在面板内拉取展示)
  { key: 'updateCenter' as const, title: TAB_TITLES.updateCenter, count: '' },
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

/** 拉 config.db v2 实体浏览摘要 (yaml 逃生模式只回 {source:'yaml'})。 */
async function loadEntities() {
  try {
    entities.value = await getConfigEntities()
  } catch (e) {
    // 瞬时失败不阻塞配置渲染, 面板下次整体刷新时重试
    ElMessage.error(errorMessage(e, '加载配置库实体失败'))
  }
}

/** 拉全量工具清单 (/api/interfaces, env 门控后), 补 db 模式快照缺失的 tools。 */
async function loadInterfaceTools() {
  try {
    const data = await getInterfaces()
    // interfaces 的 ToolParamInfo 无 ConfigTool 的字符串索引签名, 结构上兼容,
    // 这里显式收窄 (ToolsPanel/FuyaoCachePanel 只读 name/description/params)。
    interfaceTools.value = data.tools.map((t) => ({
      name: t.name,
      description: t.description,
      dangerous: t.dangerous,
      cache_ttl_key: t.cache_ttl_key,
      params: t.params,
    })) as ConfigTool[]
  } catch (e) {
    ElMessage.error(errorMessage(e, '加载工具清单失败'))
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
  // 配置库实体摘要 + 全量工具清单随 config 一起刷新 (独立失败, 不阻塞渲染)。
  void loadEntities()
  void loadInterfaceTools()
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

/** 上游热开关成功 — 不可变 patch 目标上游 enabled 与乐观锁 revision。 */
function onUpstreamEnabled(payload: UpstreamEnabledChanged) {
  const current = config.value
  if (!current || !current.upstreams) return
  const prev = current.upstreams[payload.name] || {}
  config.value = {
    ...current,
    upstreams: {
      ...current.upstreams,
      [payload.name]: { ...prev, enabled: payload.enabled },
    },
    version: payload.version,
  }
  // enabled 已写实体行, 刷新「配置库」tab 的 origin/updated_at 与 revision
  void loadEntities()
  void loadInterfaceTools()
}

/**
 * 工具热编辑成功: 回写乐观锁 revision, 重拉 /api/interfaces (改名/描述/
 * 发布态都会反映在工具清单) 与实体摘要 (origin/updated_at)。
 */
function onToolEdited(payload: {
  name: string
  revision: ConfigRevision
  published: boolean
  previouslyPublished: boolean
  changed: boolean
}) {
  if (config.value) {
    config.value = { ...config.value, version: payload.revision }
  }
  void loadEntities()
  void loadInterfaceTools()
}

/**
 * 上游连接块热编辑成功: 连接参数/类型可能已变, 重拉整份配置快照
 * (脱敏展示值也要更新), 并刷新实体摘要。
 */
function onUpstreamConnectionSaved(payload: {
  name: string
  revision: number
  enabled: boolean
  connected: boolean
  changed: boolean
}) {
  if (config.value) {
    config.value = { ...config.value, version: payload.revision }
  }
  void load(false)
}

/** FUYAO 缓存保存成功 — 把新 fuyao_cache 与 revision 回写到本地 config。 */
function onFuyaoCacheSaved(payload: {
  fuyao_cache: NonNullable<GatewayConfig['fuyao_cache']>
  version: ConfigRevision
}) {
  if (config.value) {
    config.value = {
      ...config.value,
      fuyao_cache: payload.fuyao_cache,
      version: payload.version,
    }
  }
  void loadEntities()
}

/**
 * 出厂更新热应用成功: 实体/快照/工具发布集都可能批量变化, 整份重拉
 * (面板自身已复查 dry-run; 这里刷新配置快照、实体摘要与工具清单)。
 */
function onFactoryUpgradeApplied(payload: { revision: number }) {
  if (config.value) {
    config.value = { ...config.value, version: payload.revision }
  }
  void load(false)
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
