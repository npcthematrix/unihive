<template>
  <div class="ups-panel">
    <!-- yaml 逃生模式横幅: 出厂文件即事实源, 本页所有写入口都不可用。
         不给这句话, 用户只会看到「编辑」列凭空消失, 以及点开关换来一条 503。 -->
    <div v-if="writeBlocked" class="cfg-banner cfg-banner-warn">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        网关以 <code>UNIHIVE_CONFIG_SOURCE=yaml</code> 逃生模式启动, 配置事实源是出厂
        <code>config/upstreams.yaml</code> —— 启用/停用、dangerous 封禁、全局池参数与
        连接块编辑均不可写。移除该环境变量并以默认模式重启网关即可恢复热编辑。
      </span>
    </div>

    <!-- 搜索框 -->
    <div class="ups-toolbar">
      <CfgSearchInput
        v-model="search"
        placeholder="搜索上游... (名称/类型/URL)"
        aria-label="搜索上游"
      />
    </div>

    <!-- 上游表 (el-table 自带横向滚动, 不再套外层 overflow 容器) -->
    <el-table
      ref="tableRef"
      :data="filteredEntries"
      :empty-text="emptyText"
      class="ups-table"
      row-key="name"
      @expand-change="onExpandChange"
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
              @click="toggleDetail(row)"
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
              :disabled="writeBlocked"
              :title="writeBlocked ? YAML_WRITE_HINT : undefined"
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
      <el-table-column v-if="editable" label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button
            size="small"
            text
            type="primary"
            :aria-label="`编辑上游 ${row.name} 连接块`"
            @click="openEdit(row.name)"
          >编辑</el-button>
        </template>
      </el-table-column>
      <el-table-column type="expand" v-if="anyExpandable">
        <template #default="{ row }">
          <div v-if="envEntries(row.cfg).length" class="ups-detail-block">
            <div class="ups-detail-label">环境变量 (env)</div>
            <el-table :data="envEntries(row.cfg)" class="ups-env-table">
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

    <div v-if="search.trim()" class="cfg-filter-count">
      显示 {{ filteredEntries.length }} / {{ totalUpstreams }}
    </div>

    <!-- 启动期池配置 (cfg > env > default 优先级链, 重启才生效) -->
    <details class="cfg-section">
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          全局池参数
          <span class="cfg-section-sub">— 启动期一次性, 运行时改需重启</span>
          <el-tag
            v-if="anyPendingRestart"
            type="warning"
            size="small"
            class="cfg-pool-restart-tag"
          >已修改, 重启 gateway 后生效</el-tag>
        </span>
      </summary>
      <div class="cfg-section-body">
        <div v-if="poolConfigError" class="cfg-pool-error">
          <span class="muted">运行态池参数不可用: {{ poolConfigError }}</span>
          <el-button size="small" @click="$emit('poolRetry')">重试</el-button>
        </div>
        <div v-else-if="!poolConfig" class="muted">加载中...</div>
        <div v-else>
          <!-- db 模式 + 可编辑: 输入框; 否则只读 -->
          <div class="cfg-pool-grid">
            <template v-for="row in poolRows" :key="row.key">
              <span class="cfg-pool-source">{{ row.source }}</span>
              <span class="cfg-pool-key">{{ row.key }}</span>
              <span v-if="!canEditPool" class="cfg-pool-val">{{ row.value }}</span>
              <el-input-number
                v-else
                v-model="row.draft"
                :min="1"
                :step="1"
                size="small"
                class="cfg-pool-input"
                :disabled="poolSaving"
              />
              <span class="cfg-pool-badge-cell">
                <span
                  class="cfg-pool-badge"
                  :class="`src-${row.sourceKey}`"
                >来源 {{ row.sourceLabel }}</span>
                <el-tag
                  v-if="row.needsRestart"
                  type="warning"
                  size="small"
                  effect="plain"
                  class="cfg-pool-restart-cell"
                >需重启</el-tag>
              </span>
            </template>
          </div>
          <div class="cfg-pool-meta muted">{{ poolSnapshotLabel }}</div>
          <div v-if="canEditPool" class="cfg-pool-actions">
            <el-button
              size="small"
              type="primary"
              :loading="poolSaving"
              :disabled="!poolDirty"
              @click="onSavePool"
            >保存池参数</el-button>
            <el-button
              size="small"
              :disabled="poolSaving || !poolDirty"
              @click="resetPoolDraft"
            >重置</el-button>
          </div>
        </div>
      </div>
    </details>

    <!-- 全局开关 + 日志: 两个独立折叠区 -->
    <details class="cfg-section" open>
      <summary class="cfg-section-header">
        <span class="cfg-section-title">
          <span class="cfg-section-toggle">▾</span>
          全局开关
          <span class="cfg-section-sub">— strict_registry / disable_dangerous</span>
        </span>
      </summary>
      <div class="cfg-section-body">
        <div class="cfg-toggles-row">
          <span
            class="cfg-switch"
            :class="config?.strict_registry ? 'on' : 'off'"
            :title="`启用后启动期校验配置库/YAML 中的工具名都在上游 tools/list 中实际出现 (只读全局开关, 启动时读取生效, 暂不支持热修改)`"
          >strict_registry</span>
          <!-- role=button 的 span 必须自己给 tabindex, 否则键盘永远聚焦不到,
               绑定的 enter/space 与 main.css 的 .cfg-switch.interactive:focus-visible
               都不会生效。逃生模式下移出 tab 序列, 同时去掉 aria-pressed 语义。 -->
          <span
            class="cfg-switch"
            :class="killSwitchClass"
            :role="writeBlocked ? undefined : 'button'"
            :tabindex="writeBlocked ? -1 : 0"
            :aria-pressed="writeBlocked ? undefined : Boolean(config?.disable_dangerous)"
            :title="killSwitchTitle"
            @click="onToggleKillSwitch"
            @keydown.enter.prevent="onToggleKillSwitch"
            @keydown.space.prevent="onToggleKillSwitch"
          >disable_dangerous{{ config?.disable_dangerous ? ' (已封禁)' : '' }}</span>
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

    <UpstreamEditDialog
      v-model="editDialogOpen"
      :upstream-name="editingName"
      @saved="onConnectionSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import type {
  ConfigRevision,
  GatewayConfig,
  PoolConfigEditResult,
  PoolConfigSnapshot,
  UpstreamConfig,
} from '@/api/config'
import {
  setDisableDangerous,
  setUpstreamEnabled,
  updatePoolConfig,
} from '@/api/config'
import { errorMessage } from '@/api/client'
import { formatHMS } from '@/utils/time'
import { loggingToRows, retryToString } from './format'
import {
  isUpstreamEnabled as isEnabled,
  toggleLabel,
  type UpstreamEnabledChanged,
} from './upstreams'
import { isUsableRevision } from './revisions'
import { describeEntityEditError } from './entityErrors'
import UpstreamEditDialog from './UpstreamEditDialog.vue'
import CfgSearchInput from './CfgSearchInput.vue'

