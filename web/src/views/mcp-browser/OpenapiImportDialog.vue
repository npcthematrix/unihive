<script setup lang="ts">
/**
 * FQGate OpenAPI 实时导入对话框 (P4)。
 *
 * 活拉 THS-LOCAL-GATEWAY 的 openapi.json + tools/list, 展示新增/契约变更台账,
 * 用户勾选 → 带白名单重新 preview (impact / YAML 片段都以后端按白名单重算的
 * 最新 preview 为准, 前端不自己切片段) → 强确认后热应用: 工具以 origin='user'
 * 写 config.db 并即时热注册。apply 永不删工具, removed 仅顾问提示。
 *
 * 所有可判断的逻辑在 ./openapiImport.ts (vitest 覆盖); 本组件只做渲染与转发。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  applyOpenapiImport,
  previewOpenapiImport,
  type OpenapiImportApplyResult,
  type OpenapiImportPreview,
} from '@/api/config'
import { errorMessage } from '@/api/client'
import { copyText } from '@/utils/clipboard'
import {
  actionLabel,
  actionTagType,
  canApplyImport,
  changedFieldLabel,
  defaultSelection,
  downloadText,
  fragmentFilename,
  hasImportImpact,
  importImpactGroups,
  isAllSelected,
  isRevisionConflictError,
  isUpstreamFetchError,
  isSelectableAction,
  selectableChanges,
  toggleSelected,
} from '@/views/mcp-browser/openapiImport'

const props = defineProps<{
  modelValue: boolean
  upstream: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'applied', result: OpenapiImportApplyResult): void
}>()

const loading = ref(false)
const applying = ref(false)
const loadError = ref('')
const applyError = ref('')
const preview = ref<OpenapiImportPreview | null>(null)
const selected = ref<string[]>([])
const confirmed = ref(false)
const copied = ref(false)

let previewSeq = 0
let debounceTimer: ReturnType<typeof setTimeout> | undefined
let copiedTimer: ReturnType<typeof setTimeout> | undefined

const rows = computed(() => (preview.value ? selectableChanges(preview.value.changes) : []))
const allChecked = computed(() => isAllSelected(preview.value, selected.value))
const canApply = computed(() => canApplyImport(preview.value, selected.value))
const groups = computed(() =>
  preview.value ? importImpactGroups(preview.value.impact) : [],
)
const lintErrors = computed(() => preview.value?.lint_errors ?? [])
const lintWarnings = computed(() => preview.value?.lint_warnings ?? [])
const upstreamHint = '请确认 THS-LOCAL-GATEWAY 已启动 (默认 http://127.0.0.1:17281)'

function reset(): void {
  preview.value = null
  selected.value = []
  confirmed.value = false
  loadError.value = ''
  applyError.value = ''
  copied.value = false
  if (debounceTimer) clearTimeout(debounceTimer)
}

function close(): void {
  emit('update:modelValue', false)
}

function onVisibleUpdate(value: boolean): void {
  if (!value) close()
  else emit('update:modelValue', true)
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      reset()
      void fetchPreview(undefined)
    } else {
      reset()
    }
  },
)

async function fetchPreview(tools: string[] | undefined): Promise<void> {
  const seq = ++previewSeq
  loading.value = true
  loadError.value = ''
  try {
    const result = await previewOpenapiImport({
      upstream: props.upstream,
      // 空数组后端 400; 全不选时保留上一份计划展示, 应用门本身是关的。
      tools: tools && tools.length ? tools : undefined,
    })
    if (seq !== previewSeq) return
    preview.value = result
    if (tools === undefined) selected.value = defaultSelection(result)
  } catch (e: unknown) {
    if (seq !== previewSeq) return
    preview.value = null
    loadError.value = isUpstreamFetchError(e)
      ? `${errorMessage(e, '上游拉取失败')}。${upstreamHint}`
      : errorMessage(e, '生成导入计划失败')
  } finally {
    if (seq === previewSeq) loading.value = false
  }
}

/** 勾选变化 → 300ms 防抖后带白名单重新生成计划 (impact/片段按选择重算)。 */
function scheduleRefetch(): void {
  confirmed.value = false
  if (debounceTimer) clearTimeout(debounceTimer)
  if (selected.value.length === 0) return
  debounceTimer = setTimeout(() => {
    void fetchPreview(selected.value)
  }, 300)
}

function onToggleRow(name: string): void {
  selected.value = toggleSelected(selected.value, name)
  scheduleRefetch()
}

function onToggleAll(): void {
  if (!preview.value) return
  if (allChecked.value) {
    selected.value = []
  } else {
    selected.value = selectableChanges(preview.value.changes).map((c) => c.name)
  }
  scheduleRefetch()
}

