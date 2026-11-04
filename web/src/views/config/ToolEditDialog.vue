<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(820px, calc(100vw - 32px))"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="onVisibleUpdate"
    @open="onOpen"
  >
    <div v-loading="loading" class="ted-body">
      <el-alert
        v-if="loadError"
        type="error"
        show-icon
        :closable="false"
        title="实体加载失败"
        :description="loadError"
      />

      <template v-else-if="entity && form">
        <el-alert
          v-if="conflictNotice"
          type="warning"
          show-icon
          :closable="false"
          class="ted-alert"
          title="配置已被其他会话修改, 表单已按最新实体重新加载, 请复核后再次保存"
        />
        <el-alert
          v-if="lintIssues.length"
          type="error"
          show-icon
          :closable="false"
          class="ted-alert"
          title="后端静态校验拒绝本次修改 (未写入、未改动运行态):"
        >
          <ul class="ted-issue-list">
            <li v-for="(issue, i) in lintIssues" :key="`${issue.code}-${i}`">
              <code>{{ issue.code }}</code> {{ issue.message }}
            </li>
          </ul>
        </el-alert>

        <div class="ted-meta">
          <el-tag size="small" :type="entity.origin === 'builtin' ? 'info' : 'warning'">
            {{ entity.origin === 'builtin' ? '出厂模板' : '用户修改' }}
          </el-tag>
          <span class="ted-meta-item">源文件: <code>{{ entity.source_file || '—' }}</code></span>
          <span class="ted-meta-item">revision {{ revision }}</span>
          <span class="ted-meta-item">{{ formatUpdatedAt(entity.updated_at) }}</span>
        </div>

        <!-- 描述层: 普通保存, 即写即热生效 -->
        <fieldset class="ted-group">
          <legend>描述层 — 保存即热生效, 不改变调用契约</legend>

          <label class="ted-field">
            <span class="ted-label">描述 (description)</span>
            <el-input
              v-model="form.description"
              type="textarea"
              :rows="2"
              placeholder="工具描述, 将展示在 tools/list"
            />
          </label>

          <div class="ted-field-row">
            <label class="ted-field ted-field-grow">
              <span class="ted-label">缓存档 (cache_ttl_key)</span>
              <el-input
                v-model="form.cacheTtlKey"
                placeholder="留空 = 不设缓存档 (null)"
                clearable
              />
            </label>
            <label class="ted-field ted-field-switch">
              <span class="ted-label">发布状态</span>
              <el-switch
                v-model="form.publishHidden"
                active-text="手动下架 (hidden)"
                inactive-text="正常发布 (default)"
              />
            </label>
          </div>

          <div class="ted-field">
            <div class="ted-label-row">
              <span class="ted-label">参数 (params)</span>
              <el-button size="small" text type="primary" @click="addParam">+ 添加参数</el-button>
            </div>
            <div v-if="!form.params.length" class="muted ted-empty">无参数</div>
            <div v-for="(param, idx) in form.params" :key="idx" class="ted-param-row">
              <el-input v-model="param.name" class="ted-p-name" placeholder="参数名 *" />
              <el-input v-model="param.type" class="ted-p-type" placeholder="类型 str/int…" />
              <el-input v-model="param.description" class="ted-p-desc" placeholder="参数描述" />
              <el-input v-model="param.enumText" class="ted-p-enum" placeholder='enum JSON, 如 ["a","b"]' />
              <el-checkbox v-model="param.required">必填</el-checkbox>
              <el-button
                size="small"
                text
                type="danger"
                :aria-label="`删除参数第 ${idx + 1} 行`"
                @click="removeParam(idx)"
              >删除</el-button>
            </div>
          </div>
        </fieldset>

        <!-- 契约层: 强确认 + lint + 热重注册 -->
        <fieldset class="ted-group ted-contract">
          <legend>契约层 — 改变对外 /mcp 契约, 需强确认并热重注册</legend>

          <div class="ted-field-row">
            <label class="ted-field ted-field-grow">
              <span class="ted-label">工具名 (name) — 字母开头, 字母/数字/下划线</span>
              <el-input v-model="form.name" placeholder="get_xxx" />
            </label>
            <label class="ted-field ted-field-grow">
              <span class="ted-label">路由键 (routing)</span>
              <el-input v-model="form.routing" placeholder="通常与工具名一致" />
            </label>
          </div>

          <label class="ted-field">
            <span class="ted-label">环境变量门控 (requires_env) — 留空 = 无门控 (null)</span>
            <el-input v-model="form.requiresEnv" placeholder="如 ALLOW_WATCHLIST_WRITE" clearable />
          </label>

          <div class="ted-field">
            <div class="ted-label-row">
              <span class="ted-label">上游映射 (mapping) — 路由优先级按行序</span>
              <el-button size="small" text type="primary" @click="addMapping">+ 添加映射</el-button>
            </div>
            <div v-if="!form.mapping.length" class="muted ted-empty">无映射 (依赖 routing.chain)</div>
            <div v-for="(m, idx) in form.mapping" :key="idx" class="ted-mapping-row">
              <el-input v-model="m.upstream" class="ted-m-up" placeholder="上游名, 如 fuyao_ashare" />
              <span class="ted-m-arrow">→</span>
              <el-input v-model="m.method" class="ted-m-method" placeholder="上游方法名" />
              <el-button
                size="small"
                text
                type="danger"
                :aria-label="`删除映射第 ${idx + 1} 行`"
                @click="removeMapping(idx)"
              >删除</el-button>
            </div>
          </div>
        </fieldset>
      </template>
    </div>

    <template #footer>
      <el-button @click="close" :disabled="saving">取消</el-button>
      <el-button
        :type="preview?.contractFields.length ? 'danger' : 'primary'"
        :loading="saving"
        :disabled="!entity || !form || !!loadError || !!preview?.errors.length"
        @click="onSave"
      >
        {{ saveLabel }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getToolEntity,
  updateToolEntity,
  type LintIssueBody,
  type ToolEntity,
} from '@/api/config'
import { formatUpdatedAt } from './entities'
import { describeEntityEditError } from './entityErrors'
import {
  buildToolPatch,
  toolFormFromEntity,
  type ToolContractField,
  type ToolEditForm,
} from './toolEdit'

