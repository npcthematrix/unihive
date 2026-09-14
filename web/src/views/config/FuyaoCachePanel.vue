<template>
  <div class="fcp-panel">
    <div class="cfg-banner">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        按 endpoint 分组管理 FUYAO 缓存 TTL — <code class="fcp-key">default</code> 是该 endpoint 所有工具的默认 TTL;
        <code class="fcp-key">override</code> 可对单个工具单独覆盖 (留空则用 default)。提交后需重启网关生效。
      </span>
    </div>

    <!-- 空状态: 未配置过 (endpoints 完全为空) — 提示这是默认行为, 不是"全 0 = 全关" -->
    <el-alert
      v-if="isUnset"
      type="info"
      :closable="false"
      show-icon
      class="fcp-unset-alert"
      title="尚未配置 FUYAO 缓存 TTL — 当前所有 endpoint 走全局 cache.ttl (即上方的「缓存」标签页)。"
    />

    <!-- 加载失败 -->
    <el-alert
      v-if="loadError"
      type="error"
      :closable="false"
      show-icon
      :title="`加载失败: ${loadError}`"
    >
      <el-button size="small" @click="load">重试</el-button>
    </el-alert>

    <!-- 变更预览 (默认折叠) -->
    <details v-if="diffCount > 0" class="cfg-section fcp-diff">
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          变更预览 ({{ diffCount }} 处)
        </span>
      </summary>
      <div class="cfg-section-body fcp-diff-body">
        <ul class="fcp-diff-list">
          <li v-for="d in diffs" :key="d.path">
            <code class="fcp-diff-path">{{ d.path }}</code>
            <span class="fcp-diff-arrow" aria-hidden="true">→</span>
            <span class="fcp-diff-before">{{ d.beforeText }}</span>
            <span class="fcp-diff-arrow" aria-hidden="true">→</span>
            <span class="fcp-diff-after">{{ d.afterText }}</span>
          </li>
        </ul>
      </div>
    </details>

    <!-- 4 个 endpoint 折叠区 -->
    <div class="fcp-endpoints">
      <details
        v-for="ep in ENDPOINT_ORDER"
        :key="ep"
        class="cfg-section"
        :open="isEpOpen(ep)"
        @toggle="onEpToggle(ep, $event)"
      >
        <summary class="cfg-section-header">
          <span class="cfg-section-title">
            <span class="cfg-section-toggle">▾</span>
            {{ ENDPOINT_TITLES[ep] }}
            <span class="cfg-section-sub">
              · {{ overridesByEndpoint(ep).length }} 条 override · 当前 default
              <strong>{{ formatSeconds(working.defaults[ep]) }}</strong>
            </span>
          </span>
        </summary>

        <div class="cfg-section-body">
          <!-- default TTL 行 -->
          <div class="fcp-default-row">
            <label class="fcp-label">默认 TTL (秒)</label>
            <el-input-number
              v-model="working.defaults[ep]"
              :min="0"
              :step="60"
              :max="604800"
              controls-position="right"
              class="fcp-default-input"
            />
            <span class="fcp-default-preview">
              ≈ {{ formatSeconds(working.defaults[ep]) }}
            </span>
            <div class="fcp-presets" role="group" aria-label="预设 TTL">
              <button
                v-for="p in PRESETS"
                :key="p.value"
                type="button"
                class="fcp-preset"
                :class="{ active: working.defaults[ep] === p.value }"
                @click="working.defaults[ep] = p.value"
              >{{ p.label }}</button>
            </div>
          </div>

          <!-- override 列表 -->
          <div class="fcp-overrides">
            <div class="fcp-overrides-header">
              <span class="fcp-label">工具级 override</span>
              <el-button size="small" :icon="Plus" @click="openAddOverride(ep)">添加覆盖</el-button>
            </div>

            <el-table
              v-if="overridesByEndpoint(ep).length > 0"
              :data="overridesByEndpoint(ep)"
              class="cfg-table fcp-table"
              scrollbar-always-on
            >
              <el-table-column label="工具名" min-width="180">
                <template #default="{ row }">
                  <span class="tool-name">{{ row.tool }}</span>
                </template>
              </el-table-column>
              <el-table-column label="TTL (秒)" min-width="160">
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.seconds"
                    :min="0"
                    :step="60"
                    :max="604800"
                    controls-position="right"
                    size="small"
                  />
                  <span class="fcp-default-preview">
                    ≈ {{ formatSeconds(row.seconds) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="100" align="right">
                <template #default="{ row }">
                  <el-button
                    link
                    type="danger"
                    size="small"
                    @click="removeOverride(ep, row.tool)"
                  >删除覆盖</el-button>
                </template>
              </el-table-column>
            </el-table>

            <div v-else class="fcp-empty">
              暂未自定义工具级 TTL, 全部使用 endpoint 默认值。
            </div>
          </div>
        </div>
      </details>
    </div>

    <!-- 操作栏 -->
    <div class="fcp-actions">
      <el-button @click="resetDraft" :disabled="!hasChanges || saving">放弃修改</el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!hasChanges"
        @click="onSubmit"
      >保存配置</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FUYAO_ENDPOINT_KEYS, FUYAO_ENDPOINT_TITLES, saveFuyaoCache } from '@/api/config'
import type { FuyaoCacheConfig, FuyaoEndpointKey } from '@/api/config'
import { errorMessage } from '@/api/client'

interface Props {
  fuyaoCache?: FuyaoCacheConfig
  /** GatewayConfig.version — 乐观锁, 提交时回带 */
  configVersion: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'saved', payload: { fuyao_cache: NonNullable<FuyaoCacheConfig>; version: string }): void
}>()