interface Props {
  config: GatewayConfig | null
  /** db 实体模式才出连接块编辑入口 (yaml 逃生模式只读)。 */
  editable?: boolean
  /**
   * 确定处于 yaml 逃生模式: 写接口一律 503, 行内写控件 (启用/停用、
   * disable_dangerous、池参数) 全部置灰。与 editable 分工 —— editable 在
   * entities 未落地时也是 false, 不能用它推断「模式」, 否则横幅会闪。
   */
  writeBlocked?: boolean
  /**
   * 运行态池参数快照, 由 ConfigView 的统一刷新编排 (pageLoader) 喂入 ——
   * 面板此前自己 onMounted 拉一次, 于是「立即刷新」和乐观锁冲突重拉都刷不到
   * 它, 同一页两套刷新节奏。
   */
  pool?: PoolConfigSnapshot | null
  /** 池快照通道的错误文本 (空串 = 正常)。 */
  poolError?: string
}

const props = withDefaults(defineProps<Props>(), {
  editable: false,
  writeBlocked: false,
  pool: null,
  poolError: '',
})

/** 逃生模式下所有写控件共用的 tooltip 文案。 */
const YAML_WRITE_HINT = 'YAML 逃生模式下不可写, 需以默认模式重启网关'

const emit = defineEmits<{
  changed: [payload: UpstreamEnabledChanged]
  connectionSaved: [payload: {
    name: string
    revision: number
    enabled: boolean
    connected: boolean
    changed: boolean
  }]
  killSwitchChanged: [payload: {
    disabled: boolean
    version: ConfigRevision
  }]
  poolConfigSaved: [payload: {
    revision: ConfigRevision
    pending_restart: PoolConfigEditResult['pending_restart']
  }]
  /** 池快照通道失败后的重试: 数据源在父层 pageLoader, 面板只发意图。 */
  poolRetry: []
  /**
   * 行内写操作撞乐观锁 409 且错误体没回带新 revision —— 本地 version 已经
   * 陈旧, 请父层重拉配置; 否则用户每次重试都再撞一次 409。
   */
  writeConflict: []
}>()