interface Props {
  modelValue: boolean
  toolName: string | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [payload: {
    name: string
    revision: number
    published: boolean
    previouslyPublished: boolean
    changed: boolean
  }]
}>()

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const entity = ref<ToolEntity | null>(null)
const form = ref<ToolEditForm | null>(null)
const revision = ref<number>(0)
const lintIssues = ref<LintIssueBody[]>([])
const conflictNotice = ref(false)

/** 打开时实体的原名 — 改名后 PUT 仍打旧名路径 (后端在同事务内删旧插新)。 */
const originalName = ref('')

const CONTRACT_LABELS: Record<ToolContractField, string> = {
  name: '工具名 (name)',
  routing: '路由键 (routing)',
  requires_env: '环境变量门控 (requires_env)',
  mapping: '上游映射 (mapping)',
}

const dialogTitle = computed(() =>
  props.toolName ? `编辑工具 ${props.toolName}` : '编辑工具',
)

/** 实时预览本次保存的 patch (形态错误/契约字段用于按钮态与确认框)。 */
const preview = computed(() => {
  if (!entity.value || !form.value) return null
  return buildToolPatch(form.value, entity.value)
})

const saveLabel = computed(() => {
  if (!preview.value) return '保存'
  if (!Object.keys(preview.value.patch).length) return '保存 (无变更)'
  return preview.value.contractFields.length
    ? '保存并热重注册 (契约变更)'
    : '保存 (热生效)'
})

async function onOpen(): Promise<void> {
  await reloadEntity(false)
}

async function reloadEntity(noticeConflict: boolean): Promise<void> {
  if (!props.toolName) return
  loading.value = true
  loadError.value = ''
  lintIssues.value = []
  conflictNotice.value = noticeConflict
  try {
    const detail = await getToolEntity(props.toolName)
    if (detail.source === 'yaml') {
      loadError.value = '网关以 YAML 逃生模式启动, 配置库只读, 不可编辑'
      entity.value = null
      form.value = null
      return
    }
    entity.value = detail.tool
    originalName.value = detail.tool.name
    revision.value = detail.revision
    form.value = toolFormFromEntity(detail.tool)
  } catch (e: unknown) {
    const info = describeEntityEditError(e)
    if (info.kind === 'not_found') {
      loadError.value = '该工具不是配置库实体 (可能是内置状态工具), 不可编辑'
    } else {
      loadError.value = info.message
    }
    entity.value = null
    form.value = null
  } finally {
    loading.value = false
  }
}

function addParam(): void {
  if (!form.value) return
  form.value = {
    ...form.value,
    params: [
      ...form.value.params,
      { name: '', type: '', required: false, description: '', enumText: '', extra: {} },
    ],
  }
}

function removeParam(idx: number): void {
  if (!form.value) return
  form.value = {
    ...form.value,
    params: form.value.params.filter((_, i) => i !== idx),
  }
}

function addMapping(): void {
  if (!form.value) return
  form.value = {
    ...form.value,
    mapping: [...form.value.mapping, { upstream: '', method: '' }],
  }
}

function removeMapping(idx: number): void {
  if (!form.value) return
  form.value = {
    ...form.value,
    mapping: form.value.mapping.filter((_, i) => i !== idx),
  }
}

