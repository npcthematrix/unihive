<template>
  <div class="ovl-panel" v-loading="loading">
    <!-- 说明 banner -->
    <div class="cfg-banner ovl-info">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        YAML 是出厂基线, <code class="ovl-key">data/config.db</code> 只保存与出厂
        <strong>不同</strong>的点路径覆盖。删除单行 = 该路径恢复出厂值;
        上游开关类覆盖删除后会立即按出厂值热收敛, 其余路径重启后生效。
      </span>
    </div>

    <!-- YAML 逃生模式: 覆盖层未启用 -->
    <el-alert
      v-if="disabled"
      type="warning"
      :closable="false"
      show-icon
      title="配置覆盖层未启用"
      description="网关当前以 UNIHIVE_CONFIG_SOURCE=yaml 启动, 上游热开关与 FUYAO TTL 保存均不可用。以默认模式重启网关后即可在此管理本地覆盖。"
    />

    <template v-else>
      <!-- 工具栏: revision + 计数 + 一键恢复出厂 -->
      <div class="ovl-toolbar">
        <span class="ovl-revision">
          revision <code class="ovl-rev-num">{{ revision ?? '—' }}</code>
          · {{ overlays.length }} 条本地覆盖
        </span>
        <el-button
          type="danger"
          plain
          size="small"
          :disabled="overlays.length === 0 || busyAll"
          :loading="busyAll"
          @click="onResetAll"
        >全部恢复出厂</el-button>
      </div>

      <!-- 空状态 -->
      <div v-if="overlays.length === 0" class="ovl-empty">
        没有本地覆盖 — 当前生效配置与 YAML 出厂基线完全一致。
      </div>

      <!-- 覆盖行表 -->
      <el-table
        v-else
        :data="overlays"
        class="cfg-table ovl-table"
        row-key="path"
      >
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="kindInfo(row.path).hot ? 'primary' : 'info'"
              effect="plain"
            >{{ kindInfo(row.path).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="路径" min-width="280">
          <template #default="{ row }">
            <code class="ovl-path">{{ row.path }}</code>
          </template>
        </el-table-column>
        <el-table-column label="本地值" min-width="160">
          <template #default="{ row }">
            <span
              v-if="kindInfo(row.path).kind === 'upstream-enabled'"
              class="ovl-value"
            >{{ describeEnabledValue(row.value) }}</span>
            <span
              v-else-if="kindInfo(row.path).kind === 'fuyao-ttl'"
              class="ovl-value"
            >{{ describeTtlSeconds(row.value) }}</span>
            <el-tooltip
              v-else
              :content="formatOverlayValue(row.value)"
              placement="top"
              :disabled="formatOverlayValue(row.value).length <= 40"
            >
              <code class="ovl-value ovl-value--raw">{{
                truncate(formatOverlayValue(row.value), 40)
              }}</code>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="行 revision" width="100" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.revision }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间 (本地)" width="170">
          <template #default="{ row }">
            <span class="ovl-time">{{ formatUpdatedAt(row.updated_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="right">
          <template #default="{ row }">
            <el-button
              link
              type="danger"
              size="small"
              :loading="busyPaths.has(row.path)"
              @click="onResetOne(row)"
            >恢复默认</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteConfigOverlay,
  resetConfigOverlays,
  type ConfigOverlayRow,
  type ConfigRevision,
} from '@/api/config'
import { errorMessage } from '@/api/client'
import {
  describeEnabledValue,
  describeTtlSeconds,
  formatOverlayValue,
  formatUpdatedAt,
  overlayKind,
  upstreamNameOf,
} from './overlays'
import { revisionOrSkip } from './revisions'

interface Props {
  overlays: ConfigOverlayRow[]
  /** 当前全局 revision; null = 尚未加载。 */
  revision: number | null
  /** true = 网关以 yaml 逃生模式启动, 覆盖层只读不可用。 */
  disabled: boolean
  loading: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  /** 任意写操作成功后冒泡, 父组件应重拉 overlays 与 config。 */
  changed: [revision: ConfigRevision]
}>()

const kindInfo = overlayKind

const busyPaths = ref<Set<string>>(new Set())
const busyAll = ref(false)

function truncate(s: string, n: number): string {
  return s.length <= n ? s : `${s.slice(0, n - 1)}…`
}

function markBusy(path: string, on: boolean) {
  const next = new Set(busyPaths.value)
  if (on) next.add(path)
  else next.delete(path)
  busyPaths.value = next
}

function reportWarnings(warnings: string[] | undefined) {
  if (!warnings || warnings.length === 0) return
  // 后端热收敛告警 (如运行时未注入需重启): 行已落库, 仅提示不报错
  ElMessage.warning(warnings.join('; '))
}

function isConflict(e: unknown): boolean {
  const msg = errorMessage(e, '')
  return msg.includes('version_conflict') || msg.includes('revision 不一致')
}

async function onResetOne(row: ConfigOverlayRow): Promise<void> {
  if (busyPaths.value.has(row.path)) return
  const upName = upstreamNameOf(row.path)
  const hotHint = upName
    ? `\n\n将按 YAML 出厂值立即热收敛上游「${upName}」的发布集与连接状态。`
    : kindInfo(row.path).hot
      ? '\n\n保存后立即生效。'
      : '\n\n重启网关后生效。'
  try {
    await ElMessageBox.confirm(
      `确认删除覆盖「${row.path}」, 恢复为 YAML 出厂值?${hotHint}`,
      '恢复默认确认',
      {
        confirmButtonText: '恢复默认',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true,
      },
    )
  } catch {
    return
  }

  markBusy(row.path, true)
  try {
    const result = await deleteConfigOverlay(
      row.path,
      revisionOrSkip(props.revision),
    )
    reportWarnings(result.warnings)
    ElMessage.success(`已恢复默认: ${row.path}`)
    emit('changed', result.version)
  } catch (e: unknown) {
    if (isConflict(e)) {
      ElMessage.error('配置版本已变更, 请刷新页面后重试')
    } else {
      ElMessage.error(errorMessage(e, '删除覆盖失败'))
    }
  } finally {
    markBusy(row.path, false)
  }
}

async function onResetAll(): Promise<void> {
  if (busyAll.value || props.overlays.length === 0) return
  const enabledCount = props.overlays.filter(
    (r) => kindInfo(r.path).kind === 'upstream-enabled',
  ).length
  const hotHint = enabledCount > 0
    ? `\n\n其中 ${enabledCount} 个上游开关会立即按出厂值热收敛 (发布集与连接同步变化)。`
    : ''
  try {
    await ElMessageBox.confirm(
      `确认清空全部 ${props.overlays.length} 条本地覆盖, 整体恢复到 YAML 出厂基线?${hotHint}\n\n此操作不可撤销。`,
      '全部恢复出厂确认',
      {
        confirmButtonText: '全部恢复出厂',
        cancelButtonText: '取消',
        type: 'error',
        distinguishCancelAndClose: true,
      },
    )
  } catch {
    return
  }

  busyAll.value = true
  try {
    const result = await resetConfigOverlays(revisionOrSkip(props.revision))
    reportWarnings(result.warnings)
    ElMessage.success('已清空全部本地覆盖, 配置恢复为 YAML 出厂基线')
    emit('changed', result.version)
  } catch (e: unknown) {
    if (isConflict(e)) {
      ElMessage.error('配置版本已变更, 请刷新页面后重试')
    } else {
      ElMessage.error(errorMessage(e, '恢复出厂失败'))
    }
  } finally {
    busyAll.value = false
  }
}
</script>

<style scoped>
.ovl-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ovl-key {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 0 4px;
  border-radius: 3px;
}

.ovl-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.ovl-revision {
  font-size: 0.85rem;
  color: var(--cfg-text-secondary);
}

.ovl-rev-num {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 0 6px;
  border-radius: 3px;
}

.ovl-empty {
  padding: 24px 16px;
  background: var(--cfg-bg-soft);
  border: 1px dashed var(--cfg-border);
  border-radius: 4px;
  font-size: 0.85rem;
  color: var(--cfg-text-secondary);
  text-align: center;
}

.ovl-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  word-break: break-all;
}

.ovl-value {
  font-size: 0.82rem;
}

.ovl-value--raw {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.76rem;
  background: var(--cfg-bg-soft);
  padding: 1px 6px;
  border-radius: 3px;
}

.ovl-time {
  font-size: 0.78rem;
  color: var(--cfg-text-secondary);
  font-variant-numeric: tabular-nums;
}
</style>