/** 连接块编辑弹窗状态。 */
const editDialogOpen = ref(false)
const editingName = ref<string | null>(null)

function openEdit(name: string): void {
  editingName.value = name
  editDialogOpen.value = true
}

function onConnectionSaved(payload: {
  name: string
  revision: number
  enabled: boolean
  connected: boolean
  changed: boolean
}): void {
  emit('connectionSaved', payload)
  editingName.value = null
}

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

/** 空表文案只管「为什么空」; N / M 计数交给表格下方 .cfg-filter-count 页脚
 *  (与 Mapping / Tools 两个面板同一条规则, 命中非空时也能看到总数)。 */
const emptyText = computed(() => {
  if (!totalUpstreams.value) return '暂无上游配置'
  return '无匹配项'
})

/** el-table 实例: 名称列的自定义 ▸ 按钮通过它驱动真 expand 列。 */
const tableRef = ref<TableInstance | null>(null)

/** 记录已展开的 upstream name, 仅用于 ▸ 旋转态/aria; 展开真源是 el-table。 */
const expandedNames = ref(new Set<string>())

function onExpandChange(
  row: UpstreamRow,
  expandedRows: UpstreamRow[] | UpstreamRow,
): void {
  // Element Plus 第二参传 expandedRows 数组或当前 row, 两种形态都兼容
  // (同 InterfacesView 既有写法)。
  const expanded = Array.isArray(expandedRows)
    ? expandedRows.some((r) => r.name === row.name)
    : Boolean(expandedRows)
  const next = new Set(expandedNames.value)
  if (expanded) next.add(row.name)
  else next.delete(row.name)
  expandedNames.value = next
}

