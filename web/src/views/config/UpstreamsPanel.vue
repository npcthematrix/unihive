<template>
  <div class="ups-panel">
    <!-- 搜索框 -->
    <div class="cfg-search-wrap" :class="{ 'has-value': search }">
      <input
        v-model="search"
        type="text"
        class="cfg-search"
        placeholder="搜索上游... (名称/类型/URL)"
        aria-label="搜索上游"
      >
      <button
        v-if="search"
        type="button"
        class="cfg-search-clear"
        title="清空"
        @click="search = ''"
      >×</button>
    </div>

    <!-- 上游表 -->
    <div class="ups-table-scroll">
    <el-table
      :data="filteredEntries"
      :empty-text="emptyText"
      class="cfg-table ups-table"
      row-key="name"
    >
      <el-table-column label="名称" min-width="140">
        <template #default="{ row }">
          <div class="ups-name-cell">
            <button
              v-if="row.hasDetail"
              type="button"
              class="ups-expand-btn"
              :class="{ expanded: expandedNames.has(row.name) }"
              :aria-expanded="expandedNames.has(row.name)"
              :aria-label="expandedNames.has(row.name) ? '收起详情' : '展开详情'"
              @click="toggleExpand(row.name)"
            >▸</button>
            <span class="tool-name">{{ row.name }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.cfg.type" size="small" effect="plain" round>
            {{ row.cfg.type }}
          </el-tag>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <!-- min-width 而非固定 width: loading 态按钮多出 spinner 宽度,
           固定 120px 会把"禁用"二字裁掉 ~5-19px。 -->
      <el-table-column label="状态" min-width="140">
        <template #default="{ row }">
          <div class="ups-status-cell">
            <span
              class="cfg-switch"
              :class="isEnabled(row.cfg) ? 'on' : 'off'"
              :title="isEnabled(row.cfg) ? '已启用' : '已停用'"
            >{{ isEnabled(row.cfg) ? '启用' : '停用' }}</span>
            <el-button
              size="small"
              :type="isEnabled(row.cfg) ? 'danger' : 'success'"
              plain
              :loading="toggling.has(row.name)"
              :aria-label="`${toggleLabel(isEnabled(row.cfg))}上游 ${row.name}`"
              @click="onToggle(row)"
            >{{ toggleLabel(isEnabled(row.cfg)) }}</el-button>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="Endpoint" min-width="200">
        <template #default="{ row }">
          <code v-if="row.cfg.base_url" class="ups-endpoint">{{ row.cfg.base_url }}</code>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="超时" width="70" align="right" header-align="right">
        <template #default="{ row }">
          <span class="num">{{ row.cfg.timeout_seconds ?? '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="API Key" width="170">
        <template #default="{ row }">
          <el-tooltip
            v-if="row.cfg.api_key"
            content="服务端已脱敏 (前 2 + *** + 后 2 字符) — 原始值在 .env 中"
            placement="top"
          >
            <code class="cfg-mask">{{ row.cfg.api_key }}</code>
          </el-tooltip>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column type="expand" v-if="anyExpandable">
        <template #default="{ row }">
          <div v-if="envEntries(row.cfg).length" class="ups-detail-block">
            <div class="ups-detail-label">环境变量 (env)</div>
            <el-table :data="envEntries(row.cfg)" class="cfg-table ups-env-table">
              <el-table-column label="变量" width="160">
                <template #default="{ row: envRow }">
                  <span class="tool-name">{{ envRow.key }}</span>
                </template>
              </el-table-column>
              <el-table-column label="值">
                <template #default="{ row: envRow }">
                  <el-tooltip
                    v-if="looksMasked(envRow.value)"
                    content="服务端已脱敏 (前 2 + *** + 后 2 字符)"
                    placement="top"
                  >
                    <code class="cfg-mask">{{ envRow.value }}</code>
                  </el-tooltip>
                  <span v-else>{{ envRow.value }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div v-if="row.cfg.retry" class="ups-detail-block">
            <div class="ups-detail-label">retry</div>
            <pre class="ups-retry">{{ retryToString(row.cfg.retry) }}</pre>
          </div>
        </template>
      </el-table-column>
    </el-table>
    </div>

    <!-- 全局开关 + 日志: 两个独立折叠区 -->
    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          全局开关
          <span class="cfg-section-sub">— disable_dangerous / strict_registry</span>
        </span>
      </summary>
      <div class="cfg-section-body">
        <div class="cfg-toggles-row">
          <span
            class="cfg-switch"
            :class="config?.disable_dangerous ? 'on' : 'off'"
            :title="`启用后跳过所有 dangerous: true 的 tool (只读 — 修改 YAML 后重启生效)`"
          >disable_dangerous</span>
          <span
            class="cfg-switch"
            :class="config?.strict_registry ? 'on' : 'off'"
            :title="`启用后启动校验 YAML 工具名都在上游 tools/list 中实际出现 (只读 — 修改 YAML 后重启生效)`"
          >strict_registry</span>
        </div>
      </div>
    </details>

    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          日志配置
        </span>
      </summary>
      <div class="cfg-section-body">
        <div v-if="logRows.length" class="cfg-kv-grid">
          <template v-for="row in logRows" :key="row.key">
            <div class="cfg-kv-key">{{ row.key }}</div>
            <div class="cfg-kv-val">{{ row.display }}</div>
          </template>
        </div>
        <div v-else class="muted">无日志配置</div>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { GatewayConfig, UpstreamConfig } from '@/api/config'
import { setUpstreamEnabled } from '@/api/config'
import { errorMessage } from '@/api/client'
import { loggingToRows, retryToString } from './format'
import {
  isUpstreamEnabled as isEnabled,
  toggleLabel,
  type UpstreamEnabledChanged,
} from './upstreams'
import { isUsableRevision } from './revisions'

interface Props {
  config: GatewayConfig | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  changed: [payload: UpstreamEnabledChanged]
}>()

const search = ref('')

/** 行级热开关 in-flight 集合 (不可变替换触发 el-button loading 重渲染)。 */
const toggling = ref<Set<string>>(new Set())

interface UpstreamRow {
  name: string
  cfg: UpstreamConfig
  hasDetail: boolean
}

/** 把 upstreams 字典转成数组, 保持 Object.entries 顺序 (旧版等价)。 */
const upstreamEntries = computed<UpstreamRow[]>(() => {
  const ups = props.config?.upstreams || {}
  return Object.entries(ups).map(([name, cfg]) => ({
    name,
    cfg: cfg || {},
    hasDetail: Boolean((cfg?.env && Object.keys(cfg.env).length) || cfg?.retry),
  }))
})

const totalUpstreams = computed(() => upstreamEntries.value.length)

const filteredEntries = computed<UpstreamRow[]>(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return upstreamEntries.value
  return upstreamEntries.value.filter(({ name, cfg }) => {
    if (name.toLowerCase().includes(q)) return true
    if ((cfg.type || '').toLowerCase().includes(q)) return true
    if ((cfg.base_url || '').toLowerCase().includes(q)) return true
    if ((cfg.description || '').toLowerCase().includes(q)) return true
    return false
  })
})

const anyExpandable = computed(() => upstreamEntries.value.some((r) => r.hasDetail))

const emptyText = computed(() => {
  if (!totalUpstreams.value) return '暂无上游配置'
  return `无匹配项 (${filteredEntries.value.length} / ${totalUpstreams.value})`
})

/** 记录已展开的 upstream name (Vue 自带 expand 列会自管, 此 ref 仅做反向标记/aria) */
const expandedNames = ref(new Set<string>())

function toggleExpand(name: string) {
  const next = new Set(expandedNames.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  expandedNames.value = next
}

interface EnvRow {
  key: string
  value: string
}

function envEntries(cfg: UpstreamConfig): EnvRow[] {
  if (!cfg.env) return []
  return Object.entries(cfg.env).map(([k, v]) => ({ key: k, value: String(v) }))
}

function looksMasked(v: string): boolean {
  return v.includes('***')
}

/**
 * 热切换上游启用/禁用:
 * - 禁用是破坏性操作 (接口立即从 /mcp 消失 + 断开连接), 先弹确认框;
 * - 成功后向 ConfigView 冒泡, 由父层不可变 patch config.upstreams 与 version;
 * - 409 与其他错误走 ElMessage, 不本地改状态 (服务端是唯一真来源)。
 */
async function onToggle(row: UpstreamRow): Promise<void> {
  if (toggling.value.has(row.name)) return
  const next = !isEnabled(row.cfg)

  if (!next) {
    try {
      await ElMessageBox.confirm(
        `确认禁用上游「${row.name}」?\n\n禁用后其 MCP 接口将立即从对外 /mcp 移除, 并断开该上游的运行时连接。状态已持久化, 重新启用即可恢复 (多上游共用的接口会保留到最后一个上游禁用)。`,
        '禁用上游确认',
        {
          confirmButtonText: '禁用',
          cancelButtonText: '取消',
          type: 'warning',
          distinguishCancelAndClose: true,
        },
      )
    } catch {
      return // 用户取消/关闭 — 状态不变
    }
  }

  toggling.value = new Set(toggling.value).add(row.name)
  try {
    const result = await setUpstreamEnabled(
      row.name,
      next,
      // revision 0 是合法初值, 不能用 || 把它吞成 '' (空串会跳过乐观锁校验)
      props.config?.version ?? '',
    )
    emit('changed', {
      name: result.name,
      enabled: result.enabled,
      version: result.version,
      connected: result.connected,
      toolsAdded: result.tools_added,
      toolsRemoved: result.tools_removed,
    })
    if (next) {
      if (result.connected) {
        ElMessage.success(
          `上游「${row.name}」已启用, 发布 ${result.tools_added.length} 个接口, 连接正常`,
        )
      } else {
        // 与启动语义同态: enabled 已生效、工具已发布, 但 client 没连上
        ElMessage.warning(
          `上游「${row.name}」已启用并发布 ${result.tools_added.length} 个接口, 但连接未建立, 请检查上游配置/状态`,
        )
      }
    } else {
      ElMessage.success(
        `上游「${row.name}」已禁用, ${result.tools_removed.length} 个接口从对外 /mcp 移除并断开连接`,
      )
    }
  } catch (e: unknown) {
    const errData = (
      e as { response?: { data?: { error?: string; version?: unknown } } }
    )?.response?.data
    // 503 config_store_unavailable: yaml 逃生模式, 覆盖层只读, 什么都没落库
    if (errData?.error === 'config_store_unavailable') {
      ElMessage.error(
        '配置覆盖层未启用 (网关以 YAML 模式启动), 无法热切换; 请以默认模式重启网关',
      )
    } else if (isUsableRevision(errData?.version)) {
      // 500 runtime toggle failed: overlay 已落库并回带新 revision。
      // 必须消费掉它并对齐 enabled 态 —— 否则 UI 仍显示旧状态、
      // 本地 revision 陈旧, 用户下一次点击必然撞 409。重启网关即收敛到目标态。
      emit('changed', {
        name: row.name,
        enabled: next,
        version: errData!.version as string | number,
        connected: false,
        toolsAdded: [],
        toolsRemoved: [],
      })
      ElMessage.warning('配置已保存到覆盖层, 但运行时热切换未完成, 重启网关后生效')
    } else {
      const msg = errorMessage(e, '操作失败')
      if (msg.includes('version_conflict') || msg.includes('revision 不一致')) {
        ElMessage.error('配置版本已变更, 请刷新页面后重试')
      } else {
        ElMessage.error(msg)
      }
    }
  } finally {
    const pending = new Set(toggling.value)
    pending.delete(row.name)
    toggling.value = pending
  }
}

const logRows = computed(() => loggingToRows(props.config?.logging))
</script>

<style scoped>
.ups-table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.ups-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ups-name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ups-status-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
}

.ups-status-cell .el-button {
  margin-left: 0;
}

.ups-expand-btn {
  width: 18px;
  height: 18px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 0.7rem;
  color: var(--cfg-text-secondary);
  transition: transform 120ms ease;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
}

.ups-expand-btn:hover {
  background: var(--cfg-bg-soft);
  color: var(--el-text-color-regular);
}

.ups-expand-btn:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}

.ups-expand-btn.expanded {
  transform: rotate(90deg);
}

.ups-endpoint {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.75rem;
  word-break: break-all;
}

.ups-detail-block {
  padding: 10px 16px 4px;
}

.ups-detail-label {
  font-size: 0.75rem;
  color: var(--cfg-text-secondary);
  margin-bottom: 6px;
}

.ups-env-table {
  background: var(--cfg-bg-soft);
  border: 1px solid var(--cfg-border);
  border-radius: 4px;
}

.ups-retry {
  display: block;
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.75rem;
  background: var(--cfg-bg-soft);
  padding: 8px 12px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow: auto;
  color: var(--el-text-color-regular);
}

.cfg-mask {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  background: var(--cfg-bg-soft);
  padding: 1px 6px;
  border-radius: 3px;
}
</style>
