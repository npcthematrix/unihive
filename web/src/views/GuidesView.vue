<template>
  <!-- --guide-anchor-offset: 吸顶条实际高度, 供正文 .guide-section 的
       scroll-margin-top 使用, 锚点跳转才不会把章节标题藏到吸顶条下面 -->
  <div class="view-page" :style="anchorStyle">
    <!-- 标题 + 子标签 + 章节导航共用**一条**吸顶条 —— 与「配置信息」「OMNI」
         「网关运行监控」同构: 滚动时标题不丢, 页面框架不跳。
         旧实现里 .view-header 与面板内的 .onboarding-nav 都 sticky top:0,
         滚动时导航条会盖住页面标题。 -->
    <div ref="stickyHeadRef" class="cfg-sticky-head">
      <header class="view-header">
        <h1 class="view-h1">接入指南</h1>
        <div class="view-actions">
          <el-button size="small" @click="jumpToInterfaces">MCP APIs</el-button>
          <el-button size="small" @click="jumpToMonitor">网关运行监控</el-button>
        </div>
      </header>
      <nav
        class="cfg-subtabs"
        role="tablist"
        aria-label="指南子标签"
        @keydown="onTablistKeydown"
      >
        <button
          v-for="tab in TABS"
          :id="`guide-tab-${tab.key}`"
          :key="tab.key"
          type="button"
          class="cfg-subtab"
          :class="{ active: activeTab === tab.key }"
          role="tab"
          :aria-selected="activeTab === tab.key"
          :aria-controls="`guide-panel-${tab.key}`"
          :tabindex="activeTab === tab.key ? 0 : -1"
          @click="selectTab(tab.key)"
        >{{ tab.title }}</button>
      </nav>

      <nav class="guide-chapters" aria-label="章节导航">
        <!-- 用 <button> 而非 <a href="#id">: 应用是 hash 路由, 中键/新标签
             打开 #sec-xxx 会被解析成伪路径再被兜底重定向, 且 a 的默认行为
             在 @click.prevent 失效时同样会污染 URL。按钮天然无此问题。 -->
        <button
          v-for="item in sections"
          :key="item.id"
          type="button"
          :aria-current="activeId === item.id ? 'location' : undefined"
          @click="scrollTo(item.id)"
        >{{ item.title }}</button>
      </nav>
    </div>

    <el-card shadow="never" class="panel-card">
      <div class="cfg-panel-stack">
        <section
          v-show="activeTab === 'human'"
          id="guide-panel-human"
          role="tabpanel"
          aria-labelledby="guide-tab-human"
        >
          <OnboardingPanel :tool-count="toolCount" />
        </section>
        <section
          v-show="activeTab === 'ai'"
          id="guide-panel-ai"
          role="tabpanel"
          aria-labelledby="guide-tab-ai"
        >
          <AiOnboardingPanel :tool-count="toolCount" />
        </section>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'
import { nextTabKey, parseHashTab } from './_shared/hashTab'
import {
  GUIDE_TAB_KEYS,
  GUIDE_TAB_TITLES,
  sectionsForTab,
  type GuideTabKey,
} from './guides/format'
import { useScrollSpy } from '@/composables/useScrollSpy'
import { useToolCount } from '@/composables/useToolCount'
import { scrollPageTop } from '@/utils/scrollTop'
import OnboardingPanel from './guides/OnboardingPanel.vue'
import AiOnboardingPanel from './guides/AiOnboardingPanel.vue'

const route = useRoute()
const router = useRouter()

const activeTab = ref<GuideTabKey>('human')
const sections = computed(() => sectionsForTab(activeTab.value))

const { activeId, scrollTo } = useScrollSpy(sections)

// 工具数只在这里拉一次, 按 prop 下发给两个面板。
// 原先两个面板各自 useToolCount(), v-show 让它们同时挂载 → 首屏 2 个
// /api/runtime 请求 + 常驻 2 个 60s 定时器。
const { toolCount } = useToolCount()

watch(
  () => route.query.tab,
  (q: LocationQueryValue | LocationQueryValue[]) => {
    activeTab.value = parseHashTab(q, GUIDE_TAB_KEYS, 'human')
  },
  { immediate: true },
)

const TABS = computed(() =>
  GUIDE_TAB_KEYS.map((key) => ({ key, title: GUIDE_TAB_TITLES[key] })),
)

// ===== 吸顶条高度 → 锚点偏移 =====
const stickyHeadRef = ref<HTMLElement | null>(null)
const stickyOffset = ref(0)
let resizeObserver: ResizeObserver | null = null

const anchorStyle = computed(() => `--guide-anchor-offset: ${stickyOffset.value}px`)

onMounted(() => {
  const el = stickyHeadRef.value
  if (!el) return
  stickyOffset.value = el.offsetHeight
  if (typeof ResizeObserver === 'undefined') return
  // 窄屏章节导航会换行变高, 必须跟着量, 不能写死常量
  resizeObserver = new ResizeObserver(() => {
    stickyOffset.value = el.offsetHeight
  })
  resizeObserver.observe(el)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})

function onTablistKeydown(e: KeyboardEvent): void {
  const target = nextTabKey(GUIDE_TAB_KEYS, activeTab.value, e.key)
  if (!target) return
  e.preventDefault()
  selectTab(target)
  void nextTick(() => {
    document.getElementById(`guide-tab-${target}`)?.focus()
  })
}

function selectTab(tab: GuideTabKey): void {
  if (activeTab.value === tab) return
  activeTab.value = tab
  void router.replace({ query: { ...route.query, tab } })
  scrollPageTop()
}

function jumpToInterfaces() {
  void router.push({ name: 'interfaces' })
}

function jumpToMonitor() {
  void router.push({ name: 'gateway-monitor', query: { tab: 'overview' } })
}
</script>

<style scoped>
/* 标题的 sticky 已由 .cfg-sticky-head (全局) 统一接管 —— 容器吸顶、容器内的
   .view-header 自动降级为 static, 不再需要页面级覆写。 */
</style>