function toggleDetail(row: UpstreamRow): void {
  tableRef.value?.toggleRowExpansion(row)
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
  if (props.writeBlocked || toggling.value.has(row.name)) return
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
    // 503 config_store_unavailable: yaml 逃生模式, 配置库不可用, 什么都没落库
    if (errData?.error === 'config_store_unavailable') {
      ElMessage.error(
        '配置库不可用 (网关以 YAML 逃生模式启动), 无法热切换; 请移除 UNIHIVE_CONFIG_SOURCE=yaml 后以默认模式重启网关',
      )
    } else if (isUsableRevision(errData?.version)) {
      // 500 runtime toggle failed: enabled 已写实体行并回带新 revision。
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
      ElMessage.warning('配置已写入配置库, 但运行时热切换未完成, 重启网关后生效')
    } else {
      const msg = errorMessage(e, '操作失败')
      if (msg.includes('version_conflict') || msg.includes('revision 不一致')) {
        ElMessage.error('配置版本已变更, 已重新拉取最新配置, 请再点一次')
        emit('writeConflict')
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

/** kill switch in-flight 状态 (不可变替换触发重渲染/禁用点击)。 */
const killSwitchBusy = ref(false)

/**
 * 开关徽标类: on/off 恒等于「配置里的值」(逃生模式下也要看得见当前值),
 * interactive 只在可写时给 —— 没它就没有 pointer/cursor, 视觉上即不可点。
 */
const killSwitchClass = computed(() => ({
  on: Boolean(props.config?.disable_dangerous),
  off: !props.config?.disable_dangerous,
  interactive: !props.writeBlocked,
  busy: killSwitchBusy.value && !props.writeBlocked,
}))

const killSwitchTitle = computed(() => {
  if (props.writeBlocked) return YAML_WRITE_HINT
  return props.config?.disable_dangerous
    ? '点击解除封禁: 补回当前可发布的 dangerous 工具'
    : '点击封禁: 立即从对外 /mcp 摘除所有 dangerous: true 的工具 (写操作类)'
})

/**
 * 热切换 disable_dangerous:
 * - 封禁 (off→on) 会让写操作类 dangerous 接口立即消失, 先弹确认;
 * - 成功后向 ConfigView 冒泡 patch 顶层 disable_dangerous 与 version;
 * - 409 / 500 处理与上游热开关同规则 (服务端是唯一真来源)。
 */
async function onToggleKillSwitch(): Promise<void> {
  if (props.writeBlocked || killSwitchBusy.value) return
  const next = !props.config?.disable_dangerous

  if (next) {
    try {
      await ElMessageBox.confirm(
        '确认封禁所有 dangerous 工具?\n\n封禁后所有 dangerous: true 的 MCP 接口将立即从对外 /mcp 移除 (主要是写操作类), 状态已持久化; 再次点击该开关即可解除封禁并恢复发布。',
        '封禁危险工具确认',
        {
          confirmButtonText: '封禁',
          cancelButtonText: '取消',
          type: 'warning',
          distinguishCancelAndClose: true,
        },
      )
    } catch {
      return // 用户取消/关闭 — 状态不变
    }
  }

  killSwitchBusy.value = true
  try {
    const result = await setDisableDangerous(
      next,
      props.config?.version ?? '',
    )
    emit('killSwitchChanged', {
      disabled: result.disabled,
      version: result.version,
    })
    if (next) {
      ElMessage.success(
        `已封禁 dangerous 工具, ${result.tools_removed.length} 个接口从对外 /mcp 移除`,
      )
    } else {
      ElMessage.success(
        `已解除封禁, 补回 ${result.tools_added.length} 个可发布的 dangerous 接口`,
      )
    }
  } catch (e: unknown) {
    const errData = (
      e as { response?: { data?: { error?: string; version?: unknown } } }
    )?.response?.data
    if (errData?.error === 'config_store_unavailable') {
      ElMessage.error(
        '配置库不可用 (网关以 YAML 逃生模式启动), 无法热切换; 请移除 UNIHIVE_CONFIG_SOURCE=yaml 后以默认模式重启网关',
      )
    } else if (isUsableRevision(errData?.version)) {
      // 500: 已写实体行并回带新 revision, 必须消费掉避免后续 409。
      emit('killSwitchChanged', {
        disabled: next,
        version: errData!.version as ConfigRevision,
      })
      ElMessage.warning('配置已写入配置库, 但运行时热切换未完成, 重启网关后生效')
    } else {
      const msg = errorMessage(e, '操作失败')
      if (msg.includes('version_conflict') || msg.includes('revision 不一致')) {
        ElMessage.error('配置版本已变更, 已重新拉取最新配置, 请再点一次')
        emit('writeConflict')
      } else {
        ElMessage.error(msg)
      }
    }
  } finally {
    killSwitchBusy.value = false
  }
}

const logRows = computed(() => loggingToRows(props.config?.logging))

// ---------------------------------------------------------------------------
// 启动期池配置 (db 模式可编辑; 保存即落 config.db, 运行时改需重启)
// ---------------------------------------------------------------------------

/** 快照与错误都由父层 pageLoader 的池通道喂入 (统一刷新节奏)。 */
const poolConfig = computed(() => props.pool)
const poolConfigError = computed(() => props.poolError || null)
/** PUT 响应: persisted + running + pending_restart, 用于"需重启"badge 渲染。 */
const poolEditState = ref<PoolConfigEditResult | null>(null)
const poolSaving = ref(false)
/** 三字段编辑中草稿 (UI 状态, 不持久化)。 */
const poolDraft = reactive<{ mootdx2_reader_limit: number; mootdx2_worker_threads: number; tdx_quant_worker_threads: number }>({
  mootdx2_reader_limit: 0,
  mootdx2_worker_threads: 0,
  tdx_quant_worker_threads: 0,
})

/** 草稿是否被用户动过 —— 决定新一轮快照能否覆盖它 (见下方 watcher)。 */
const draftTouched = ref(false)
/** 程序化对齐草稿时, 不得反过来把 draftTouched 置真。 */
let syncingDraft = false

/**
 * 草稿任一字段变 → 清掉 "需重启" badge (poolEditState) + 记下「用户动过」。
 *
 * badge 仅在「落库但运行态未生效」期间显示, 一旦用户再次编辑意味着
 * 旧 PUT 的 pending_restart 已被覆盖 (新 draft 会重新走 PUT), 不应
 * 残留误导。watcher 默认 flush:'pre', 会在 resetPoolDraft 的同步赋值后批处理,
 * 所以由 resetPoolDraft 自己用 syncingDraft 守卫挡住「对齐草稿」被误判成
 * 「用户编辑」。
 */
watch(
  () => [
    poolDraft.mootdx2_reader_limit,
    poolDraft.mootdx2_worker_threads,
    poolDraft.tdx_quant_worker_threads,
  ],
  () => {
    poolEditState.value = null
    if (!syncingDraft) draftTouched.value = true
  },
)

/**
 * 池快照落地/更新 (首次加载、立即刷新、乐观锁冲突重拉都走这里):
 * 用户还没动过草稿才对齐到当前运行态, 否则保留输入 —— 否则一次后台
 * 刷新会把正在填的池参数清空。
 */
watch(
  poolConfig,
  (snap) => {
    if (snap && !draftTouched.value) resetPoolDraft()
  },
  { immediate: true },
)

/** db 实体模式 + 可编辑 prop + 池 snapshot 已加载 — 三者都齐才显示表单。 */
const canEditPool = computed(() =>
  Boolean(props.editable && poolConfig.value && !poolConfigError.value),
)

interface PoolRow {
  key: 'mootdx2_reader_limit' | 'mootdx2_worker_threads' | 'tdx_quant_worker_threads'
  source: string
  value: number
  draft: number
  sourceKey: string
  sourceLabel: string
  needsRestart: boolean
}

/** 把 cfg / env / default 翻成中文标注; 后端返非预期值时保留原文。 */
function sourceLabel(s: string | undefined): string {
  if (s === 'cfg') return '配置库'
  if (s === 'env') return '环境变量'
  if (s === 'default') return '代码默认'
  return s || '代码默认'
}

/** poolConfig + poolEditState + poolDraft → 三行渲染数据。 */
const poolRows = computed<PoolRow[]>(() => {
  if (!poolConfig.value) return []
  const snap = poolConfig.value
  const edit = poolEditState.value
  return [
    {
      key: 'mootdx2_reader_limit',
      source: 'mootdx2',
      value: snap.mootdx2_reader_limit,
      draft: poolDraft.mootdx2_reader_limit,
      sourceKey: snap.sources.mootdx2_reader_limit || 'default',
      sourceLabel: sourceLabel(snap.sources.mootdx2_reader_limit),
      needsRestart: Boolean(edit?.pending_restart?.mootdx2_reader_limit),
    },
    {
      key: 'mootdx2_worker_threads',
      source: 'mootdx2',
      value: snap.mootdx2_worker_threads,
      draft: poolDraft.mootdx2_worker_threads,
      sourceKey: snap.sources.mootdx2_worker_threads || 'default',
      sourceLabel: sourceLabel(snap.sources.mootdx2_worker_threads),
      needsRestart: Boolean(edit?.pending_restart?.mootdx2_worker_threads),
    },
    {
      key: 'tdx_quant_worker_threads',
      source: 'tdx_quant',
      value: snap.tdx_quant_worker_threads,
      draft: poolDraft.tdx_quant_worker_threads,
      sourceKey: snap.sources.tdx_quant_worker_threads || 'default',
      sourceLabel: sourceLabel(snap.sources.tdx_quant_worker_threads),
      needsRestart: Boolean(edit?.pending_restart?.tdx_quant_worker_threads),
    },
  ]
})

/** 三字段是否都被改过 (与 snapshot 比, 任一不同即 dirty)。 */
const poolDirty = computed(() => {
  if (!poolConfig.value) return false
  return (
    poolDraft.mootdx2_reader_limit !== poolConfig.value.mootdx2_reader_limit
    || poolDraft.mootdx2_worker_threads !== poolConfig.value.mootdx2_worker_threads
    || poolDraft.tdx_quant_worker_threads !== poolConfig.value.tdx_quant_worker_threads
  )
})

const anyPendingRestart = computed(() =>
  Boolean(poolEditState.value?.pending_restart
    && Object.values(poolEditState.value.pending_restart).some(Boolean)),
)

function resetPoolDraft(): void {
  const snap = poolConfig.value
  if (!snap) return
  syncingDraft = true
  poolDraft.mootdx2_reader_limit = snap.mootdx2_reader_limit
  poolDraft.mootdx2_worker_threads = snap.mootdx2_worker_threads
  poolDraft.tdx_quant_worker_threads = snap.tdx_quant_worker_threads
  poolEditState.value = null
  draftTouched.value = false
  void nextTick(() => { syncingDraft = false })
}

async function onSavePool(): Promise<void> {
  if (!poolConfig.value || poolSaving.value) return
  poolSaving.value = true
  try {
    const result = await updatePoolConfig(
      {
        mootdx2_reader_limit: poolDraft.mootdx2_reader_limit,
        mootdx2_worker_threads: poolDraft.mootdx2_worker_threads,
        tdx_quant_worker_threads: poolDraft.tdx_quant_worker_threads,
      },
      // revision 0 是合法初值; |"" 跳过乐观锁; 非空 Number 走检查
      { revision: typeof props.config?.version === 'number' ? props.config.version : undefined },
    )
    poolEditState.value = result
    if (result.changed) {
      ElMessage.success(
        `池参数已保存, ${Object.values(result.pending_restart).filter(Boolean).length} 项需重启 gateway 后生效`,
      )
      emit('poolConfigSaved', {
        revision: result.revision,
        pending_restart: result.pending_restart,
      })
    } else {
      // no-op 不 emit poolConfigSaved — 与 onUpstreamConnectionSaved/
      // onFactoryUpgradeApplied 等兄弟 handler 不同: 它们即便 changed=false
      // 也 emit (revision / enabled 可能未变, 但实体行本身可能漂)。
      // 池参数 PUT 的 no-op 路径三字段 + revision 全部相等, 父级
      // ConfigView 跑 loadEntities/loadInterfaceTools 会是无意义的 GET;
      // 主动跳过可避免额外往返。
      ElMessage.info('池参数无变化')
    }
  } catch (e: unknown) {
    const info = describeEntityEditError(e)
    if (info.kind === 'revision_conflict') {
      ElMessage.error('配置版本已变更, 已重新拉取最新配置, 请再保存一次')
      // 父层整份重拉: 换掉陈旧 version, 池通道同时刷新 (草稿由
      // draftTouched 保住 —— 冲突说明别的会话动过配置, 当前输入仍然
      // 大概率是想要的目标值)。
      emit('writeConflict')
    } else if (info.kind === 'store_unavailable') {
      ElMessage.error('配置库不可用 (YAML 逃生模式), 无法保存')
    } else {
      ElMessage.error(info.message || '保存失败')
    }
  } finally {
    poolSaving.value = false
  }
}

/**
 * 运行态快照的采集时刻 (后端 UNIX 秒)。过去面板只在挂载时拉一次, 展示的值
 * 可能是几分钟前的旧读数却无任何标注; 纳入统一刷新后必须把新鲜度一起给出。
 */
const poolSnapshotLabel = computed(() =>
  poolConfig.value ? `运行态采样 ${formatHMS(poolConfig.value.timestamp * 1000)}` : '',
)
</script>

<style scoped>
.ups-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 搜索框独占一行 (OpenAPI 导入按钮已下线, 不再同行) */
.ups-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ups-toolbar .cfg-search-wrap {
  flex: 1;
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

/* 全局池参数 — 启动期 cfg > env > default 三档来源标注。
   grid 而非 kv 列表: 让一行四列对齐 (上游/字段/值/来源), 切换折叠时
   高度变化最小。 */
.cfg-pool-grid {
  display: grid;
  grid-template-columns: minmax(80px, max-content) max-content max-content 1fr;
  column-gap: 16px;
  row-gap: 6px;
  align-items: baseline;
}

.cfg-pool-row {
  display: contents;
}

.cfg-pool-source {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--cfg-text-secondary);
}

.cfg-pool-key {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--el-text-color-regular);
}

.cfg-pool-val {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--el-color-primary);
}

.cfg-pool-badge {
  display: inline-block;
  font-size: 0.7rem;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--cfg-border);
  background: var(--cfg-bg-soft);
  color: var(--cfg-text-secondary);
  white-space: nowrap;
  justify-self: start;
}

