<template>
  <div class="uc-panel">
    <!-- yaml 逃生模式 -->
    <div v-if="entities && !isDb" class="cfg-banner cfg-banner-warn">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        网关以 <code>UNIHIVE_CONFIG_SOURCE=yaml</code> 启动,出厂更新中心不可用:
        该模式下配置事实源就是出厂 YAML 文件,直接编辑文件并重启即可。
      </span>
    </div>

    <template v-else-if="isDb">
      <!-- 模板状态 -->
      <el-alert
        v-if="statusError"
        type="error"
        :closable="false"
        show-icon
        class="uc-alert"
        :title="`模板状态加载失败:${statusError}`"
      >
        <el-button size="small" @click="loadStatus">重试</el-button>
      </el-alert>

      <el-alert
        v-else-if="dbStatus?.build_error"
        type="error"
        :closable="false"
        show-icon
        class="uc-alert"
        title="出厂 YAML 模板构建失败,暂时无法检查更新"
      >
        <code class="uc-build-error">{{ dbStatus.build_error }}</code>
        <div class="uc-alert-hint">请先修复出厂 YAML (语法错误或明文密钥等 fail-closed 校验)。</div>
      </el-alert>

      <el-alert
        v-else-if="dbStatus && dbStatus.template_changed"
        type="warning"
        :closable="false"
        show-icon
        class="uc-alert"
        title="检测到出厂模板更新"
      >
        库内快照
        <code>{{ shortTemplateVersion(dbStatus.seeded_version) }}</code>
        与当前出厂 YAML
        <code>{{ shortTemplateVersion(dbStatus.current_version) }}</code>
        不一致。是否吸收由你审阅字段级差异后决定,系统不会自动升级。
      </el-alert>

      <el-alert
        v-else-if="dbStatus && !dbStatus.template_changed"
        type="success"
        :closable="false"
        show-icon
        class="uc-alert"
        title="库内模板与出厂 YAML 一致,暂无出厂更新"
      />

      <!-- 状态明细 + 操作 -->
      <details class="cfg-section" open>
        <summary class="cfg-section-header">
          <span class="cfg-section-title">
            <span class="cfg-section-toggle">▾</span>
            模板与修订
          </span>
        </summary>
        <div class="cfg-section-body">
          <div class="cfg-kv-grid" v-loading="statusLoading">
            <div class="cfg-kv-key">配置修订 revision</div>
            <div class="cfg-kv-val">
              <span class="num">{{ dbStatus?.revision ?? dbEntities?.revision ?? 0 }}</span>
            </div>
            <div class="cfg-kv-key">库内模板版本</div>
            <div class="cfg-kv-val">
              <code>{{ shortTemplateVersion(dbStatus?.seeded_version ?? dbEntities?.template_version) }}</code>
            </div>
            <div class="cfg-kv-key">出厂模板版本</div>
            <div class="cfg-kv-val">
              <code>{{ shortTemplateVersion(dbStatus?.current_version) }}</code>
            </div>
            <div class="cfg-kv-key">出厂模板规模</div>
            <div class="cfg-kv-val">
              <template v-if="dbStatus?.factory_counts">
                {{ dbStatus.factory_counts.upstreams }} 上游 ·
                {{ dbStatus.factory_counts.tools }} 工具 ·
                {{ dbStatus.factory_counts.sections }} 配置段
              </template>
              <span v-else>—</span>
            </div>
          </div>

          <div class="uc-actions">
            <el-button
              type="primary"
              :icon="Refresh"
              :loading="previewLoading"
              @click="runPreview()"
            >检查出厂更新</el-button>
            <span class="uc-actions-hint">
              只读预览:重新构建出厂模板并做字段级三方合并,不写库、不碰运行态
            </span>
          </div>
        </div>
      </details>

      <!-- 预览结果 -->
      <el-alert
        v-if="previewError"
        type="error"
        :closable="false"
        show-icon
        class="uc-alert"
        :title="`检查失败:${previewError}`"
      />

      <div v-if="preview" class="uc-preview">
        <!-- 不再用外层折叠包全部内容 (里面还有台账折叠, 折叠套折叠); 改为普通区段常驻展开 -->
        <section class="uc-preview-section">
          <h3 class="uc-preview-title">
            合并预览
            <code class="uc-version-arrow">
              {{ shortTemplateVersion(preview.old_version) }}
              →
              {{ shortTemplateVersion(preview.new_version) }}
            </code>
          </h3>
          <div class="uc-preview-body">
            <div v-if="!preview.changed" class="cfg-banner uc-banner-ok">
              <span class="cfg-info-icon" aria-hidden="true">✓</span>
              <span>已是最新:出厂模板相对库内快照无净变更 (revision {{ previewRevision }} 不变)。</span>
            </div>

            <template v-else>
              <div class="uc-summary" role="list">
                <span class="uc-chip uc-chip-success" role="listitem">
                  新增 <b>{{ preview.summary.added }}</b>
                </span>
                <span class="uc-chip uc-chip-danger" role="listitem">
                  删除 <b>{{ preview.summary.removed }}</b>
                </span>
                <span class="uc-chip uc-chip-primary" role="listitem">
                  更新 <b>{{ preview.summary.updated }}</b>
                </span>
                <span class="uc-chip uc-chip-warning" role="listitem">
                  保留用户改动 <b>{{ preview.summary.kept_user }}</b>
                </span>
                <span
                  class="uc-chip"
                  :class="preview.summary.conflicts ? 'uc-chip-danger' : 'uc-chip-muted'"
                  role="listitem"
                >
                  字段冲突 <b>{{ preview.summary.conflicts }}</b>
                </span>
              </div>

              <!-- 冲突逐条裁决 -->
              <section
                v-if="preview.conflicts.length"
                class="uc-conflicts"
                aria-label="字段冲突裁决"
              >
                <div class="uc-conflicts-head">
                  <h4>
                    字段冲突逐条裁决
                    <el-tag
                      v-if="pendingCount"
                      type="danger"
                      size="small"
                      disable-transitions
                    >{{ pendingCount }} 条待裁决</el-tag>
                    <el-tag v-else type="success" size="small" disable-transitions>
                      已全部裁决
                    </el-tag>
                  </h4>
                  <div class="uc-conflicts-bulk">
                    <el-button size="small" @click="decideAll('keep_cur')">
                      全部保留当前
                    </el-button>
                    <el-button size="small" @click="decideAll('accept_new')">
                      全部采用出厂
                    </el-button>
                  </div>
                </div>

                <div
                  v-for="c in preview.conflicts"
                  :key="conflictKey(c)"
                  class="uc-conflict"
                  :class="{ 'uc-conflict-pending': !isDecided(c) }"
                >
                  <div class="uc-conflict-head">
                    <el-tag size="small" :type="kindTagType(c.kind)" disable-transitions>
                      {{ kindLabel(c.kind) }}
                    </el-tag>
                    <code class="uc-conflict-name">{{ c.name }}</code>
                    <code class="uc-conflict-path">{{ c.path }}</code>
                    <span class="uc-conflict-type">{{ c.conflict }}</span>
                    <el-radio-group
                      :model-value="resolutions[conflictKey(c)]"
                      size="small"
                      class="uc-choice"
                      @update:model-value="(v: string | number | boolean | undefined) => setChoice(c, v)"
                    >
                      <el-radio value="keep_cur">保留当前</el-radio>
                      <el-radio value="accept_new">采用出厂</el-radio>
                    </el-radio-group>
                  </div>
                  <div class="uc-diff-grid">
                    <div class="uc-diff-cell">
                      <div class="uc-diff-label">出厂基线 (old)</div>
                      <pre>{{ formatConflictValue(c.old) }}</pre>
                    </div>
                    <div class="uc-diff-cell">
                      <div class="uc-diff-label">库内当前 (cur)</div>
                      <pre>{{ formatConflictValue(c.cur) }}</pre>
                    </div>
                    <div class="uc-diff-cell uc-diff-new">
                      <div class="uc-diff-label">新出厂 (new)</div>
                      <pre>{{ formatConflictValue(c.new) }}</pre>
                    </div>
                  </div>
                </div>
              </section>

              <!-- lint -->
              <el-alert
                v-for="(issue, i) in preview.lint_errors"
                :key="`le-${i}`"
                type="error"
                :closable="false"
                show-icon
                class="uc-alert"
                :title="`静态校验错误 [${issue.code}] ${issue.message}`"
              />
              <el-alert
                v-for="(issue, i) in preview.lint_warnings"
                :key="`lw-${i}`"
                type="warning"
                :closable="false"
                show-icon
                class="uc-alert"
                :title="`静态校验警告 [${issue.code}] ${issue.message}`"
              />

              <!-- 运行态影响面 -->
              <section v-if="hasImpact(preview.impact)" class="uc-impact">
                <h4>运行态影响 (热生效,无需重启)</h4>
                <div
                  v-for="group in impactGroups(preview.impact)"
                  :key="group.key"
                  class="uc-impact-row"
                >
                  <span class="uc-impact-label">{{ group.label }} ({{ group.names.length }})</span>
                  <span class="uc-impact-names">
                    <el-tag
                      v-for="n in group.names"
                      :key="n"
                      size="small"
                      :type="group.tone"
                      disable-transitions
                    >{{ n }}</el-tag>
                  </span>
                </div>
              </section>

              <!-- 变更台账: 原三个近重复折叠合并为一个区段, 按实体类型分 3 子段 -->
              <section class="uc-ledger-book">
                <h4>变更台账</h4>
                <div class="uc-ledger-group">
                  <div class="uc-ledger-group-title">
                    配置段 ({{ sectionRows.length }})
                  </div>
                  <LedgerTable v-if="sectionRows.length" :rows="sectionRows" />
                  <div v-else class="uc-ledger-empty">无</div>
                </div>
                <div class="uc-ledger-group">
                  <div class="uc-ledger-group-title">
                    上游 ({{ upstreamRows.length }})
                  </div>
                  <LedgerTable v-if="upstreamRows.length" :rows="upstreamRows" />
                  <div v-else class="uc-ledger-empty">无</div>
                </div>
                <div class="uc-ledger-group">
                  <div class="uc-ledger-group-title">
                    工具 ({{ toolRows.length }})
                  </div>
                  <LedgerTable v-if="toolRows.length" :rows="toolRows" />
                  <div v-else class="uc-ledger-empty">无</div>
                </div>
              </section>

              <!-- apply 栏 -->
              <div class="uc-apply-bar">
                <div class="uc-apply-hint">
                  <template v-if="pendingCount">
                    还有 <b>{{ pendingCount }}</b> 条字段冲突未逐条裁决,裁决后才能应用
                  </template>
                  <template v-else-if="preview.lint_errors.length">
                    候选配置存在 {{ preview.lint_errors.length }} 个静态校验错误,应用已阻止
                  </template>
                  <template v-else>
                    应用前自动写 JSON 备份到 <code>data/backups/</code>;
                    运行态切换或落库失败自动回滚,库内容保持升级前状态
                  </template>
                </div>
                <el-button
                  type="danger"
                  :loading="applying"
                  :disabled="!canApply"
                  @click="onApply"
                >热应用出厂更新</el-button>
              </div>
            </template>
          </div>
        </section>
      </div>

      <!-- apply 结果 -->
      <el-alert
        v-if="applyResult?.changed"
        type="success"
        :closable="false"
        show-icon
        class="uc-alert"
        :title="`出厂更新已热应用:revision ${applyResult.revision}`"
      >
        <div>
          模板
          <code>{{ shortTemplateVersion(applyResult.old_version) }}</code>
          →
          <code>{{ shortTemplateVersion(applyResult.new_version) }}</code>
          · 新增 {{ applyResult.summary.added }} / 删除 {{ applyResult.summary.removed }}
          / 更新 {{ applyResult.summary.updated }}
        </div>
        <div v-for="group in appliedImpactGroups" :key="group.key" class="uc-result-line">
          {{ group.label }}:{{ group.names.join('、') }}
        </div>
        <div v-for="(w, i) in applyResult.warnings" :key="`w-${i}`" class="uc-warning-line">
          ⚠ {{ w }}
        </div>
        <div v-if="applyResult.backup_path" class="uc-backup-line">
          升级前备份:<code>{{ applyResult.backup_path }}</code>
          <a :href="backupUrl" target="_blank" rel="noopener" download>下载最近一份备份</a>
        </div>
      </el-alert>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'
