<template>
  <div class="view-page">
    <!-- 页头 + 告警 + subtab 共用一个 sticky 容器: 长页滚动时标题与
         标签一起吸顶, 不必回到顶部才能切 tab。原先只有 .view-header 吸顶。 -->
    <div class="cfg-sticky-head">
    <header class="view-header">
      <h1 class="view-h1">配置信息</h1>
      <div class="view-actions">
        <span
          class="last-refresh"
          :class="{ failed: loadError }"
        >{{ refreshLabel }}<template v-if="proxyRefreshSuffix"> · {{ proxyRefreshSuffix }}</template></span>
        <el-button size="small" :icon="DocumentCopy" @click="copyJson" :disabled="!copyReady || copyInFlight" :loading="copyInFlight">复制 JSON</el-button>
        <el-button
          size="small"
          :icon="Refresh"
          :loading="manualLoading"
          @click="load(true)"
        >立即刷新</el-button>
      </div>
    </header>

    <div v-if="loadError || proxyError || entitiesFailed" class="alert-stack">
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

      <!-- 实体摘要失败会让「可编辑」判定一并失效 (isDbMode 无法确认),
           此处必须显式说明, 否则上游/工具/池参数三处热编辑入口会静默消失。 -->
      <el-alert
        v-if="entitiesFailed"
        type="warning"
        :closable="false"
        show-icon
        class="load-error"
        title="配置库实体加载失败, 热编辑入口已暂时隐藏"
      >
        <div class="entities-failed-hint">
          无法确认配置事实源 ({{ entitiesError }})。上游连接块 / 工具规格 /
          全局池参数的编辑入口依赖该接口, 请重试或点击右上角「立即刷新」。
        </div>
        <el-button size="small" @click="loadEntities">重试</el-button>
      </el-alert>
    </div>

    <!-- subtab 导航 — WAI-ARIA Tabs 模式: roving tabindex + 方向键 -->
    <nav
      class="cfg-subtabs"
      role="tablist"
      aria-label="配置子标签"
      @keydown="onTablistKeydown"
    >
      <button
        v-for="tab in TABS"
        :id="`cfg-tab-${tab.key}`"
        :key="tab.key"
        type="button"
        class="cfg-subtab"
        :class="{ active: activeTab === tab.key }"
        role="tab"
        :aria-selected="activeTab === tab.key"
        :aria-controls="`cfg-panel-${tab.key}`"
        :tabindex="activeTab === tab.key ? 0 : -1"
        @click="selectTab(tab.key)"
      >
        {{ tab.title }}
        <span v-if="tab.count !== ''" class="cfg-subtab-count">{{ tab.count }}</span>
      </button>
    </nav>
    </div>

    <!-- 7 个 panel, 切换只显示 active -->
    <div v-loading="loading && !config" class="cfg-panel-stack">
      <section
        v-show="activeTab === 'upstreams'"
        id="cfg-panel-upstreams"
        role="tabpanel"
        aria-labelledby="cfg-tab-upstreams"
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
        id="cfg-panel-mapping"
        role="tabpanel"
        aria-labelledby="cfg-tab-mapping"
      >
        <MappingPanel :config="displayConfig" />
      </section>

      <section
        v-show="activeTab === 'tools'"
        id="cfg-panel-tools"
        role="tabpanel"
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
        id="cfg-panel-cache"
        role="tabpanel"
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
        id="cfg-panel-proxy"
        role="tabpanel"
        aria-labelledby="cfg-tab-proxy"
      >
        <ProxyPanel :proxy="proxy" @change="onProxyChange" />
      </section>

      <section
        v-show="activeTab === 'database'"
        id="cfg-panel-database"
        role="tabpanel"
        aria-labelledby="cfg-tab-database"
      >
        <DatabasePanel :entities="entities" />
      </section>

      <section
        v-show="activeTab === 'updateCenter'"
        id="cfg-panel-updateCenter"
        role="tabpanel"
        aria-labelledby="cfg-tab-updateCenter"
      >
        <UpdateCenterPanel :entities="entities" :active="activeTab === 'updateCenter'" @applied="onFactoryUpgradeApplied" />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
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
import { parseHashTab, nextTabKey } from './_shared/hashTab'
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
  entitiesError,
  toolsSettled,
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

/**
 * tablist 方向键导航 (WAI-ARIA Tabs 的 roving tabindex 模式):
 * Left/Right 循环切换并激活, Home/End 跳首尾。
 * 只有当前 tab 是 tabindex=0, 其余为 -1, 所以焦点必须跟着 activeTab 走。
 */
function onTablistKeydown(e: KeyboardEvent): void {
  const target = nextTabKey(TAB_KEYS, activeTab.value, e.key)
  if (!target) return
  e.preventDefault()
  selectTab(target)
  void nextTick(() => {
    document.getElementById(`cfg-tab-${target}`)?.focus()
  })
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

/**
 * 实体摘要请求失败 (且没有任何可用数据) —— 与「yaml 逃生模式」是两回事:
 * 前者是异常, 需要显式错误面 + 重试; 后者是预期状态, 只需说明即可。
 * 两者都会让 isDbMode 变成 false, 所以必须分开呈现, 否则上游 / 工具规格 /
 * 全局池参数三处热编辑入口会在一次瞬时失败后静默消失, 用户只看到一个 toast。
 */
const entitiesFailed = computed(() => Boolean(entitiesError.value) && !entities.value)

/**
 * 「复制 JSON」就绪判定。displayConfig 在快照缺 tools 时用 /api/interfaces
 * 合成清单补齐, 所以必须等该通道结束过一轮 (成功或失败), 否则首屏 config
 * 先落地时立即复制会得到一份缺 tools 的 JSON, 与屏幕所见不一致。
 */
const copyReady = computed(() => {
  if (!config.value) return false
  if (config.value.tools && config.value.tools.length) return true
  return toolsSettled.value
})

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
  // M1: 复制与屏幕一致的配置 — displayConfig 在原始快照缺 tools 时
  // 已用 /api/interfaces 合成清单补齐, 与「工具」tab 展示同源。
  const cfg = displayConfig.value
  if (!cfg) {
    ElMessage.error('暂无配置数据')
    return
  }
  copyInFlight.value = true
  try {
    const text = JSON.stringify(cfg, null, 2)
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
/* 页头 + 告警 + subtab 的统一吸顶容器 .cfg-sticky-head, 以及 .cfg-panel-stack
   已于 2026-10-17 上提到 main.css (OmniView 引用了同名类但 scoped 不外溢,
   那边实际没生效)。此处只保留本页特有的样式。 */

/* 只在有告警时渲染 (模板已加 v-if), 不再靠 margin-bottom 制造幽灵间距 */
.alert-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.entities-failed-hint {
  margin: 4px 0 8px;
  font-size: 0.82rem;
  line-height: 1.6;
}
</style>