.cfg-pool-badge.src-cfg {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.cfg-pool-badge.src-env {
  border-color: var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
}

.cfg-pool-badge.src-default {
  border-color: var(--cfg-border);
  background: var(--cfg-bg-soft);
  color: var(--cfg-text-secondary);
}

/* 折叠标题旁的全局"已修改需重启" 提示 */
.cfg-pool-restart-tag {
  margin-left: 8px;
  vertical-align: middle;
}

/* badge 单元 (badge + 可选 需重启 tag) */
.cfg-pool-badge-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.cfg-pool-restart-cell {
  font-weight: 600;
}

/* el-input-number 在 grid 单元里撑满整列 */
.cfg-pool-input {
  width: 100%;
  justify-self: stretch;
}

.cfg-pool-input :deep(.el-input__inner) {
  text-align: left;
}

/* 运行态采样时刻: 池参数纳入统一刷新后, 这行说明读数是几点采的。 */
.cfg-pool-meta {
  margin-top: 8px;
  font-size: 12px;
}

/* 快照不可用 (404/503/瞬时失败) 时给一个就地重试入口 —— 数据源在父层
   pageLoader 的池通道, 面板只发 poolRetry。 */
.cfg-pool-error {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 保存/重置 行 */
.cfg-pool-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px dashed var(--cfg-border);
}

/* L1: 窄屏池参数四列网格前三列等宽字体不收缩, 第四列被挤溢出;
   折成两行 (来源+键 / 值+徽章) 并允许整区横滚兜底。 */
@media (max-width: 768px) {
  .cfg-pool-grid {
    grid-template-columns: max-content 1fr;
    column-gap: 12px;
  }

  .cfg-pool-val,
  .cfg-pool-input {
    grid-column: 2;
  }

  .cfg-pool-badge-cell {
    grid-column: 1 / -1;
  }
}
</style>
