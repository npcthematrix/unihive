<template>
  <div class="view-page">
    <header class="view-header">
      <h1 class="view-h1">接入指南</h1>
    </header>

    <el-card shadow="never" class="panel-card">

    <!-- 2 个 subtab -->
    <nav class="cfg-subtabs" role="tablist" aria-label="指南子标签">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        class="cfg-subtab"
        :class="{ active: activeTab === tab.key }"
        role="tab"
        :aria-selected="activeTab === tab.key"
        @click="selectTab(tab.key)"
      >{{ tab.title }}</button>
    </nav>

    <div class="guides-panel-stack">
      <section
        v-show="activeTab === 'human'"
        class="cfg-panel"
        aria-label="人工接入指南"
      >
        <OnboardingPanel />
      </section>
      <section
        v-show="activeTab === 'ai'"
        class="cfg-panel"
        aria-label="AI 接入指南"
      >
        <AiOnboardingPanel />
      </section>
    </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { parseHashTab } from './_shared/hashTab'
import type { LocationQueryValue } from 'vue-router'
import OnboardingPanel from './guides/OnboardingPanel.vue'
import AiOnboardingPanel from './guides/AiOnboardingPanel.vue'

const route = useRoute()
const router = useRouter()

/** 2 个 subtab 的 key 常量与类型 */
const TAB_KEYS = ['human', 'ai'] as const
type GuidesTabKey = (typeof TAB_KEYS)[number]

/** subtab 展示标题 */
const TAB_TITLES: Record<GuidesTabKey, string> = {
  human: '人工接入',
  ai: 'AI 接入',
}

const activeTab = ref<GuidesTabKey>('human')

watch(
  () => route.query.tab,
  (q: LocationQueryValue | LocationQueryValue[]) => {
    activeTab.value = parseHashTab(q, TAB_KEYS, 'human')
  },
  { immediate: true },
)

function selectTab(tab: GuidesTabKey): void {
  if (activeTab.value === tab) return
  activeTab.value = tab
  void router.replace({ query: { ...route.query, tab } })
}

const TABS = computed(() =>
  (TAB_KEYS as readonly GuidesTabKey[]).map((key) => ({
    key,
    title: TAB_TITLES[key],
  })),
)
</script>

<style scoped>
.guides-panel-stack {
  min-height: 200px;
}
</style>