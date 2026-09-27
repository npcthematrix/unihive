<template>
  <div class="view-page">
    <header class="view-header">
      <h1 class="view-h1">配置信息</h1>
      <div class="view-actions">
        <span
          class="last-refresh"
          :class="{ failed: loadError }"
        >{{ refreshLabel }}<template v-if="proxyRefreshSuffix"> · {{ proxyRefreshSuffix }}</template></span>
        <el-button size="small" :icon="DocumentCopy" @click="copyJson" :disabled="!config || copyInFlight" :loading="copyInFlight">复制 JSON</el-button>
        <el-button
          size="small"
          :icon="Refresh"
          :loading="manualLoading"
          @click="load(true)"
        >立即刷新</el-button>
      </div>
    </header>

    <el-card shadow="never" class="panel-card">
    <div class="alert-stack">
      <el-alert
        v-if="loadError"
        type="error"
        :closable="false"
        show-icon
        class="load-error"
        :title="`加载失败：${loadError}`"
      >
        <el-button size="small" @click="load(false)">重试</el-button>
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

    <!-- 7 个 panel, 切换只显示 active -->
    <div v-loading="loading && !config" class="cfg-panel-stack">
      <section
        v-show="activeTab === 'upstreams'"
        class="cfg-panel"
      >
        <UpstreamsPanel
          :config="displayConfig"
          :editable="isDbMode"
          @changed="onUpstreamEnabled"
          @connection-saved="onUpstreamConnectionSaved"
          @kill-switch-changed="onKillSwitchChanged"
          @pool-config-saved="onPoolConfigSaved"
        />
      </section>

      <section
        v-show="activeTab === 'mapping'"
        class="cfg-panel"
      >
        <MappingPanel :config="displayConfig" />
      </section>

      <section
        v-show="activeTab === 'tools'"
        class="cfg-panel"
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
      >
        <ProxyPanel :proxy="proxy" @change="onProxyChange" />
      </section>

      <section
        v-show="activeTab === 'database'"
        class="cfg-panel"
      >
        <DatabasePanel :entities="entities" />
      </section>

      <section
        v-show="activeTab === 'updateCenter'"
        class="cfg-panel"
      >
        <UpdateCenterPanel :entities="entities" @applied="onFactoryUpgradeApplied" />
      </section>
    </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { Refresh, DocumentCopy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getConfig, getConfigEntities, getProxySettings } from '@/api/config'
import type { ConfigRevision, GatewayConfig, ProxySettings } from '@/api/config'
import { getInterfaces } from '@/api/interfaces'
import { copyText } from '@/utils/clipboard'
import { formatHMS, formatLastRefreshLabel } from '@/utils/time'
import { TAB_KEYS, TAB_TITLES, type TabKey } from './config/format'
import type { UpstreamEnabledChanged } from './config/upstreams'
import { userModifiedCount } from './config/entities'
import { parseHashTab } from './_shared/hashTab'
import { createConfigPageLoader } from './config/pageLoader'
import UpstreamsPanel from './config/UpstreamsPanel.vue'
import MappingPanel from './config/MappingPanel.vue'
import ToolsPanel from './config/ToolsPanel.vue'
import CachePanel from './config/CachePanel.vue'
import ProxyPanel from './config/ProxyPanel.vue'
import DatabasePanel from './config/DatabasePanel.vue'
import UpdateCenterPanel from './config/UpdateCenterPanel.vue'

const route = useRoute()
const router = useRouter()

const copyInFlight = ref(false)

/**
 * 四路数据 (config / 实体摘要 / 全量工具清单 / proxy) 的并发加载编排,
 * 各通道独立错误面。逻辑抽到 config/pageLoader 纯 composable 单测。
 * isProxyActive 是惰性 getter, 引用下方稍后声明的 activeTab (闭包调用时才读)。
 */
const {
  config,
  entities,
  interfaceTools,
  proxy,
  loading,
  manualLoading,
  loadError,
  proxyError,
  lastRefresh,
  proxyLastRefresh,
  load: loaderLoad,
  loadProxy,
  loadEntities,
  loadInterfaceTools,
} = createConfigPageLoader(
  {
    getConfig,
    getEntities: getConfigEntities,
    getInterfaces,
    getProxy: getProxySettings,
  },
  (message) => ElMessage.error(message),
  () => activeTab.value === 'proxy',
)

/** 模板里的刷新/重试直接绑这个 (立即刷新传 true 走按钮 loading 态)。 */
const load = loaderLoad

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
  { key: 'database' as const, title: TAB_TITLES.database, count: userModifiedEntitiesCount.value },
  // 出厂更新无静态角标计数 (模板 hash 信号在面板内拉取展示)
  { key: 'updateCenter' as const, title: TAB_TITLES.updateCenter, count: '' },
])

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

/** dangerous kill switch 热切换成功 — patch 顶层 disable_dangerous + revision。 */
function onKillSwitchChanged(payload: {
  disabled: boolean
  version: ConfigRevision
}) {
  const current = config.value
  if (!current) return
  config.value = {
    ...current,
    disable_dangerous: payload.disabled,
    version: payload.version,
  }
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

/**
 * 全局池参数 PUT 成功: revision bump, 跑一次实体摘要刷新让变更被记录。
 * running 值未变 (Semaphore/ThreadPool 不可运行时扩缩, 等用户重启);
 * 不需要重拉 /api/config 因为 schema 字段没变。
 */
function onPoolConfigSaved(payload: {
  revision: ConfigRevision
  pending_restart: Record<string, boolean>
}) {
  if (config.value) {
    config.value = { ...config.value, version: payload.revision }
  }
  void loadEntities()
  void loadInterfaceTools()
}

const refreshLabel = computed(() =>
  formatLastRefreshLabel(loadError.value, lastRefresh.value),
)

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
</style>
