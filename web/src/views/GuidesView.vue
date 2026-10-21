<template>
  <div class="view-page">
    <!-- 吸顶条只留「标题 + 子标签」两行 —— 与「配置信息」「OMNI」「网关运行
         监控」完全同构。章节导航原先也挤在这条里, 三行常驻 125px, 在 900px
         视口上吃掉 14% 的可视高度, 而这页恰恰是唯一需要长时间阅读的页面。
         章节导航现在在正文右侧的目录栏 (见 .guide-toc)。
         2026-10-18: 页头不再靠 position:sticky —— 它在 .view-body 之外,
         天然固定, 只有正文滚。 -->
    <div class="cfg-sticky-head">
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
    </div>

    <!-- 唯一的滚动容器: 标题与子标签固定, 只有正文滚 -->
    <div class="view-body">
      <!-- 正文 + 右侧目录。目录在 DOM 里排在正文**之前**: 宽屏由 grid 放到右栏,
           窄屏自然落到正文上方 —— 视觉顺序与 DOM/朗读顺序一致, 不需要 order 反转。 -->
      <div class="guide-body">
        <aside class="guide-toc">
          <div class="guide-toc-inner">
            <p class="guide-toc-title">本页目录</p>
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
              >
                <span class="guide-toc-num">{{ item.num }}</span>
                <span class="guide-toc-text">{{ item.title }}</span>
              </button>
            </nav>
          </div>
        </aside>

        <el-card shadow="never" class="panel-card guide-card">
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
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
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

// ===== 锚点偏移 =====
// 页头在 .view-body 之外 (见模板注释), 章节跳转的落点天然在页头下方 —— 原先
// 「ResizeObserver 量吸顶条高度 → 写 --guide-anchor-offset → 供 scroll-margin-top
// 使用」这一整套已经不需要了, 只留 CSS 里固定的 12px 呼吸 (见 main.css)。

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