import {
  applyConfigUpgrade,
  CONFIG_UPGRADE_BACKUP_URL,
  getTemplateStatus,
  previewConfigUpgrade,
  type ConflictResolution,
  type ConfigEntities,
  type FactoryTemplateStatus,
  type ResolutionMap,
  type UpgradeApplyResult,
  type UpgradeConflict,
  type UpgradeImpact,
  type UpgradePreview,
} from '@/api/config'
import { errorMessage } from '@/api/client'
import { isDbEntities, shortTemplateVersion } from './entities'
import {
  canApplyUpgrade,
  conflictKey,
  decideAll as decideAllKeys,
  formatConflictValue,
  hasImpact,
  impactGroups,
  isDbStatus,
  isResolution,
  kindLabel,
  meaningfulChanges,
  pendingConflicts,
  withResolution,
  type ImpactGroup,
} from './updateCenter'
import LedgerTable from './UpgradeLedgerTable.vue'

interface Props {
  entities: ConfigEntities | null
  /** 该 tab 是否激活 (ConfigView 传入); 激活时才拉取模板状态, 避免进入配置页即自发请求。 */
  active?: boolean
}

const props = withDefaults(defineProps<Props>(), { active: false })
const emit = defineEmits<{
  applied: [payload: { revision: number }]
}>()