async function onSave(): Promise<void> {
  if (!entity.value || !form.value || !preview.value || saving.value) return
  const { patch, contractFields, errors } = preview.value
  if (errors.length) {
    ElMessage.error(`表单有 ${errors.length} 处错误, 请修正后再保存`)
    return
  }
  if (!Object.keys(patch).length) {
    ElMessage.info('表单与实体一致, 无变更')
    return
  }

  if (contractFields.length) {
    const lines = contractFields.map((f) => `- ${CONTRACT_LABELS[f]}`).join('\n')
    try {
      await ElMessageBox.confirm(
        `本次保存包含契约层变更:\n\n${lines}\n\n`
        + '这些字段会改变对外 /mcp 的调用契约。保存时后端先跑静态校验, '
        + '通过后立即热重注册 (无需重启); 校验失败则完全不生效。确认继续?',
        '契约层变更强确认',
        {
          confirmButtonText: '确认保存并重注册',
          cancelButtonText: '取消',
          type: 'warning',
          distinguishCancelAndClose: true,
        },
      )
    } catch {
      return
    }
  }

  saving.value = true
  lintIssues.value = []
  try {
    const result = await updateToolEntity(originalName.value, patch, {
      confirmDangerous: contractFields.length > 0,
      revision: revision.value,
    })
    if (!result.changed) {
      ElMessage.info('表单与实体一致, 无变更')
    } else {
      ElMessage.success(successMessage(result.published, result.previously_published))
    }
    emit('saved', {
      name: result.name,
      revision: result.revision,
      published: result.published,
      previouslyPublished: result.previously_published,
      changed: result.changed,
    })
    close()
  } catch (e: unknown) {
    await handleSaveError(e)
  } finally {
    saving.value = false
  }
}

function successMessage(published: boolean, previouslyPublished: boolean): string {
  if (published && previouslyPublished) return '已保存, 接口已热重注册'
  if (published && !previouslyPublished) return '已保存, 接口已发布到对外 /mcp'
  if (!published && previouslyPublished) return '已保存, 接口已从对外 /mcp 移除'
  return '已保存 (该工具当前未发布)'
}

async function handleSaveError(e: unknown): Promise<void> {
  const info = describeEntityEditError(e)
  if (info.kind === 'lint_rejected') {
    lintIssues.value = info.issues
    ElMessage.error(`静态校验未通过 (${info.issues.length} 项), 未写入`)
    return
  }
  if (info.kind === 'revision_conflict') {
    ElMessage.warning('配置版本已变更, 正在重新加载最新实体')
    await reloadEntity(true)
    return
  }
  if (info.kind === 'confirmation_required') {
    ElMessage.error('后端要求契约层强确认, 请重新点击保存并确认')
    return
  }
  if (info.kind === 'store_unavailable') {
    ElMessage.error('配置库不可用 (YAML 逃生模式), 无法热编辑')
    return
  }
  ElMessage.error(info.message)
}

function onVisibleUpdate(value: boolean): void {
  if (!value) close()
  else emit('update:modelValue', true)
}

function close(): void {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.ted-body {
  min-height: 160px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ted-alert {
  margin-bottom: 0;
}

.ted-issue-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 0.82rem;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ted-issue-list code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: var(--el-fill-color-light);
  padding: 0 4px;
  border-radius: 3px;
}

.ted-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 0.75rem;
  color: var(--cfg-text-secondary, var(--el-text-color-secondary));
}

.ted-meta-item code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.72rem;
}

.ted-group {
  border: 1px solid var(--cfg-border, var(--el-border-color));
  border-radius: 6px;
  padding: 12px 14px 14px;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ted-group.ted-contract {
  border-color: var(--el-color-danger-light-5);
  background: var(--el-color-danger-light-9);
}

.ted-group legend {
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0 6px;
  color: var(--el-text-color-regular);
}

.ted-contract legend {
  color: var(--el-color-danger-dark-2);
}

.ted-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ted-field-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.ted-field-grow {
  flex: 1 1 220px;
}

.ted-field-switch {
  flex: 0 0 auto;
  justify-content: flex-end;
}

.ted-label {
  font-size: 0.78rem;
  color: var(--el-text-color-regular);
}

.ted-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ted-empty {
  font-size: 0.78rem;
}

/* 弹窗宽 = min(820px, 100vw - 32px), 内容区在视口 752~900px 时已窄于
   固定列合计 (~720px)。过去靠 `@media (max-width: 720px)` 触发 wrap,
   断点看的是视口而不是弹窗内容区, 恰好漏掉这段溢出区间。
   改为恒可换行 + 弹性 basis: 宽屏自然排一行, 窄屏自动折行, 不依赖断点。 */
.ted-param-row,
.ted-mapping-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ted-p-name { flex: 1 1 130px; min-width: 0; }
.ted-p-type { flex: 1 1 110px; min-width: 0; }
.ted-p-desc { flex: 2 1 180px; min-width: 0; }
.ted-p-enum { flex: 2 1 180px; min-width: 0; }

.ted-m-up { flex: 1 1 180px; min-width: 0; }
.ted-m-method { flex: 1 1 180px; min-width: 0; }
.ted-m-arrow {
  color: var(--el-text-color-secondary);
  flex: 0 0 auto;
}
</style>