const ENDPOINT_ORDER = FUYAO_ENDPOINT_KEYS
const ENDPOINT_TITLES = FUYAO_ENDPOINT_TITLES

/** 6 个 TTL 预设 — 与 CachePanel 风格一致 */
const PRESETS = [
  { label: '实时 (0)', value: 0 },
  { label: '1 分钟', value: 60 },
  { label: '5 分钟', value: 300 },
  { label: '1 小时', value: 3600 },
  { label: '1 天', value: 86400 },
  { label: '7 天', value: 604800 },
] as const

/** 内部编辑态 — default 按 endpoint 平铺; override 嵌套在 endpoints[ep].override */
interface OverrideRow {
  tool: string
  seconds: number
}
interface Draft {
  defaults: Record<FuyaoEndpointKey, number>
  overrides: Record<FuyaoEndpointKey, OverrideRow[]>
}

const loadError = ref('')
const saving = ref(false)

/** 服务端原值快照 — 用于 diff 计算 */
const snapshot = reactive<Draft>({
  defaults: { fuyao_ashare: 0, fuyao_index: 0, fuyao_meta: 0, fuyao_fund: 0 },
  overrides: { fuyao_ashare: [], fuyao_index: [], fuyao_meta: [], fuyao_fund: [] },
})
/** 用户当前编辑态 */
const working = reactive<Draft>({
  defaults: { fuyao_ashare: 0, fuyao_index: 0, fuyao_meta: 0, fuyao_fund: 0 },
  overrides: { fuyao_ashare: [], fuyao_index: [], fuyao_meta: [], fuyao_fund: [] },
})

function overridesByEndpoint(ep: FuyaoEndpointKey): OverrideRow[] {
  return working.overrides[ep]
}

/** 服务端从未配置过 (endpoints 完全为空) — 用于空状态 banner 提示。
 *  用 props.fuyaoCache 直接判断, 不走 hydrate 后的 snapshot (hydrate 会填 0 占位)。 */
const isUnset = computed(() => {
  const endpoints = props.fuyaoCache?.endpoints
  return !endpoints || Object.keys(endpoints).length === 0
})

const hasChanges = computed(() => diffCount.value > 0)
const diffCount = computed(() => diffs.value.length)

interface DiffEntry {
  path: string
  beforeText: string
  afterText: string
}

const diffs = computed<DiffEntry[]>(() => {
  const out: DiffEntry[] = []
  for (const ep of ENDPOINT_ORDER) {
    const beforeDef = snapshot.defaults[ep]
    const afterDef = working.defaults[ep]
    if (beforeDef !== afterDef) {
      out.push({
        path: `${ep}.default`,
        beforeText: `${beforeDef}s (${formatSeconds(beforeDef)})`,
        afterText: `${afterDef}s (${formatSeconds(afterDef)})`,
      })
    }
    const beforeOv = new Map(snapshot.overrides[ep].map((r) => [r.tool, r.seconds]))
    const afterOv = new Map(working.overrides[ep].map((r) => [r.tool, r.seconds]))
    const allTools = new Set([...beforeOv.keys(), ...afterOv.keys()])
    for (const tool of allTools) {
      const b = beforeOv.get(tool)
      const a = afterOv.get(tool)
      if (b === undefined && a !== undefined) {
        out.push({
          path: `${ep}.override.${tool}`,
          beforeText: '(未设置)',
          afterText: `${a}s (${formatSeconds(a)})`,
        })
      } else if (b !== undefined && a === undefined) {
        out.push({
          path: `${ep}.override.${tool}`,
          beforeText: `${b}s (${formatSeconds(b)})`,
          afterText: '(已删除, 回落 default)',
        })
      } else if (b !== undefined && a !== undefined && b !== a) {
        out.push({
          path: `${ep}.override.${tool}`,
          beforeText: `${b}s (${formatSeconds(b)})`,
          afterText: `${a}s (${formatSeconds(a)})`,
        })
      }
    }
  }
  return out
})