/** db 模式窄化后的实体快照 (模板内 props 联合类型不收窄, 走 computed)。 */
const dbEntities = computed(() =>
  isDbEntities(props.entities) ? props.entities : null,
)
const isDb = computed(() => dbEntities.value !== null)
const dbStatus = computed(() => (isDbStatus(status.value) ? status.value : null))

const status = ref<FactoryTemplateStatus | null>(null)
const statusLoading = ref(false)
const statusError = ref('')

const preview = ref<UpgradePreview | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
/** 裁决表: key=conflictKey; 未裁决的 key 不建条目 (el-radio 无选中)。 */
const resolutions = reactive<ResolutionMap>({})

const applying = ref(false)
const applyResult = ref<UpgradeApplyResult | null>(null)

const backupUrl = CONFIG_UPGRADE_BACKUP_URL

const previewRevision = computed(() => preview.value?.revision ?? 0)
const sectionRows = computed(() =>
  preview.value ? meaningfulChanges(preview.value.sections) : [],
)
const upstreamRows = computed(() =>
  preview.value ? meaningfulChanges(preview.value.upstreams_changes) : [],
)
const toolRows = computed(() =>
  preview.value ? meaningfulChanges(preview.value.tools_changes) : [],
)
const pendingCount = computed(() =>
  preview.value ? pendingConflicts(preview.value.conflicts, resolutions).length : 0,
)
const canApply = computed(() => canApplyUpgrade(preview.value, resolutions))

