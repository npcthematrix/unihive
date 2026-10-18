<template>
  <div class="proxy-panel">
    <p class="proxy-description">
      mootdx2 → TDX_QUANT 透明代理。启用后, 以下 <strong>{{ coverageCount }}</strong> 个行情类
      工具优先走 TDX_QUANT (本地 DLL, 更稳定), 失败时自动降级到 MOOTDX2 (远程 TDX)。
    </p>

    <!-- TDX_QUANT 实时状态 -->
    <div class="proxy-status">
      <span class="proxy-status-label">TDX_QUANT 状态:</span>
      <span
        class="proxy-status-value"
        :class="proxy?.tdx_quant_status || 'UNKNOWN'"
        role="status"
        :aria-label="`TDX_QUANT 状态: ${proxy?.tdx_quant_status || '未知'}${statusHint ? ` — ${statusHint}` : ''}`"
      >{{ proxy?.tdx_quant_status || '—' }}</span>
      <span class="proxy-status-hint">{{ statusHint }}</span>
    </div>

    <!-- 启用开关 + 保存反馈 -->
    <div class="proxy-toggle-row">
      <!-- 不用 inline-prompt: 文案在轨道内会被开关宽度裁掉;
           active/inactive-text 在轨道右侧渲染, 始终完整。 -->
      <el-switch
        v-model="enabled"
        :disabled="toggleDisabled || saving"
        :loading="saving"
        active-text="TDX_QUANT 代理已启用"
        inactive-text="TDX_QUANT 代理已停用"
        @change="onToggle"
      />
      <span
        class="proxy-save-status"
        :class="{ failed: saveFailed }"
        aria-live="polite"
      >{{ saveStatusText }}</span>
    </div>

    <!-- 覆盖范围 -->
    <div class="proxy-coverage">
      <h4 class="proxy-section-title">覆盖范围</h4>
      <p class="proxy-section-hint">以下工具在代理启用时优先走 TDX_QUANT</p>
      <div class="proxy-coverage-table-scroll">
        <el-table :data="coverage" class="proxy-coverage-table" :empty-text="coverageEmpty">
          <el-table-column label="MOOTDX2 工具" min-width="200">
            <template #default="{ row }">
              <code>{{ row.mootdx2_tool }}</code>
            </template>
          </el-table-column>
          <el-table-column width="40" align="center">
            <template #default>
              <span class="proxy-arrow" aria-hidden="true">→</span>
            </template>
          </el-table-column>
          <el-table-column label="TDX_QUANT 工具" min-width="200">
            <template #default="{ row }">
              <code>{{ row.tdx_quant_tool }}</code>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 未代理的行情类工具 (静态说明, 无 JS 数据)
         注意: 这份清单是前端硬编码的, 后端 TOOL_MAP / 代理覆盖范围变更时
         不会自动同步。改动代理范围时请一并更新此处。 -->
    <div class="proxy-section-non-proxied">
      <h4 class="proxy-section-title">未代理的 mootdx2 行情类工具</h4>
      <p class="proxy-section-hint">以下工具属于行情类但不在代理范围内, 继续走原 mootdx2 链路</p>
      <ul>
        <li><code>get_k_data</code> / <code>get_minutes</code> / <code>get_fzline_local</code> — 其他 K 线/分时入口</li>
        <li><code>get_minute_local</code> / <code>get_daily_local</code> — 本地缓存路径 (走代理会绕过缓存, 故排除)</li>
        <li><code>get_index_overview</code> — 6 大指数概览 (聚合类, tdx_quant 需循环调用)</li>
        <li><code>get_trade</code> / <code>get_trade_history</code> — 逐笔成交 (tdx_quant 无对应能力)</li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { setProxySettings } from '@/api/config'
import type { ProxyCoverageEntry, ProxySettings, TdxQuantStatus } from '@/api/config'
import { errorMessage } from '@/api/client'

interface Props {
  proxy: ProxySettings | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'change', updated: ProxySettings): void
}>()

const enabled = ref(Boolean(props.proxy?.enabled))
const saving = ref(false)
const saveStatusText = ref('')
const saveFailed = ref(false)

/** 旧 proxy tab 在设置变更后保持 enabled 与服务端同步 */
watch(
  () => props.proxy?.enabled,
  (v) => {
    enabled.value = Boolean(v)
  },
)

