<template>
  <div class="tools-panel">
    <!-- 搜索框 -->
    <div class="cfg-search-wrap" :class="{ 'has-value': search }">
      <input
        v-model="search"
        type="text"
        class="cfg-search"
        placeholder="按名称 / 描述 / 参数筛选…"
        aria-label="筛选工具规格"
      >
      <button
        v-if="search"
        type="button"
        class="cfg-search-clear"
        title="清空"
        @click="search = ''"
      >×</button>
    </div>

    <el-table
      :data="filteredTools"
      :empty-text="emptyText"
      class="cfg-table tools-table"
      row-key="name"
    >
      <el-table-column label="名称" min-width="180">
        <template #default="{ row }">
          <span class="tool-name">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="描述" min-width="240">
        <template #default="{ row }">
          <span
            class="tools-desc"
            :class="{ expandable: row.description.length > 80 }"
            @click="toggleExpand(row.name)"
          >{{ row.descDisplay }}</span>
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

    <div v-if="search.trim()" class="tools-count">
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

interface Props {
  config: GatewayConfig | null
  /** db 实体模式才出编辑入口 (yaml 逃生模式只读)。 */
  editable?: boolean
}

const props = withDefaults(defineProps<Props>(), { editable: false })
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
        // 不再依赖 expanded 触发的 key 变化 — 直接绑 expandedNames 响应即可
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
  gap: 8px;
}

.tools-desc {
  font-size: 0.82rem;
  line-height: 1.4;
}

.tools-desc.expandable {
  cursor: pointer;
}

.tools-desc.expandable:hover {
  color: var(--primary, var(--el-color-primary));
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

.tools-count {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 4px;
}
</style>