async function loadStatus(): Promise<void> {
  statusLoading.value = true
  statusError.value = ''
  try {
    status.value = await getTemplateStatus()
  } catch (e) {
    statusError.value = errorMessage(e, '模板状态加载失败')
  } finally {
    statusLoading.value = false
  }
}

function replaceResolutions(next: ResolutionMap): void {
  for (const key of Object.keys(resolutions)) delete resolutions[key]
  Object.assign(resolutions, next)
}

/** 只收集已做出的裁决;未裁决条目不进请求体, 引擎按默认 keep_cur 出候选。 */
function chosenResolutions(): ResolutionMap {
  const chosen: ResolutionMap = {}
  for (const [key, value] of Object.entries(resolutions)) {
    if (isResolution(value)) chosen[key] = value
  }
  return chosen
}

async function runPreview(): Promise<void> {
  previewLoading.value = true
  previewError.value = ''
  try {
    const chosen = chosenResolutions()
    const result = await previewConfigUpgrade(
      Object.keys(chosen).length ? chosen : undefined,
    )
    preview.value = result
    // 新预览可能带来新冲突 key: 保留已裁决项, 新 key 留空待逐条确认。
    const merged: ResolutionMap = {}
    for (const c of result.conflicts) {
      const key = conflictKey(c)
      if (isResolution(chosen[key])) merged[key] = chosen[key]
    }
    replaceResolutions(merged)
  } catch (e) {
    preview.value = null
    previewError.value = errorMessage(e, '出厂更新检查失败')
  } finally {
    previewLoading.value = false
  }
}

