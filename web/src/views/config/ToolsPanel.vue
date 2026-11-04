<template>
  <div class="tools-panel">
    <!-- 搜索框 -->
    <CfgSearchInput
      v-model="search"
      placeholder="按名称 / 描述 / 参数筛选…"
      aria-label="筛选工具规格"
    />

    <!-- yaml 逃生模式: 编辑列整列消失, 不给这句话用户只能看到「入口凭空没了」。 -->
    <div v-if="writeBlocked" class="cfg-banner cfg-banner-warn">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        网关以 <code>UNIHIVE_CONFIG_SOURCE=yaml</code> 逃生模式启动, 工具规格只读;
        热编辑入口需以默认模式重启网关恢复。
      </span>
    </div>

    <!-- 口径说明: 本页数据源是 /api/interfaces (装配后 + env 过滤后的可发布集),
         不是 config.db 的工具实体表。两者计数天然不同, 必须说清楚,
         否则用户会以为「配置库」少算了工具。
         逃生模式同样需要这句话 (口径差异与能不能编辑无关)。 -->
    <div class="tools-scope-note">
      此处为<strong>装配后可发布</strong>的工具集 (已按上游启用态与 env 门控过滤),
      与「配置库」的实体总数口径不同; 需补环境变量才会出现的工具不会列在此处。
    </div>

    <el-table
      :data="filteredTools"
      :empty-text="emptyText"
      class="tools-table"
      row-key="name"
    >
      <el-table-column label="名称" min-width="180">
        <template #default="{ row }">
          <span class="tool-name">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="描述" min-width="240">
        <template #default="{ row }">
          <!-- 只有真正被截断的描述才渲染成按钮: 短描述不再是可点却无反应的
               span, 长描述则带 aria-expanded 且原生支持 enter/space + 焦点环。 -->
          <button
            v-if="row.description.length > 80"
            type="button"
            class="tools-desc expandable"
            :aria-expanded="isExpanded(row.name)"
            :aria-label="`${isExpanded(row.name) ? '收起' : '展开'} ${row.name} 的完整描述`"
            @click="toggleExpand(row.name)"
          >{{ row.descDisplay }}</button>
          <span v-else class="tools-desc">{{ row.descDisplay }}</span>
        </template>
      </el-table-column>
      <el-table-column label="缓存键" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.cache_ttl_key" size="small" effect="plain" round>
            {{ row.cache_ttl_key }}
          </el-tag>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="参数" min-width="220">
        <template #default="{ row }">
          <span v-if="row.params.length" class="tools-params">
            <template v-for="(p, idx) in row.params" :key="idx">
              <span
                class="tools-param"
                :class="{ required: p.required }"
              >{{ p.required ? '*' : '·' }}{{ p.text }}</span>
              <span v-if="idx < row.params.length - 1" class="tools-param-sep"> </span>
            </template>
          </span>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="危险" width="70" align="center">
        <template #default="{ row }">
          <el-tooltip
            v-if="row.dangerous"
            content="需要调用方显式启用"
            placement="top"
          >
            <span class="cfg-switch off tools-danger">危险</span>
          </el-tooltip>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column v-if="editable" label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button
            size="small"
            text
            type="primary"
            :aria-label="`编辑工具 ${row.name}`"
            @click="openEdit(row.name)"
          >编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="search.trim()" class="cfg-filter-count">
      显示 {{ filteredTools.length }} / {{ totalTools }}
    </div>

    <ToolEditDialog
      v-model="dialogOpen"
      :tool-name="editingName"
      @saved="onSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ConfigRevision, ConfigTool, GatewayConfig } from '@/api/config'
import { paramCells, paramsSearchKey } from './format'
import ToolEditDialog from './ToolEditDialog.vue'
import CfgSearchInput from './CfgSearchInput.vue'

interface Props {
  config: GatewayConfig | null
  /** db 实体模式才出编辑入口 (yaml 逃生模式只读)。 */
  editable?: boolean
  /** 确定处于 yaml 逃生模式 — 给只读横幅 (与加载中区分, 避免横幅闪烁)。 */
  writeBlocked?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  editable: false,
  writeBlocked: false,
})
const emit = defineEmits<{
  changed: [payload: {
    name: string
    revision: ConfigRevision
    published: boolean
    previouslyPublished: boolean
    changed: boolean
  }]
}>()

/** 编辑弹窗状态 (弹窗内部自取 GET 详情, 列表只负责打开/冒泡保存事件)。 */
const dialogOpen = ref(false)
const editingName = ref<string | null>(null)

function openEdit(name: string): void {
  editingName.value = name
  dialogOpen.value = true
}

function onSaved(payload: {
  name: string
  revision: number
  published: boolean
  previouslyPublished: boolean
  changed: boolean
}): void {
  emit('changed', payload)
  editingName.value = null
}

const search = ref('')

/** 行已展开的 tool name 集合 (展开后显示完整描述) */
const expandedNames = ref(new Set<string>())

interface ToolRow {
  name: string
  description: string
  descDisplay: string
  cache_ttl_key?: string | null
  dangerous?: boolean
  params: { text: string; required: boolean }[]
  searchKey: string
}

function isExpanded(name: string): boolean {
  return expandedNames.value.has(name)
}

function toggleExpand(name: string) {
  const next = new Set(expandedNames.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  expandedNames.value = next
}

const tools = computed<ToolRow[]>(() => {
  const list = props.config?.tools || []
  return [...list]
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')))
    .map<ToolRow>((t: ConfigTool) => {
      const desc = t.description || '(无描述)'
      const expanded = expandedNames.value.has(t.name)
      const showFull = desc.length > 80 && expanded
      return {
        name: t.name,
        description: desc,
        descDisplay: showFull ? desc : desc.length > 80 ? desc.slice(0, 80) + '…' : desc,
        cache_ttl_key: t.cache_ttl_key,
        dangerous: t.dangerous,
        params: paramCells(t.params),
        searchKey: (
          t.name + ' ' + desc + ' ' + paramsSearchKey(t.params) + ' ' + (t.cache_ttl_key || '')
        ).toLowerCase(),
      }
    })
})

const totalTools = computed(() => tools.value.length)

const filteredTools = computed<ToolRow[]>(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return tools.value
  return tools.value.filter((r) => r.searchKey.includes(q))
})

const emptyText = computed(() => {
  if (!totalTools.value) return '暂无工具规格'
  return '无匹配项'
})
</script>

<style scoped>
.tools-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tools-scope-note {
  font-size: 0.78rem;
  line-height: 1.6;
  color: var(--cfg-text-secondary);
  padding: 8px 12px;
  background: var(--cfg-bg-soft);
  border: 1px solid var(--cfg-border);
  border-radius: 6px;
}

.tools-desc {
  font-size: 0.82rem;
  line-height: 1.4;
  color: inherit;
}

/* 展开控件用原生 button 后, 抹掉按钮外观让它与同列的 span 排版一致。 */
button.tools-desc {
  display: inline;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

button.tools-desc:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  border-radius: 2px;
}

.tools-desc.expandable:hover {
  color: var(--el-color-primary);
}

.tools-params {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.75rem;
}

/* Element Plus danger-dark-2: 比 base danger 更适合小字正文, 暗色主题自动适配 */
.tools-param.required {
  color: var(--el-color-danger-dark-2, #c45656);
}

.tools-param:not(.required) {
  color: var(--text-secondary);
}
</style>
