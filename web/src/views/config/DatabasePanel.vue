<template>
  <div class="db-panel">
    <!-- yaml 逃生模式 (entities 已加载但 source!=db; 加载中不渲染横幅) -->
    <div v-if="entities && !isDb" class="cfg-banner db-banner db-banner-yaml">
      <span class="cfg-info-icon" aria-hidden="true">i</span>
      <span>
        网关以 <code>UNIHIVE_CONFIG_SOURCE=yaml</code> 启动,当前配置事实源是
        <code>config/upstreams.yaml</code> 出厂文件,数据库实体浏览不可用。
        控制台的启用开关与 FUYAO TTL 写入在该模式下同样停用。
      </span>
    </div>

    <template v-else-if="entities">
      <div class="cfg-banner db-banner">
        <span class="cfg-info-icon" aria-hidden="true">i</span>
        <span>
          配置事实源为 <code>data/config.db</code> 实体表;出厂 YAML 仅作模板保留。
          本页只读浏览实体来源与修订版本;字段编辑见「上游」「工具规格」标签,
          出厂模板更新在「出厂更新」标签审阅后吸收。
        </span>
      </div>

      <!-- 修订 / 模板 -->
      <details class="cfg-section" open>
        <summary class="cfg-section-header">
          <span class="cfg-section-title">
            <span class="cfg-section-toggle">▾</span>
            数据库与模板
          </span>
        </summary>
        <div class="cfg-section-body">
          <div class="cfg-kv-grid">
            <div class="cfg-kv-key">配置修订 revision</div>
            <div class="cfg-kv-val">
              <span class="num">{{ entities.revision }}</span>
              <span class="cfg-sub">每次净变更 +1,乐观锁版本</span>
            </div>
            <div class="cfg-kv-key">出厂模板版本</div>
            <div class="cfg-kv-val">
              <code>{{ shortTemplateVersion(entities.template_version) }}</code>
            </div>
            <div class="cfg-kv-key">模板导入时间</div>
            <div class="cfg-kv-val">{{ formatUpdatedAt(entities.template_imported_at) }}</div>
            <div class="cfg-kv-key">用户改动</div>
            <div class="cfg-kv-val">
              <span class="num">{{ userModified }}</span>
              <span class="cfg-sub">个实体行的来源非出厂模板</span>
            </div>
          </div>
        </div>
      </details>

      <!-- 上游实体 -->
      <details class="cfg-section" open>
        <summary class="cfg-section-header">
          <span class="cfg-section-title">
            <span class="cfg-section-toggle">▾</span>
            {{ entities.upstreams.length }} 个上游实体
          </span>
        </summary>
        <div class="cfg-section-body">
          <el-table :data="entities.upstreams" class="cfg-table" size="small">
            <el-table-column label="名称" min-width="200">
              <template #default="{ row }">
                <span class="tool-name">{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="类型" min-width="160">
              <template #default="{ row }"><code>{{ row.type }}</code></template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <span class="cfg-switch" :class="row.enabled ? 'on' : 'off'">
                  {{ row.enabled ? '启用' : '停用' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="110">
              <template #default="{ row }">
                <el-tag :type="originTagType(row.origin)" size="small" disable-transitions>
                  {{ originLabel(row.origin) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" min-width="170">
              <template #default="{ row }">
                <span class="db-time">{{ formatUpdatedAt(row.updated_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </details>

      <!-- 配置段实体 -->
      <details class="cfg-section" open>
        <summary class="cfg-section-header">
          <span class="cfg-section-title">
            <span class="cfg-section-toggle">▾</span>
            {{ entities.sections.length }} 个配置段
          </span>
        </summary>
        <div class="cfg-section-body">
          <el-table :data="entities.sections" class="cfg-table" size="small">
            <el-table-column label="配置段" min-width="200">
              <template #default="{ row }">
                <span class="tool-name">{{ row.section }}</span>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="110">
              <template #default="{ row }">
                <el-tag :type="originTagType(row.origin)" size="small" disable-transitions>
                  {{ originLabel(row.origin) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" min-width="170">
              <template #default="{ row }">
                <span class="db-time">{{ formatUpdatedAt(row.updated_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </details>

      <!-- 工具实体 -->
      <details class="cfg-section" open>
        <summary class="cfg-section-header">
          <span class="cfg-section-title">
            <span class="cfg-section-toggle">▾</span>
            {{ entities.tools.total }} 个工具实体
          </span>
        </summary>
        <div class="cfg-section-body">
          <div class="cfg-kv-grid">
            <template v-for="[origin, count] in toolOriginRows" :key="origin">
              <div class="cfg-kv-key">{{ originLabel(origin) }}</div>
              <div class="cfg-kv-val"><span class="num">{{ count }}</span> 个</div>
            </template>
          </div>
        </div>
      </details>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigEntities, ConfigEntityOrigin } from '@/api/config'
import {
  formatUpdatedAt,
  isDbEntities,
  originLabel,
  originTagType,
  shortTemplateVersion,
  userModifiedCount,
} from './entities'

interface Props {
  entities: ConfigEntities | null
}

const props = defineProps<Props>()

const isDb = computed(() => isDbEntities(props.entities))
const entities = computed(() =>
  isDbEntities(props.entities) ? props.entities : null,
)
const userModified = computed(() => userModifiedCount(props.entities))

/** 工具按来源聚合行, 固定 builtin 在前, 其余来源按名字排序。 */
const toolOriginRows = computed<Array<[ConfigEntityOrigin, number]>>(() => {
  const byOrigin = entities.value?.tools.by_origin ?? {}
  return Object.entries(byOrigin).sort(([a], [b]) => {
    if (a === 'builtin') return -1
    if (b === 'builtin') return 1
    return a.localeCompare(b)
  }) as Array<[ConfigEntityOrigin, number]>
})
</script>

<style scoped>
.db-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.db-banner {
  display: flex;
  align-items: center;
  gap: 8px;
}

.db-banner code {
  background: var(--cfg-bg-soft);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.78rem;
}

.db-banner-yaml {
  border-left: 3px solid var(--el-color-warning);
}

.cfg-sub {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 0.78rem;
}

.db-time {
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}
</style>
