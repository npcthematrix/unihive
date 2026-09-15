<template>
  <div class="fcp-panel">
    <!-- 顶部说明 banner -->
    <div class="cfg-banner fcp-info">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        按 endpoint 分组列出全部 FUYAO 工具 — 在每行的
        <code class="fcp-key">TTL</code>
        处直接设置秒数 (留空 = 走全局
        <code class="fcp-key">cache.ttl[cache_ttl_key]</code>)。
        提交后需重启网关生效。
      </span>
    </div>

    <!-- 空状态横幅: 仅 isUnset 时显示 -->
    <el-alert
      v-if="isUnset"
      type="info"
      :closable="false"
      show-icon
      class="fcp-unset-alert"
      title="尚未配置 FUYAO 缓存 TTL"
      description="当前所有 FUYAO 工具走全局 cache.ttl (即上方的「缓存」标签页)。直接在每行 TTL 输入框填入秒数即可覆盖单个工具。"
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

    <!-- 全局搜索 + 计数 -->
    <div class="fcp-toolbar">
      <el-input
        v-model="searchQuery"
        :prefix-icon="Search"
        clearable
        placeholder="搜索工具名或简述 (跨 4 个 endpoint 过滤, 如 fund / 龙虎榜)"
        class="fcp-search"
        autocomplete="off"
      />
      <span class="fcp-toolbar-count">
        {{ overrideCount }} 条 override · 共 {{ totalTools }} 个工具
      </span>
    </div>

    <!-- 4 endpoint 折叠 -->
    <div class="fcp-endpoints">
      <details
        v-for="ep in ENDPOINT_ORDER"
        :key="ep"
        class="cfg-section"
        :open="isEpOpen(ep)"
        @toggle="onEpToggle(ep, $event)"
      >
        <summary class="cfg-section-header">
          <span class="cfg-section-toggle">▸</span>
          <span class="cfg-section-title">{{ ENDPOINT_TITLES[ep] }}</span>
          <span class="cfg-section-sub">
            · {{ toolsByEp(ep).length }} 个工具 /
            {{ overridesCountByEp(ep) }} 条 override
          </span>
        </summary>
        <div class="cfg-section-body">
          <!-- 批量设置行 -->
          <div class="fcp-bulk-row">
            <span class="fcp-label">批量设置</span>
            <span class="fcp-presets" role="group" :aria-label="`批量设置 ${ENDPOINT_TITLES[ep]} TTL`">
              <button
                v-for="p in PRESETS"
                :key="p.value"
                type="button"
                class="fcp-preset"
                :title="`为该 endpoint 所有工具设置 ${p.label}`"
                @click="bulkSetEp(ep, p.value)"
              >{{ p.label }}</button>
              <button
                type="button"
                class="fcp-preset fcp-preset--danger"
                title="清空该 endpoint 所有 override"
                @click="bulkClearEp(ep)"
              >清空全部</button>
            </span>
          </div>

          <!-- 全量工具表格 -->
          <el-table
            v-if="filteredToolsByEp(ep).length > 0"
            :data="filteredToolsByEp(ep)"
            class="cfg-table fcp-table"
            scrollbar-always-on
            row-key="name"
            :empty-text="searchQuery ? '无匹配工具' : '该 endpoint 下未检测到 FUYAO 工具'"
          >
            <el-table-column label="工具名" min-width="220">
              <template #default="{ row }">
                <span class="tool-name">{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="简述" min-width="200">
              <template #default="{ row }">
                <span class="tool-desc">{{ row.description || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="TTL (秒)" min-width="170">
              <template #default="{ row }">
                <el-input-number
                  :model-value="working[row.name]"
                  :min="0"
                  :step="60"
                  :max="604800"
                  controls-position="right"
                  size="small"
                  placeholder="(走全局)"
                  @update:model-value="(v: number | undefined) => setOverride(row.name, v)"
                />
              </template>
            </el-table-column>
            <el-table-column label="状态" width="180">
              <template #default="{ row }">
                <span v-if="working[row.name] !== undefined" class="fcp-status fcp-status--set">
                  ✓ 已覆盖 · {{ formatSeconds(working[row.name]!) }}
                </span>
                <span v-else class="fcp-status fcp-status--unset">
                  走全局 cache.ttl
                </span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="danger"
                  size="small"
                  :disabled="working[row.name] === undefined"
                  @click="removeOverride(row.name)"
                >清空</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-else class="fcp-empty">
            {{ searchQuery
              ? '该 endpoint 下无匹配工具'
              : '该 endpoint 下未检测到 FUYAO 工具, 请检查上游配置' }}
          </div>
        </div>
      </details>
    </div>

    <!-- 变更预览 -->
    <details v-if="diffCount > 0" class="cfg-section fcp-diff" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-toggle">▸</span>
        <span class="cfg-section-title">变更预览</span>
        <span class="cfg-section-sub">· {{ diffCount }} 处改动</span>
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

    <!-- 底部 actions -->
    <div class="fcp-actions">
      <el-button :disabled="!hasChanges || saving" @click="resetDraft">放弃修改</el-button>
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
import { Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  FUYAO_ENDPOINT_KEYS,
  FUYAO_ENDPOINT_TITLES,
  fuyaoEndpointOf,
  saveFuyaoCache,
} from '@/api/config'
import type { ConfigTool, FuyaoCacheConfig, FuyaoEndpointKey } from '@/api/config'
import { errorMessage } from '@/api/client'

interface Props {
  fuyaoCache?: FuyaoCacheConfig
  /** GatewayConfig.version — 乐观锁, 提交时回带 */
  configVersion: string
  /** 全部 gateway 工具列表 (来自 config.tools), 用于派生 FUYAO 工具及描述。 */
  allTools?: ConfigTool[]
  /** 工具路由映射 (来自 config.upstream_tool_mapping), 用于判断工具所属 FUYAO 端点。 */
  upstreamToolMapping?: Record<string, Record<string, string>>
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

const loadError = ref('')
const saving = ref(false)
const searchQuery = ref('')
/** 默认全展开, 用户可手动收/展。 */
const openSet = ref<Set<FuyaoEndpointKey>>(new Set(ENDPOINT_ORDER))

type ToolsMap = Record<string, number>

const snapshot = reactive<ToolsMap>({})
const working = reactive<ToolsMap>({})

/** 派生: 按 endpoint 分组的 FUYAO 工具列表 (含描述)。每次 props 变化重算。 */
interface FuyaoToolRow {
  name: string
  description: string
}

function toolsByEp(ep: FuyaoEndpointKey): FuyaoToolRow[] {
  const out: FuyaoToolRow[] = []
  for (const tool of props.allTools || []) {
    const tEp = fuyaoEndpointOf(tool.name, props.upstreamToolMapping)
    if (tEp !== ep) continue
    out.push({ name: tool.name, description: tool.description || '' })
  }
  out.sort((a, b) => a.name.localeCompare(b.name))
  return out
}

/** 受搜索过滤后的同节工具列表。 */
function filteredToolsByEp(ep: FuyaoEndpointKey): FuyaoToolRow[] {
  const q = searchQuery.value.trim().toLowerCase()
  const rows = toolsByEp(ep)
  if (!q) return rows
  return rows.filter(
    (r) =>
      r.name.toLowerCase().includes(q) ||
      (r.description && r.description.toLowerCase().includes(q)),
  )
}

function overridesCountByEp(ep: FuyaoEndpointKey): number {
  return toolsByEp(ep).filter((r) => Object.prototype.hasOwnProperty.call(working, r.name)).length
}

const overrideCount = computed(() => Object.keys(working).length)
const totalTools = computed(() =>
  ENDPOINT_ORDER.reduce((n, ep) => n + toolsByEp(ep).length, 0),
)

/** 服务端从未配置过 (tools 完全为空) — 用于空状态 banner 提示。 */
const isUnset = computed(() => {
  const tools = props.fuyaoCache?.tools
  return !tools || Object.keys(tools).length === 0
})

const hasChanges = computed(() => diffCount.value > 0)
const diffCount = computed(() => diffs.value.length)

interface DiffEntry {
  path: string
  beforeText: string
  afterText: string
}

const diffs = computed<DiffEntry[]>(() => {
  const all = new Set<string>([...Object.keys(snapshot), ...Object.keys(working)])
  const out: DiffEntry[] = []
  for (const tool of all) {
    const before = snapshot[tool]
    const after = working[tool]
    if (before === undefined && after !== undefined) {
      out.push({
        path: `tools.${tool}`,
        beforeText: '(未设置, 走全局)',
        afterText: `${after}s (${formatSeconds(after)})`,
      })
    } else if (before !== undefined && after === undefined) {
      out.push({
        path: `tools.${tool}`,
        beforeText: `${before}s (${formatSeconds(before)})`,
        afterText: '(已清空, 回落全局 cache.ttl)',
      })
    } else if (before !== undefined && after !== undefined && before !== after) {
      out.push({
        path: `tools.${tool}`,
        beforeText: `${before}s (${formatSeconds(before)})`,
        afterText: `${after}s (${formatSeconds(after)})`,
      })
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

/** 把服务端 FuyaoCacheConfig 拍平到 reactive dict (snapshot/working 复用同一结构)。 */
function hydrateFromConfig(cfg: FuyaoCacheConfig | undefined, target: ToolsMap) {
  for (const k of Object.keys(target)) delete target[k]
  const tools = cfg?.tools || {}
  for (const [k, v] of Object.entries(tools)) {
    if (typeof v === 'number' && v >= 0) target[k] = v
  }
}

function resetDraft() {
  hydrateFromConfig(props.fuyaoCache, working)
}

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
  () => [props.fuyaoCache, props.allTools, props.upstreamToolMapping] as const,
  () => load(),
  { immediate: true, deep: true },
)

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

/** 单工具设置: undefined / null / NaN / 负值 = 清空 override (回落全局)。
 *  有限非负数 = 设值 (整数化)。 */
function setOverride(tool: string, seconds: number | undefined | null) {
  if (seconds === undefined || seconds === null || Number.isNaN(seconds)) {
    delete working[tool]
    return
  }
  if (!Number.isFinite(seconds) || seconds < 0) {
    delete working[tool]
    return
  }
  working[tool] = Math.floor(seconds)
}

function removeOverride(tool: string) {
  delete working[tool]
}

/** 批量把 endpoint 内全部工具设为同一 TTL (含已配置的, 强制覆盖)。 */
function bulkSetEp(ep: FuyaoEndpointKey, seconds: number) {
  if (seconds < 0 || !Number.isFinite(seconds)) return
  for (const row of toolsByEp(ep)) {
    working[row.name] = Math.floor(seconds)
  }
}

function bulkClearEp(ep: FuyaoEndpointKey) {
  for (const row of toolsByEp(ep)) {
    delete working[row.name]
  }
}

function buildPayload(): FuyaoCacheConfig {
  return { tools: { ...working } }
}

async function onSubmit() {
  if (!hasChanges.value || saving.value) return
  saving.value = true
  try {
    const result = await saveFuyaoCache(buildPayload(), props.configVersion || '')
    for (const k of Object.keys(snapshot)) delete snapshot[k]
    for (const [k, v] of Object.entries(result.fuyao_cache.tools || {})) {
      snapshot[k] = v
    }
    emit('saved', { fuyao_cache: result.fuyao_cache, version: result.version })
    ElMessage.success('FUYAO 缓存配置已保存, 请重启网关生效')
  } catch (e: unknown) {
    const msg = errorMessage(e, '保存失败')
    if (msg.includes('version_conflict') || msg.includes('版本不一致')) {
      try {
        await ElMessageBox.confirm(
          '服务端配置已变更, 你的本地编辑基于旧版本。继续保存将覆盖他人改动。\n\n点击「刷新页面」加载最新版本后重新编辑。',
          '版本冲突',
          {
            confirmButtonText: '刷新页面',
            cancelButtonText: '继续编辑',
            type: 'warning',
            distinguishCancelAndClose: true,
          },
        )
        window.location.reload()
      } catch {
        // 用户选择「继续编辑」或关闭弹窗 — 保留当前 UI, 提示用户稍后手动刷新
        void ElMessageBox.alert(
          '你的本地编辑与服务端不同步, 重新加载页面前请勿保存。',
          '提示',
          { confirmButtonText: '我知道了', type: 'info' },
        )
      }
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
  margin-top: -4px;
}

/* toolbar */
.fcp-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.fcp-search {
  flex: 1 1 320px;
  max-width: 480px;
}
.fcp-toolbar-count {
  font-size: 0.82rem;
  color: var(--cfg-text-secondary);
  white-space: nowrap;
}

/* 4 endpoint 折叠 */
.fcp-endpoints {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.fcp-label {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--cfg-text-secondary);
  min-width: 96px;
}

/* 批量设置行 */
.fcp-bulk-row {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 8px 12px;
  align-items: center;
  padding: 6px 0 8px;
  border-bottom: 1px dashed var(--cfg-border);
  margin-bottom: 8px;
}

/* 预设按钮组 */
.fcp-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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
.fcp-preset--danger:hover {
  border-color: var(--el-color-danger);
  color: var(--el-color-danger);
}

/* 全量工具表格 */
.fcp-table :deep(.el-input-number) {
  width: 130px;
}

.tool-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
}

.tool-desc {
  color: var(--cfg-text-secondary);
  font-size: 0.82rem;
  line-height: 1.4;
}

/* 状态 badge */
.fcp-status {
  display: inline-flex;
  align-items: center;
  font-size: 0.78rem;
  padding: 1px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.fcp-status--set {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
  font-weight: 500;
}
.fcp-status--unset {
  color: var(--cfg-text-secondary);
  background: var(--cfg-bg-soft);
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

/* 响应式 */
@media (max-width: 900px) {
  .fcp-bulk-row {
    grid-template-columns: 1fr;
  }
  .fcp-presets {
    grid-column: 1 / -1;
  }
}

/* 640px 中间断点: 极窄屏进一步压缩 tool row (label 与 TTL 输入框 stack) */
@media (max-width: 640px) {
  .fcp-tool-row {
    flex-direction: column;
    align-items: stretch;
    gap: 6px;
  }
  .fcp-tool-row .fcp-label {
    min-width: 0;
  }
}

/* diff 预览 */
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

/* 底部 actions */
.fcp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
