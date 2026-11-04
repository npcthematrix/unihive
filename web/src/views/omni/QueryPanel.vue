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
      <el-select v-model="source" class="query-select" placeholder="全部数据源" clearable @change="onSearch">
        <el-option
          v-for="key in SOURCE_KEYS"
          :key="key"
          :label="displaySource(key)"
          :value="key"
        />
      </el-select>
      <el-select v-model="boardType" class="query-select" placeholder="全部类型" clearable @change="onSearch">
        <el-option
          v-for="key in BOARD_TYPE_KEYS"
          :key="key"
          :label="displayBoardType(key)"
          :value="key"
        />
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
          <span class="results-hint">点击板块名称查看成分股</span>
        </div>

        <el-table
          :data="sectors"
          class="sector-table"
          @row-click="openSectorModal"
          stripe
        >
          <el-table-column label="板块名称" min-width="180">
            <template #default="{ row }">
              <!-- 用真实 button 提供键盘等价入口: el-table 的行不可聚焦,
                   只靠 @row-click 时键盘用户无法打开成分股弹窗 -->
              <button
                type="button"
                class="sector-link"
                :title="row.name"
                :aria-label="`查看 ${row.name} 的成分股`"
                @click.stop="openSectorModal(row)"
              >{{ row.name }}</button>
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
          <!-- 不放 jumper/total: 窄屏会横向溢出; 页数/总条数下方 page-info 已展示 -->
          <el-pagination
            :current-page="page"
            :page-size="pageSize"
            :total="totalCount"
            :pager-count="5"
            layout="prev, pager, next"
            background
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
      class="sector-dialog"
      width="min(600px, calc(100vw - 32px))"
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
          <!-- max-height: 大板块（最大约 1500 行）表内滚动、表头吸顶;
               否则弹窗超出视口、锁页时底部不可达 -->
          <el-table :data="modalStocks" stripe class="stocks-table" max-height="55vh">
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
import { computed, ref, shallowRef, watch } from 'vue'
import type { OmniSector, OmniSectorStock } from '@/api/omni'
import {
  getOmniSectorStocks,
  type OmniQueryParams,
  type OmniSectorStocksResponse,
  type OmniSource,
} from '@/api/omni'
import { errorMessage, isCanceledError } from '@/api/client'
import {
  BOARD_TYPE_KEYS,
  SOURCE_KEYS,
  buildQueryDesc,
  displayBoardType,
  displaySource,
  formatDate,
} from './format'

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
  { deep: true },
)

const totalPages = computed(() => Math.ceil(props.totalCount / props.pageSize))

/**
 * 结果描述必须读**已提交**的 props.queryFilters, 而不是上面的输入框草稿:
 * 用户改了关键词但没回车时, 读草稿会让描述与实际展示的数据不一致。
 */
const queryDesc = computed(() =>
  buildQueryDesc({
    source: props.queryFilters.source,
    boardType: props.queryFilters.boardType,
    keyword: props.queryFilters.keyword,
  }),
)

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

// 必须 shallowRef：ref 会把句柄对象包成 reactive 代理，.value 与原对象
// 不再 ===，导致每次响应都被误判"已被新请求取代"，弹窗永久 loading。
const modalRequest = shallowRef<{ cancel: () => void } | null>(null)

function openSectorModal(row: OmniSector) {
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
  const reqHandle = { cancel: () => ac.abort() }
  modalRequest.value = reqHandle
  try {
    const res = await getOmniSectorStocks({ sector_id: sector.id, code: sector.code, source: sector.source }, ac.signal)
    // 弹窗已关/已被新请求取代 → 不回写, 避免关闭后错误被 watch 清空又复活
    if (ac.signal.aborted || modalRequest.value !== reqHandle) return
    // 后端业务失败是 HTTP 200 + {status:'failed'}，axios 不会抛错；
    // 不检查就会把真实原因显示成"暂无成分股数据"。
    if (res.status === 'failed') {
      modalError.value = res.message || '加载失败'
      return
    }
    modalSector.value = res.sector
    modalStocks.value = res.stocks || []
  } catch (e) {
    if (isCanceledError(e)) return
    modalError.value = errorMessage(e, '未知错误')
  } finally {
    if (modalRequest.value === reqHandle) {
      modalLoading.value = false
      modalRequest.value = null
    }
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
  min-width: 200px;
}

.query-select {
  width: 160px;
}

/* 窄屏: keyword 撑满一行, 两个 select 收窄紧跟其后; 避免 640-768px 区间
   320+160+160 = 640 与 padding/gap 叠加撑爆 query-bar 的尴尬换行。 */
@media (max-width: 768px) {
  .query-keyword {
    width: 100%;
  }
  .query-select {
    width: 130px;
  }
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
  font-size: 13px;
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
  /* Element Plus danger 语义色, 浅/暗主题自动适配 (旧硬编码红在暗色下刺眼) */
  background: var(--el-color-danger-light-9, #fef0f0);
  color: var(--el-color-danger, #f56c6c);
  border: 1px solid var(--el-color-danger-light-7, #fbc4c4);
}

.feedback-meta {
  margin-left: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.results-header {
  font-size: 13px;
  margin-bottom: 8px;
}

.results-hint {
  margin-left: 12px;
  color: var(--el-text-color-placeholder);
  font-weight: 400;
  font-size: 12px;
  font-style: italic;
}

.sector-table {
  cursor: pointer;
}

/* 板块名 = 可聚焦按钮 (键盘等价入口), 外观保持链接感 */
.sector-link {
  appearance: none;
  -webkit-appearance: none;
  background: none;
  border: 0;
  padding: 0;
  font: inherit;
  font-weight: 500;
  color: var(--el-color-primary);
  text-align: left;
  cursor: pointer;
  border-radius: 3px;
}

.sector-link:hover {
  text-decoration: underline;
}

.sector-link:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.pagination {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin-top: 12px;
}

.page-info {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.modal-meta {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 12px;
}

.stocks-table code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.status-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 11px;
}

/* 用 Element Plus danger 语义色, 浅/暗主题自动适配
   (旧硬编码 #b91c1c 在暗色主题下与浅底容器对比度过低) */
.status-icon-error {
  color: var(--el-color-danger);
}

.status-icon-empty {
  color: var(--el-text-color-secondary);
}
</style>

<!-- 弹窗 teleport 到 body, scoped 的 scopeId 不保证能穿透到其内部结构,
     故用显式 class + 全局规则。仅作用于本页成分股弹窗, 不影响其他 dialog。 -->
<style>
.sector-dialog .el-dialog__title {
  display: block;
  /* 右上角关闭按钮是 absolute 定位, 留出空间避免遮住标题末尾 */
  padding-right: 24px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