function isDecided(c: UpgradeConflict): boolean {
  return isResolution(resolutions[conflictKey(c)])
}

function setChoice(
  c: UpgradeConflict,
  value: string | number | boolean | undefined,
): void {
  if (!isResolution(value)) return
  replaceResolutions(withResolution(resolutions, conflictKey(c), value))
}

function decideAll(choice: ConflictResolution): void {
  if (!preview.value) return
  replaceResolutions(decideAllKeys(preview.value.conflicts, choice))
}

function kindTagType(kind: string): 'primary' | 'warning' | 'info' {
  if (kind === 'tool') return 'primary'
  if (kind === 'upstream') return 'warning'
  return 'info'
}

async function onApply(): Promise<void> {
  const p = preview.value
  if (!p || !canApply.value || applying.value) return

  const impactLines = impactGroups(p.impact)
    .filter((g) => g.tone !== 'success')
    .map((g) => {
      const names = g.names.slice(0, 8).join('、')
      const suffix = g.names.length > 8 ? ` 等 ${g.names.length} 个` : ''
      return `- ${g.label}:${names}${suffix}`
    })
  const message = [
    `本次将吸收出厂模板更新:新增 ${p.summary.added}、删除 ${p.summary.removed}、更新 ${p.summary.updated} 个实体/配置段。`,
    impactLines.length ? `\n运行态影响:\n${impactLines.join('\n')}` : '',
    '\n应用前会先把升级前完整配置写为 JSON 备份;随后热切换运行态并重注册工具,无需重启。',
    '\n运行态切换或落库失败会自动回滚,库内容保持升级前状态。确认应用?',
  ].join('')

  try {
    await ElMessageBox.confirm(message, '出厂更新强确认', {
      confirmButtonText: '确认热应用',
      cancelButtonText: '取消',
      type: 'warning',
      distinguishCancelAndClose: true,
    })
  } catch {
    return
  }

  applying.value = true
  try {
    const result = await applyConfigUpgrade({
      resolutions: chosenResolutions(),
      revision: p.revision,
    })
    if (!result.changed) {
      ElMessage.info('出厂模板相对库内快照无净变更')
    } else {
      applyResult.value = result
      ElMessage.success(`出厂更新已热应用,新 revision=${result.revision}`)
      emit('applied', { revision: result.revision })
    }
    preview.value = null
    replaceResolutions({})
    await loadStatus()
    // 自动复查: 成功后应显示 "已是最新", 同时刷新 revision 基线。
    await runPreview()
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status === 409) {
      ElMessage.error('配置已被其他会话修改 (revision 冲突),已为你重新检查更新')
      await runPreview()
    } else {
      ElMessage.error(errorMessage(e, '应用出厂更新失败,运行态与库均未改动'))
    }
  } finally {
    applying.value = false
  }
}

/** apply 结果影响面 → 复用 dry-run 的分组渲染。 */
const appliedImpactGroups = computed<ImpactGroup[]>(() => {
  const r = applyResult.value
  if (!r) return []
  const impact: UpgradeImpact = {
    tools_added: r.tools_added ?? [],
    tools_removed: r.tools_removed ?? [],
    tools_republished: r.tools_republished ?? [],
    upstreams_attach: r.upstreams_attached ?? [],
    upstreams_reconnect: r.upstreams_reconnected ?? [],
    upstreams_detach: r.upstreams_detached ?? [],
  }
  return impactGroups(impact)
})

/**
 * M2: 模板状态 (构建+hash 比对) 只在本 tab 激活时拉取, 不在进入配置页即自发请求。
 * 首次激活拉一次, 离开不重复; 同 ErrorTrackingPanel 的 active 模式。
 *
 * 触发条件同时跟 active 与 isDb: 深链 ?tab=updateCenter 进入本页时 active
 * 一开局就是 true 且不再变化, 而 entities (isDb 的依据) 要等接口落地 ——
 * 只听 active 会让这个 tab 永远停在空态。
 */
