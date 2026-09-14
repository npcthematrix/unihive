<template>
  <div class="query-panel">
    <!-- 搜索栏 -->
    <div class="query-bar">
      <el-input
        v-model="keyword"
        class="query-keyword"
        placeholder="输入板块名称或代码搜索..."
        clearable
        @keydown.enter="onSearch"
        @clear="onSearch"
      />
      <el-select v-model="source" placeholder="全部数据源" clearable style="width: 160px" @change="onSearch">
        <el-option label="全部数据源" value="" />
        <el-option label="同花顺" value="fuyao" />
        <el-option label="TDX-QUANT" value="tqquant" />
      </el-select>
      <el-select v-model="boardType" placeholder="全部类型" clearable style="width: 160px" @change="onSearch">
        <el-option label="全部类型" value="" />
        <el-option label="行业板块" value="industry" />
        <el-option label="概念板块" value="concept" />
        <el-option label="风格板块" value="style" />
        <el-option label="地域板块" value="region" />
      </el-select>
      <el-button type="primary" :loading="loading" @click="onSearch">查询</el-button>
    </div>

    <!-- 结果区 -->
    <div v-loading="loading" class="results-wrap">
      <div v-if="loadError" class="feedback feedback-error">
        <span class="status-icon status-icon-error" aria-hidden="true">✕</span>
        查询异常: {{ loadError }}
        <el-button size="small" text type="primary" @click="onSearch">重试</el-button>
      </div>

      <div
        v-else-if="!hasSearched"
        class="feedback feedback-info"
      >
        <span class="status-icon" aria-hidden="true">💡</span>
        请输入关键词搜索板块, 或调整筛选条件后点击查询
      </div>

      <div
        v-else-if="!sectors.length"
        class="feedback feedback-empty"
      >
        <span class="status-icon status-icon-empty" aria-hidden="true">○</span>
        未找到匹配的板块
        <span class="feedback-meta">[{{ queryDesc }}]</span>
      </div>

      <template v-else>
        <div class="results-header">
          找到 <strong>{{ totalCount }}</strong> 个板块
          <span class="feedback-meta">[{{ queryDesc }}]</span>
          <span class="results-hint">点击板块行查看成分股</span>
        </div>

        <el-table
          :data="sectors"
          class="sector-table"
          @row-click="openSectorModal"
          stripe
        >
          <el-table-column label="板块名称" min-width="180">
            <template #default="{ row }">
              <span class="sector-name" :title="row.name">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="代码" width="120">
            <template #default="{ row }">
              <code>{{ row.code }}</code>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="110">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" round>
                {{ displayBoardType(row.board_type) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="成分股数量" width="110" align="right" header-align="right">
            <template #default="{ row }">{{ row.stock_count || 0 }}</template>
          </el-table-column>
          <el-table-column label="数据源" width="110">
            <template #default="{ row }">{{ displaySource(row.source) }}</template>
          </el-table-column>
          <el-table-column label="更新时间" width="120">
            <template #default="{ row }">{{ formatDate(row.update_time) }}</template>
          </el-table-column>
        </el-table>

        <div v-if="totalPages > 1" class="pagination">
          <el-pagination
            :current-page="page"
            :page-size="pageSize"
            :total="totalCount"
            :pager-count="5"
            layout="prev, pager, next, jumper, ->, total"
            @current-change="onPageChange"
          />
          <span class="page-info">第 {{ page }} / {{ totalPages }} 页 (共 {{ totalCount }} 条)</span>
        </div>
      </template>
    </div>

    <!-- 板块成分股弹窗 -->
    <el-dialog
      v-model="modalOpen"
      :title="modalTitle"
      width="600px"
      :close-on-click-modal="false"
      destroy-on-close
      @open="onModalOpen"
    >
      <div class="modal-meta">{{ modalMeta }}</div>
      <div v-loading="modalLoading" class="modal-body">
        <div v-if="modalError" class="feedback feedback-error">
          <span class="status-icon status-icon-error" aria-hidden="true">✕</span>
          加载失败: {{ modalError }}
        </div>
        <div
          v-else-if="!modalStocks.length"
          class="feedback feedback-empty"
        >
          <span class="status-icon status-icon-empty" aria-hidden="true">○</span>
          暂无成分股数据
        </div>
        <template v-else>
          <div class="results-header">
            共 <strong>{{ modalStocks.length }}</strong> 只成分股
            <span v-if="modalSector?.stock_count" class="feedback-meta">
              (板块宣称 {{ modalSector.stock_count }} 只)
            </span>
          </div>
          <el-table :data="modalStocks" stripe class="stocks-table">
            <el-table-column label="#" width="70" align="right" header-align="right">
              <template #default="{ row }">{{ row.rank ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="代码" width="120">
              <template #default="{ row }">
                <code>{{ row.stock_code }}</code>
              </template>
            </el-table-column>
            <el-table-column label="名称" min-width="160">
              <template #default="{ row }">{{ row.stock_name || '' }}</template>
            </el-table-column>
          </el-table>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { OmniSector, OmniSectorStock } from '@/api/omni'
import {
  getOmniSectorStocks,
  type OmniQueryParams,
  type OmniSectorStocksResponse,
  type OmniSource,
} from '@/api/omni'
import { errorMessage } from '@/api/client'
import { displayBoardType, displaySource, formatDate } from './format'

interface Props {
  sectors: OmniSector[]
  totalCount: number
  page: number
  pageSize: number
  loading: boolean
  loadError: string
  hasSearched: boolean
  queryFilters: { source: OmniSource; boardType: string; keyword: string }
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'search', params: OmniQueryParams): void
  (e: 'page-change', page: number): void
}>()

/** 内部 input 状态 — 改 dropdown 立即触发查询, 关键词改 enter/clear 触发。 */
const keyword = ref(props.queryFilters.keyword)
const source = ref<OmniSource>(props.queryFilters.source)
const boardType = ref(props.queryFilters.boardType)

// 与外部 state 同步 (切换 tab 再切回时不丢用户输入)
watch(
  () => props.queryFilters,
  (f) => {
    keyword.value = f.keyword
    source.value = f.source
    boardType.value = f.boardType
  },
)

const totalPages = computed(() => Math.ceil(props.totalCount / props.pageSize))

const queryDesc = computed(() => {
  const parts: string[] = []
  if (source.value) parts.push(`数据源=${displaySource(source.value)}`)
  if (boardType.value) parts.push(`类型=${displayBoardType(boardType.value)}`)
  if (keyword.value.trim()) parts.push(`关键词="${keyword.value.trim()}"`)
  return parts.join(', ') || '(无筛选)'
})

function onSearch() {
  emit('search', {
    source: source.value || undefined,
    board_type: (boardType.value || undefined) as OmniQueryParams['board_type'],
    keyword: keyword.value.trim() || undefined,
    page: 1,
    page_size: props.pageSize,
  })
}

function onPageChange(newPage: number) {
  if (newPage === props.page) return
  emit('page-change', newPage)
}

// ===== 板块成分股弹窗 =====

const modalOpen = ref(false)
const modalLoading = ref(false)
const modalError = ref('')
const modalSector = ref<OmniSectorStocksResponse['sector'] | null>(null)
const modalStocks = ref<OmniSectorStock[]>([])
const modalMeta = ref('')
const modalTitle = ref('板块成分股')

const modalRequest = ref<{ cancel: () => void } | null>(null)

async function openSectorModal(row: OmniSector) {
  modalTitle.value = row.name
  modalMeta.value = `${row.code} · ${displayBoardType(row.board_type)} · ${displaySource(row.source)}`
  modalSector.value = {
    id: row.id,
    source: row.source,
    board_type: row.board_type,
    code: row.code,
    name: row.name,
    stock_count: row.stock_count,
  }
  modalStocks.value = []
  modalError.value = ''
  modalOpen.value = true
}

async function onModalOpen() {
  if (!modalSector.value) return
  await fetchStocks()
}

async function fetchStocks() {
  const sector = modalSector.value
  if (!sector) return
  modalLoading.value = true
  modalError.value = ''
  const ac = new AbortController()
  modalRequest.value = { cancel: () => ac.abort() }
  try {
    const res = await getOmniSectorStocks({ sector_id: sector.id, code: sector.code, source: sector.source }, ac.signal)
    modalSector.value = res.sector
    modalStocks.value = res.stocks || []
  } catch (e) {
    if ((e as { name?: string })?.name === 'CanceledError') return
    modalError.value = errorMessage(e, '未知错误')
  } finally {
    modalLoading.value = false
    modalRequest.value = null
  }
}

watch(modalOpen, (open) => {
  if (!open) {
    modalRequest.value?.cancel()
    modalRequest.value = null
    modalError.value = ''
  }
})

defineExpose({
  /** 由 OmniView 在切换 tab 时调用, 避免离开后弹窗残留。 */
  closeModal: () => {
    modalRequest.value?.cancel()
    modalOpen.value = false
  },
})
</script>

<style scoped>
.query-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.query-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.query-keyword {
  width: 320px;
  max-width: 100%;
}

.results-wrap {
  min-height: 200px;
}

.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px 16px;
  border-radius: 6px;
  font-size: 0.85rem;
}

.feedback-info {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
}

.feedback-empty {
  background: var(--el-fill-color-blank);
  border: 1px dashed var(--el-border-color);
  color: var(--el-text-color-secondary);
  justify-content: center;
}

.feedback-error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.feedback-meta {
  margin-left: 4px;
  color: var(--el-text-color-secondary);
  font-size: 0.78rem;
}

.results-header {
  font-size: 0.85rem;
  margin-bottom: 8px;
}

.results-hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-weight: 400;
  font-size: 0.78rem;
}

.sector-table {
  cursor: pointer;
}

.sector-name {
  font-weight: 500;
}

.pagination {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin-top: 12px;
}

.page-info {
  font-size: 0.78rem;
  color: var(--el-text-color-secondary);
}

.modal-meta {
  font-size: 0.82rem;
  color: var(--el-text-color-secondary);
  margin-bottom: 12px;
}

.stocks-table code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
}

.status-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 0.7rem;
}

.status-icon-error {
  color: #b91c1c;
}

.status-icon-empty {
  color: var(--el-text-color-secondary);
}
</style>