const coverage = computed<ProxyCoverageEntry[]>(() => props.proxy?.coverage || [])
const coverageCount = computed(() => coverage.value.length)
const coverageEmpty = computed(() =>
  coverage.value.length ? '' : '覆盖范围为空 — TOOL_MAP 未配置或加载失败',
)

const OFFLINE_STATUSES: TdxQuantStatus[] = ['UNAVAILABLE', 'MISSING']
const toggleDisabled = computed(() =>
  OFFLINE_STATUSES.includes(props.proxy?.tdx_quant_status || 'UNKNOWN'),
)

const STATUS_HINTS: Record<TdxQuantStatus, string> = {
  ONLINE: '在线 — 代理可用',
  DEGRADED: '降级 — 部分异常, 仍可走代理',
  OFFLINE: '离线 — 不可用',
  UNAVAILABLE: '不可用 — 当前状态不允许启用',
  DISABLED: '未启用 — 启动期被禁用',
  MISSING: '未配置 — 缺少 tdx_quant 上游',
  FAILED: '探活失败',
  LOADING: '加载中…',
  UNKNOWN: '未知状态',
}

const statusHint = computed(
  () => STATUS_HINTS[props.proxy?.tdx_quant_status || 'UNKNOWN'] || '',
)

async function onToggle(value: boolean | string | number) {
  // 防止快速连点 (保存中) 触发并发请求
  if (saving.value) return
  const next = Boolean(value)
  saving.value = true
  saveFailed.value = false
  saveStatusText.value = '保存中…'
  try {
    const updated = await setProxySettings(next)
    emit('change', updated)
    saveStatusText.value = '已保存'
    ElMessage.success(`代理已${next ? '启用' : '关闭'}`)
  } catch (e) {
    saveFailed.value = true
    const msg = errorMessage(e, '保存失败')
    saveStatusText.value = `保存失败: ${msg}`
    // 不再 ElMessage.error: saveStatusText 在面板头部持续显示, 比 toast 更醒目且不刷屏
    // 回滚
    enabled.value = !next
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.proxy-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.proxy-description {
  margin: 0;
  font-size: 0.85rem;
  color: var(--cfg-text-secondary);
  line-height: 1.5;
}

.proxy-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: var(--cfg-bg-soft);
  border: 1px solid var(--cfg-border);
  border-radius: 6px;
}

.proxy-status-label {
  font-size: 0.85rem;
  color: var(--cfg-text-secondary);
}

.proxy-status-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
  font-size: 0.85rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--cfg-bg);
  border: 1px solid var(--cfg-border);
  color: var(--el-text-color-regular);
}

.proxy-status-value.ONLINE {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-7);
}

.proxy-status-value.DEGRADED {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-7);
}

.proxy-status-value.OFFLINE,
.proxy-status-value.UNAVAILABLE,
.proxy-status-value.MISSING,
.proxy-status-value.FAILED {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
  border-color: var(--el-color-danger-light-7);
}

.proxy-status-value.DISABLED,
.proxy-status-value.LOADING,
.proxy-status-value.UNKNOWN {
  color: var(--cfg-text-secondary);
}

.proxy-status-hint {
  font-size: 0.8rem;
  color: var(--cfg-text-secondary);
  margin-left: 4px;
}

.proxy-toggle-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 14px;
  border: 1px solid var(--cfg-border);
  border-radius: 6px;
}

.proxy-save-status {
  font-size: 0.8rem;
  color: var(--cfg-text-secondary);
}

.proxy-save-status.failed {
  color: var(--el-color-danger);
}

.proxy-coverage {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.proxy-section-title {
  margin: 4px 0 0;
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.proxy-section-hint {
  margin: 0 0 6px;
  font-size: 0.8rem;
  color: var(--cfg-text-secondary);
}

.proxy-coverage-table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.proxy-coverage-table code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
}

.proxy-arrow {
  color: var(--cfg-text-secondary);
}

.proxy-section-non-proxied {
  padding: 12px 14px;
  background: var(--cfg-bg-soft);
  border: 1px solid var(--cfg-border);
  border-radius: 6px;
}

.proxy-section-non-proxied ul {
  margin: 0;
  padding-left: 18px;
  font-size: 0.82rem;
  line-height: 1.6;
}

.proxy-section-non-proxied code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  background: var(--cfg-bg);
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid var(--cfg-border);
}
</style>