function formatSeconds(s: number): string {
  if (s === 0) return '实时 / 不缓存'
  if (s < 60) return `${s} 秒`
  if (s < 3600) {
    const m = s / 60
    return `${Number.isInteger(m) ? m : m.toFixed(1)} 分钟`
  }
  if (s < 86400) {
    const h = s / 3600
    return `${Number.isInteger(h) ? h : h.toFixed(1)} 小时`
  }
  const d = s / 86400
  return `${Number.isInteger(d) ? d : d.toFixed(1)} 天`
}

/** 把服务端 FuyaoCacheConfig 拍平成 Draft (default 平铺, override 数组化)。 */
function hydrateFromConfig(cfg: FuyaoCacheConfig | undefined, target: Draft) {
  const endpoints = cfg?.endpoints || {}
  for (const ep of ENDPOINT_ORDER) {
    target.defaults[ep] = Number(endpoints[ep]?.default ?? 0)
    const ov = endpoints[ep]?.override || {}
    target.overrides[ep] = Object.entries(ov)
      .map(([tool, seconds]) => ({ tool, seconds: Number(seconds) }))
      .sort((a, b) => a.tool.localeCompare(b.tool))
  }
}

function resetDraft() {
  hydrateFromConfig(props.fuyaoCache, working)
}

/** 初始化 + 监听 props 变化 (config 重新拉时刷新) */
function load() {
  loadError.value = ''
  try {
    hydrateFromConfig(props.fuyaoCache, snapshot)
    hydrateFromConfig(props.fuyaoCache, working)
  } catch (e) {
    loadError.value = errorMessage(e, '解析 FUYAO 缓存配置失败')
  }
}

watch(
  () => props.fuyaoCache,
  () => load(),
  { immediate: true, deep: true },
)

/** 各 endpoint 折叠状态: 默认全部展开, 用户可手动收起。
 *  修复 2.1: 4 个 endpoint 一次性全展开, panel 短, 一次性全看更友好。 */
const openSet = ref<Set<FuyaoEndpointKey>>(new Set(ENDPOINT_ORDER))

function isEpOpen(ep: FuyaoEndpointKey): boolean {
  return openSet.value.has(ep)
}

function onEpToggle(ep: FuyaoEndpointKey, e: Event) {
  const target = e.target as HTMLDetailsElement
  const next = new Set(openSet.value)
  if (target.open) next.add(ep)
  else next.delete(ep)
  openSet.value = next
}

function openAddOverride(ep: FuyaoEndpointKey) {
  const existing = new Set(overridesByEndpoint(ep).map((r) => r.tool))
  // 候选工具: 该 endpoint 已有 override + 当前 default 不为 0 时, 显示 endpoint 名称作为占位
  // 不强求拉上游 tool list (会增加额外 API), 允许任意名称输入 — 后端只校验类型
  ElMessageBox.prompt(
    `为 endpoint ${ep} 添加工具级 override\n输入 MCP tool 名称 (任意字符串)`,
    '添加覆盖',
    {
      confirmButtonText: '添加',
      cancelButtonText: '取消',
      inputPattern: /^\S+$/,
      inputErrorMessage: '工具名不能为空或包含空白',
    },
  )
    .then(({ value }) => {
      const tool = value.trim()
      if (existing.has(tool)) {
        ElMessage.warning(`override[${tool}] 已存在`)
        return
      }
      working.overrides[ep] = [
        ...working.overrides[ep],
        { tool, seconds: working.defaults[ep] },
      ].sort((a, b) => a.tool.localeCompare(b.tool))
    })
    .catch(() => {
      /* 用户取消 — 静默 */
    })
}

function removeOverride(ep: FuyaoEndpointKey, tool: string) {
  working.overrides[ep] = working.overrides[ep].filter((r) => r.tool !== tool)
}

function buildPayload(): FuyaoCacheConfig {
  const endpoints: NonNullable<FuyaoCacheConfig['endpoints']> = {}
  for (const ep of ENDPOINT_ORDER) {
    const override: Record<string, number> = {}
    for (const row of working.overrides[ep]) {
      override[row.tool] = row.seconds
    }
    endpoints[ep] = { default: working.defaults[ep], override }
  }
  return { endpoints }
}

