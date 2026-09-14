<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-toolbar">
        <h2 class="card-title">接入指南</h2>
      </div>
    </template>

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
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { TAB_KEYS, TAB_TITLES, type GuidesTabKey } from './guides/format'
import { parseHashTab } from './_shared/hashTab'
import OnboardingPanel from './guides/OnboardingPanel.vue'
import AiOnboardingPanel from './guides/AiOnboardingPanel.vue'

const route = useRoute()
const router = useRouter()

const activeTab = ref<GuidesTabKey>('human')

activeTab.value = parseHashTab(route.query.tab, TAB_KEYS, 'human')

watch(
  () => route.query.tab,
  (q) => {
    activeTab.value = parseHashTab(q, TAB_KEYS, 'human')
  },
)

function selectTab(tab: GuidesTabKey) {
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