const statusLoadedOnce = ref(false)
watch(
  [() => props.active, isDb],
  ([active, db]) => {
    if (active && db && !statusLoadedOnce.value && !status.value && !statusLoading.value) {
      statusLoadedOnce.value = true
      void loadStatus()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.uc-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.uc-alert {
  margin: 0;
}

.uc-alert code {
  background: var(--cfg-bg-soft);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.78rem;
}

.uc-alert-hint {
  margin-top: 4px;
  font-size: 0.82rem;
}

.uc-build-error {
  display: block;
  white-space: normal;
  word-break: break-all;
}

.uc-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
}

.uc-actions-hint {
  color: var(--el-text-color-secondary);
  font-size: 0.82rem;
}

.uc-version-arrow {
  margin-left: 8px;
  font-size: 0.78rem;
}

/* 普通区段 (替代原 cfg-section 折叠), 卡片外观与全局 cfg-section 一致 */
.uc-preview-section {
  border: 1px solid var(--cfg-border);
  border-radius: 6px;
  background: var(--cfg-bg);
}

.uc-preview-title {
  display: flex;
  align-items: center;
  margin: 0;
  padding: 10px 14px;
  font-size: 0.88rem;
  font-weight: 600;
  border-bottom: 1px solid var(--cfg-border);
}

.uc-preview-body {
  padding: 12px 14px;
}

.uc-banner-ok {
  align-items: center;
}

.uc-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 12px;
}

.uc-chip {
  display: inline-flex;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 0.82rem;
  background: var(--cfg-bg-soft);
  color: var(--el-text-color-secondary);
}

.uc-chip b {
  font-variant-numeric: tabular-nums;
}

.uc-chip-success {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success-dark-2);
}

.uc-chip-danger {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger-dark-2);
}

.uc-chip-primary {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary-dark-2);
}

.uc-chip-warning {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
}

.uc-chip-muted {
  opacity: 0.7;
}

.uc-conflicts-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.uc-conflicts-head h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 0.92rem;
}

.uc-conflict {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
}

.uc-conflict-pending {
  border-left: 3px solid var(--el-color-danger);
}

.uc-conflict-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.uc-conflict-name {
  font-weight: 600;
}

.uc-conflict-path {
  color: var(--el-color-primary);
  font-size: 0.8rem;
}

.uc-conflict-type {
  color: var(--el-text-color-secondary);
  font-size: 0.78rem;
}

.uc-choice {
  margin-left: auto;
}

.uc-diff-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.uc-diff-label {
  font-size: 0.76rem;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
}

.uc-diff-cell pre {
  margin: 0;
  padding: 6px 8px;
  background: var(--cfg-bg-soft);
  border-radius: 4px;
  font-size: 0.76rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow: auto;
}

.uc-diff-new pre {
  background: var(--el-color-primary-light-9);
}

.uc-impact {
  margin: 12px 0;
}

.uc-impact h4 {
  margin: 0 0 8px;
  font-size: 0.92rem;
}

.uc-impact-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 6px;
}

.uc-impact-label {
  flex: 0 0 9em;
  font-size: 0.82rem;
  color: var(--el-text-color-secondary);
}

.uc-impact-names {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.uc-ledger-book {
  margin-top: 12px;
}

.uc-ledger-book h4 {
  margin: 0 0 8px;
  font-size: 0.92rem;
}

.uc-ledger-group {
  margin-top: 10px;
}

.uc-ledger-group:first-of-type {
  margin-top: 0;
}

.uc-ledger-group-title {
  margin-bottom: 4px;
  font-size: 0.82rem;
  color: var(--el-text-color-secondary);
}

.uc-ledger-empty {
  color: var(--el-text-color-secondary);
  font-size: 0.82rem;
  padding: 4px 0;
}

.uc-apply-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}

.uc-apply-hint {
  font-size: 0.82rem;
  color: var(--el-text-color-secondary);
}

.uc-apply-hint code,
.uc-backup-line code {
  background: var(--cfg-bg-soft);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.78rem;
}

.uc-result-line,
.uc-warning-line,
.uc-backup-line {
  font-size: 0.82rem;
  margin-top: 2px;
}

.uc-backup-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

@media (max-width: 768px) {
  .uc-diff-grid {
    grid-template-columns: 1fr;
  }

  .uc-choice {
    margin-left: 0;
  }
}
</style>