async function onSubmit() {
  if (!hasChanges.value) return
  saving.value = true
  try {
    const result = await saveFuyaoCache(buildPayload(), props.configVersion || '')
    // 用服务端回值覆盖 snapshot, 让 diff 归零; 同时通过 emit 让父级更新 config.version
    snapshot.defaults = { ...working.defaults }
    snapshot.overrides = {
      fuyao_ashare: [...working.overrides.fuyao_ashare],
      fuyao_index: [...working.overrides.fuyao_index],
      fuyao_meta: [...working.overrides.fuyao_meta],
      fuyao_fund: [...working.overrides.fuyao_fund],
    }
    emit('saved', { fuyao_cache: result.fuyao_cache, version: result.version })
    ElMessage.success('FUYAO 缓存配置已保存, 请重启网关生效')
  } catch (e: unknown) {
    // 409 version_conflict 由 axios 拦截器变成普通 Error, 后端 message 在 errorMessage 里
    const msg = errorMessage(e, '保存失败')
    if (msg.includes('version_conflict') || msg.includes('版本不一致')) {
      ElMessageBox.alert(
        '服务端配置已变更, 请刷新页面后重试',
        '版本冲突',
        { confirmButtonText: '我知道了', type: 'warning' },
      )
    } else {
      ElMessage.error(msg)
    }
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.fcp-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.fcp-default-row {
  display: grid;
  /* 修复 2.2: grid 让 label/input/preview 在窄屏仍保持固定列, 预设按钮自动换行 */
  grid-template-columns: auto auto auto 1fr;
  align-items: center;
  gap: 10px 12px;
  margin-bottom: 12px;
}

@media (max-width: 900px) {
  .fcp-default-row {
    grid-template-columns: auto auto 1fr;
  }
  .fcp-presets {
    grid-column: 1 / -1;
    margin-left: 0;
    justify-content: flex-start;
  }
}

.fcp-label {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--cfg-text-secondary);
  min-width: 96px;
}

.fcp-default-input {
  width: 140px;
}

.fcp-default-preview {
  font-size: 0.78rem;
  color: var(--cfg-text-secondary);
}

.fcp-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  /* grid 模式下不需要 margin-left: auto */
  min-width: 0;
}

.fcp-preset {
  appearance: none;
  -webkit-appearance: none;
  background: var(--cfg-bg-soft);
  border: 1px solid var(--cfg-border);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 0.72rem;
  font-family: inherit;
  color: var(--el-text-color-regular);
  cursor: pointer;
  transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
}

.fcp-preset:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

.fcp-preset:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}

.fcp-preset.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  font-weight: 600;
}

.fcp-overrides {
  border-top: 1px dashed var(--cfg-border);
  padding-top: 10px;
}

.fcp-overrides-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.fcp-empty {
  padding: 12px 14px;
  background: var(--cfg-bg-soft);
  border: 1px dashed var(--cfg-border);
  border-radius: 4px;
  font-size: 0.82rem;
  color: var(--cfg-text-secondary);
  text-align: center;
}

.fcp-table :deep(.el-input-number) {
  width: 120px;
}

/* 修复 2.3: 表格外层加 max-width + 横向滚动, 避免长工具名挤压窄屏 */
.fcp-table {
  max-width: 100%;
  overflow-x: auto;
}

.fcp-overrides {
  /* 也给外层一个滚动保护 */
  max-width: 100%;
}

.fcp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.fcp-diff {
  border-color: var(--el-color-warning-light-7);
}

.fcp-diff-body {
  max-height: 240px;
  overflow: auto;
}

.fcp-diff-list {
  margin: 0;
  padding: 0;
  list-style: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  line-height: 1.7;
}

.fcp-diff-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
}

.fcp-diff-path {
  background: var(--cfg-bg-soft);
  padding: 1px 6px;
  border-radius: 3px;
}

.fcp-diff-arrow {
  color: var(--cfg-text-secondary);
}

.fcp-diff-before {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
  padding: 1px 6px;
  border-radius: 3px;
}

.fcp-diff-after {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
  padding: 1px 6px;
  border-radius: 3px;
}

/* banner 中 default / override 关键词: primary 色 + 等宽字体, 跟上下行内 code 区分 */
.fcp-key {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 0 4px;
  border-radius: 3px;
}

.fcp-unset-alert {
  margin-top: -4px; /* 紧贴 banner */
}
</style>