async function retry(): Promise<void> {
  await fetchPreview(selected.value.length ? selected.value : undefined)
}

async function copyFragment(): Promise<void> {
  if (!preview.value?.yaml_fragment) return
  const ok = await copyText(preview.value.yaml_fragment, 'YAML 片段已复制')
  if (!ok) return
  copied.value = true
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => {
    copied.value = false
  }, 1500)
}

function downloadFragment(): void {
  if (!preview.value?.yaml_fragment) return
  downloadText(fragmentFilename(), preview.value.yaml_fragment)
}

async function apply(): Promise<void> {
  if (!preview.value || !canApply.value || !confirmed.value || applying.value) return
  applying.value = true
  applyError.value = ''
  try {
    const result = await applyOpenapiImport({
      upstream: props.upstream,
      tools: selected.value,
      revision: preview.value.revision,
    })
    if (!result.changed) {
      ElMessage.info('上游契约与库内现状一致, 无需导入')
      close()
      return
    }
    const n =
      (result.tools_added?.length ?? 0) + (result.tools_republished?.length ?? 0)
    ElMessage.success(`已热导入 ${n} 个工具 (revision ${result.revision})`)
    emit('applied', result)
    close()
  } catch (e: unknown) {
    if (isRevisionConflictError(e)) {
      ElMessage.error('配置已被其他操作改动, 已按最新 revision 重新生成计划, 请复核后再应用')
      confirmed.value = false
      await fetchPreview(undefined)
      selected.value = preview.value ? defaultSelection(preview.value) : []
      return
    }
    applyError.value = errorMessage(e, '热导入失败, 运行态与配置库均已回滚')
  } finally {
    applying.value = false
  }
}

