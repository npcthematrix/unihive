<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(720px, calc(100vw - 32px))"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="onVisibleUpdate"
    @open="onOpen"
  >
    <div v-loading="loading" class="ued-body">
      <el-alert
        v-if="loadError"
        type="error"
        show-icon
        :closable="false"
        title="实体加载失败"
        :description="loadError"
      />

      <template v-else-if="entity">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          class="ued-alert"
          title="连接块是建连契约: 保存会断开并重连该上游 (启用态), 其接口热重注册, 无需重启。"
          description="密钥字段只接受 ${环境变量} 占位符, 明文密钥会被拒绝; 实际值写在 .env。enabled 不在此编辑, 请用列表里的启用/停用开关。"
        />
        <el-alert
          v-if="conflictNotice"
          type="warning"
          show-icon
          :closable="false"
          class="ued-alert"
          title="配置已被其他会话修改, 已按最新实体重新加载, 请复核后再次保存"
        />
        <el-alert
          v-if="formError"
          type="error"
          show-icon
          :closable="false"
          class="ued-alert"
          :title="formError"
        >
          <ul v-if="formErrors.length" class="ued-error-list">
            <li v-for="(msg, i) in formErrors" :key="i">{{ msg }}</li>
          </ul>
        </el-alert>

        <div class="ued-meta">
          <el-tag size="small" :type="entity.origin === 'builtin' ? 'info' : 'warning'">
            {{ entity.origin === 'builtin' ? '出厂模板' : '用户修改' }}
          </el-tag>
          <el-tag size="small" :type="entity.enabled ? 'success' : 'info'" effect="plain">
            {{ entity.enabled ? '当前启用' : '当前停用' }}
          </el-tag>
          <span class="ued-meta-item">revision {{ revision }}</span>
          <span class="ued-meta-item">{{ formatUpdatedAt(entity.updated_at) }}</span>
        </div>

        <label class="ued-field">
          <span class="ued-label">上游类型 (type)</span>
          <el-select
            v-model="typeInput"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入类型"
            class="ued-type-select"
          >
            <el-option
              v-for="t in knownTypes"
              :key="t"
              :label="t"
              :value="t"
            />
          </el-select>
        </label>

        <div class="ued-field">
          <span class="ued-label">连接块 (config JSON, 不含 enabled/type)</span>
          <el-input
            v-model="configText"
            type="textarea"
            :rows="14"
            class="ued-json"
            spellcheck="false"
            placeholder='{ "base_url": "http://127.0.0.1:17281/mcp" }'
          />
        </div>
      </template>
    </div>

    <template #footer>
      <el-button @click="close" :disabled="saving">取消</el-button>
      <el-button
        type="danger"
        :loading="saving"
        :disabled="!entity || !!loadError || !canSave"
        @click="onSave"
      >
        {{ changed ? '保存并热重连 (契约变更)' : '保存 (无变更)' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getUpstreamEntity,
  updateUpstreamConfigEntity,
  type UpstreamEntity,
} from '@/api/config'
import { formatUpdatedAt } from './entities'
import { describeEntityEditError } from './entityErrors'
import {
  KNOWN_UPSTREAM_TYPES,
  connectionConfigToText,
  isUpstreamChanged,
  parseConnectionConfig,
} from './upstreamEdit'

interface Props {
  modelValue: boolean
  upstreamName: string | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [payload: {
    name: string
    revision: number
    enabled: boolean
    connected: boolean
    changed: boolean
  }]
}>()

const knownTypes = KNOWN_UPSTREAM_TYPES
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const entity = ref<UpstreamEntity | null>(null)
const typeInput = ref('')
const configText = ref('{}')
const revision = ref(0)
const formError = ref('')
const formErrors = ref<string[]>([])
const conflictNotice = ref(false)
const parsedConfig = ref<Record<string, unknown> | null>(null)

const dialogTitle = computed(() =>
  props.upstreamName ? `编辑上游连接 ${props.upstreamName}` : '编辑上游连接',
)

const parseResult = computed(() => parseConnectionConfig(configText.value))

watch(parseResult, (r) => {
  parsedConfig.value = r.value ?? null
  formError.value = r.errors.length ? '连接块有错误, 请修正后再保存:' : ''
  formErrors.value = r.errors
}, { immediate: true })

const changed = computed(() => {
  if (!entity.value || !parsedConfig.value) return false
  return isUpstreamChanged(entity.value, parsedConfig.value, typeInput.value.trim())
})

const canSave = computed(() => changed.value && !!parsedConfig.value)

async function onOpen(): Promise<void> {
  await reloadEntity(false)
}

async function reloadEntity(noticeConflict: boolean): Promise<void> {
  if (!props.upstreamName) return
  loading.value = true
  loadError.value = ''
  formError.value = ''
  formErrors.value = []
  conflictNotice.value = noticeConflict
  try {
    const detail = await getUpstreamEntity(props.upstreamName)
    if (detail.source === 'yaml') {
      loadError.value = '网关以 YAML 逃生模式启动, 配置库只读, 不可编辑'
      entity.value = null
      return
    }
    entity.value = detail.upstream
    revision.value = detail.revision
    typeInput.value = detail.upstream.type
    configText.value = connectionConfigToText(detail.upstream.config || {})
  } catch (e: unknown) {
    loadError.value = describeEntityEditError(e).message
    entity.value = null
  } finally {
    loading.value = false
  }
}

async function onSave(): Promise<void> {
  if (!entity.value || !parsedConfig.value || saving.value) return
  if (!changed.value) {
    ElMessage.info('表单与实体一致, 无变更')
    return
  }
  const name = props.upstreamName as string
  const enabled = entity.value.enabled
  try {
    await ElMessageBox.confirm(
      `确认整体替换上游「${name}」的连接块${
        typeInput.value.trim() !== entity.value.type
          ? `并将类型改为 ${typeInput.value.trim()}`
          : ''
      }?\n\n`
      + (enabled
        ? '该上游当前处于启用态: 保存后会立即断开旧连接并重连, 期间其 MCP 接口热重注册; 若新配置连不上, 接口仍发布但调用会失败 (与开机语义一致)。'
        : '该上游当前停用: 仅持久化, 下次启用时按新配置建连。'),
      '上游连接块变更强确认',
      {
        confirmButtonText: '确认保存并重连',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true,
      },
    )
  } catch {
    return
  }

  saving.value = true
  try {
    const result = await updateUpstreamConfigEntity(
      name,
      parsedConfig.value,
      {
        type: typeInput.value.trim(),
        confirmDangerous: true,
        revision: revision.value,
      },
    )
    ElMessage.success(
      result.changed
        ? result.enabled
          ? result.connected
            ? '连接块已保存, 上游重连成功'
            : '连接块已保存, 但重连未建立, 请检查配置'
          : '连接块已保存 (上游停用中, 启用时生效)'
        : '表单与实体一致, 无变更',
    )
    emit('saved', {
      name: result.name,
      revision: result.revision,
      enabled: result.enabled,
      connected: result.connected,
      changed: result.changed,
    })
    close()
  } catch (e: unknown) {
    await handleSaveError(e)
  } finally {
    saving.value = false
  }
}

async function handleSaveError(e: unknown): Promise<void> {
  const info = describeEntityEditError(e)
  if (info.kind === 'revision_conflict') {
    ElMessage.warning('配置版本已变更, 正在重新加载最新实体')
    await reloadEntity(true)
    return
  }
  if (info.kind === 'store_unavailable') {
    ElMessage.error('配置库不可用 (YAML 逃生模式), 无法热编辑')
    return
  }
  // invalid (明文密钥/保留键)、not_found、server、network 都展示后端原文
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
.ued-body {
  min-height: 200px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ued-alert {
  margin-bottom: 0;
}

.ued-error-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 0.82rem;
}

.ued-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 0.75rem;
  color: var(--cfg-text-secondary, var(--el-text-color-secondary));
}

.ued-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ued-label {
  font-size: 0.78rem;
  color: var(--el-text-color-regular);
}

.ued-type-select {
  width: 240px;
}

.ued-json :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  line-height: 1.5;
}
</style>