const impactToneClass: Record<string, string> = {
  success: 'imp-success',
  warning: 'imp-warning',
  danger: 'imp-danger',
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="从 OpenAPI 实时导入工具契约"
    width="min(960px, calc(100vw - 32px))"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="onVisibleUpdate"
  >
    <div v-loading="loading" class="oi-body">
      <el-alert
        v-if="loadError"
        type="error"
        show-icon
        :closable="false"
        class="oi-alert"
        :title="loadError"
      >
        <div class="oi-error-actions">
          <el-button size="small" @click="retry">重试</el-button>
        </div>
      </el-alert>

      <template v-else-if="preview">
        <!-- 台账计数 -->
        <div class="oi-summary">
          <el-tag size="small" type="success" effect="plain">
            新增 {{ preview.summary.added }}
          </el-tag>
          <el-tag size="small" type="warning" effect="plain">
            契约变更 {{ preview.summary.changed }}
          </el-tag>
          <el-tag size="small" type="info" effect="plain">
            无变化 {{ preview.summary.unchanged }}
          </el-tag>
          <el-tooltip
            v-if="preview.unmapped.length"
            placement="top"
            effect="light"
          >
            <template #content>
              <div class="oi-tip">
                <div v-for="u in preview.unmapped" :key="u.name">
                  <code>{{ u.name }}</code>: {{ u.reason }}
                </div>
              </div>
            </template>
            <el-tag size="small" type="danger" effect="plain">
              无法映射 {{ preview.summary.unmapped }}
            </el-tag>
          </el-tooltip>
          <el-tooltip
            v-if="preview.removed.length"
            placement="top"
            effect="light"
          >
            <template #content>
              <div class="oi-tip">
                <div v-for="n in preview.removed" :key="n">
                  <code>{{ n }}</code>
                </div>
              </div>
            </template>
            <el-tag size="small" type="info" effect="plain">
              上游已下线 {{ preview.summary.removed }}
            </el-tag>
          </el-tooltip>
          <span class="oi-rev">revision {{ preview.revision }}</span>
        </div>

        <el-alert
          v-if="preview.removed.length"
          type="info"
          :closable="false"
          show-icon
          class="oi-alert"
          title="“上游已下线” 仅为顾问提示: 导入不会删除任何库内工具, 需下架时请到配置页手动处理。"
        />

        <el-alert
          v-if="lintErrors.length"
          type="error"
          :closable="false"
          show-icon
          class="oi-alert"
          title="候选契约未过静态校验, 不能应用:"
        >
          <ul class="oi-issues">
            <li v-for="(i, idx) in lintErrors" :key="`${i.code}-${idx}`">
              <code>{{ i.code }}</code> {{ i.message }}
            </li>
          </ul>
        </el-alert>

        <el-alert
          v-if="lintWarnings.length"
          type="warning"
          :closable="false"
          show-icon
          class="oi-alert"
          title="静态校验警告 (不阻断):"
        >
          <ul class="oi-issues">
            <li v-for="(i, idx) in lintWarnings" :key="`${i.code}-${idx}`">
              <code>{{ i.code }}</code> {{ i.message }}
            </li>
          </ul>
        </el-alert>

        <!-- 工具台账 -->
        <div class="oi-table-head">
          <el-checkbox
            :model-value="allChecked"
            :disabled="!rows.length"
            @change="onToggleAll"
          >
            全选 ({{ selected.length }}/{{ rows.length }})
          </el-checkbox>
        </div>
        <el-table :data="preview.changes" size="small" class="oi-table" max-height="280">
          <el-table-column width="48">
            <template #default="{ row }">
              <el-checkbox
                v-if="isSelectableAction(row.action)"
                :model-value="selected.includes(row.name)"
                @change="onToggleRow(row.name)"
              />
            </template>
          </el-table-column>
          <el-table-column label="工具" prop="name" min-width="240">
            <template #default="{ row }">
              <span class="oi-name">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="处置" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="actionTagType(row.action)" effect="plain">
                {{ actionLabel(row.action) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="变化字段" min-width="180">
            <template #default="{ row }">
              <span v-if="row.changed_fields.length" class="oi-fields">
                <el-tag
                  v-for="f in row.changed_fields"
                  :key="f"
                  size="small"
                  effect="plain"
                >
                  {{ changedFieldLabel(f) }}
                </el-tag>
              </span>
              <span v-else class="oi-muted">—</span>
            </template>
          </el-table-column>
        </el-table>

        <!-- 影响面 -->
        <div v-if="hasImportImpact(preview.impact)" class="oi-impact">
          <div v-for="g in groups" :key="g.key" class="oi-impact-row">
            <span class="oi-impact-label" :class="impactToneClass[g.tone]">{{ g.label }}</span>
            <span class="oi-impact-names">
              <code v-for="n in g.names" :key="n">{{ n }}</code>
            </span>
          </div>
        </div>

        <!-- YAML 评审片段 -->
        <div class="oi-frag-head">
          <span class="oi-frag-title">
            YAML 片段 ({{ preview.selected.length }} 个工具, 评审后并入出厂模板即转 builtin)
          </span>
          <span class="oi-frag-actions">
            <el-button size="small" @click="copyFragment">
              {{ copied ? '已复制' : '复制片段' }}
            </el-button>
            <el-button size="small" @click="downloadFragment">下载 .yaml</el-button>
          </span>
        </div>
        <pre v-if="preview.yaml_fragment" class="oi-fragment">{{ preview.yaml_fragment }}</pre>
        <div v-else class="oi-muted oi-no-frag">当前选择无需导入</div>
      </template>

      <div v-else-if="!loading" class="oi-muted">尚无导入计划</div>
    </div>

    <template #footer>
      <el-alert
        v-if="applyError"
        type="error"
        :closable="false"
        show-icon
        class="oi-apply-error"
        :title="applyError"
      />
      <el-checkbox
        v-if="preview"
        v-model="confirmed"
        :disabled="!canApply"
        class="oi-confirm"
      >
        我已评审 YAML 片段与影响面, 确认热写 config.db 并即时重注册
        {{ canApply ? selected.length : 0 }} 个工具契约
      </el-checkbox>
      <div class="oi-footer-actions">
        <el-button @click="close">取消</el-button>
        <el-button
          type="warning"
          :loading="applying"
          :disabled="!canApply || !confirmed"
          @click="apply"
        >
          应用导入
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.oi-body {
  min-height: 200px;
}

.oi-alert {
  margin-bottom: 10px;
}

.oi-error-actions {
  margin-top: 6px;
}

.oi-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.oi-rev {
  margin-left: auto;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  font-variant-numeric: tabular-nums;
}

.oi-tip {
  max-width: 420px;
  font-size: 12px;
  line-height: 1.6;
}

.oi-issues {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
}

.oi-table-head {
  margin: 4px 0 6px;
}

.oi-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.oi-fields {
  display: inline-flex;
  gap: 4px;
  flex-wrap: wrap;
}

.oi-muted {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.oi-impact {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
}

.oi-impact-row {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-size: 12px;
}

.oi-impact-label {
  flex: 0 0 132px;
  font-weight: 600;
}

.oi-impact-names {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
}

.imp-success {
  color: var(--el-color-success);
}

.imp-warning {
  color: var(--el-color-warning);
}

.imp-danger {
  color: var(--el-color-danger);
}

.oi-frag-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 14px 0 6px;
  gap: 8px;
}

.oi-frag-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.oi-fragment {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.6;
  white-space: pre;
}

.oi-no-frag {
  padding: 12px;
}

.oi-apply-error {
  margin-bottom: 10px;
  text-align: left;
}

.oi-confirm {
  display: block;
  text-align: left;
  margin-bottom: 12px;
}

.oi-footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
