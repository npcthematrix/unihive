<script setup lang="ts">
/** 工具展开详情: 危险提示 / 缓存 / 路由链 / 参数 / 数据源 (对应旧 buildDetailBody)。 */
import type { ToolParamInfo, ToolSpec } from '@/api/interfaces'
import { isParamPlaceholder } from './tools'

defineProps<{ tool: ToolSpec }>()

function asInfo(param: string | ToolParamInfo): ToolParamInfo | null {
  return typeof param === 'string' ? null : param
}

function ttlText(tool: ToolSpec): string {
  // 调用点在 v-if="tool.cache_ttl_key" 内; 无缓存由外层 v-else 直接渲染。
  if (tool.cache_ttl_seconds == null) return '未设 TTL'
  return `${tool.cache_ttl_seconds} 秒`
}
</script>

<template>
  <div class="detail-body">
    <el-alert
      v-if="tool.dangerous"
      class="danger-banner"
      type="error"
      :closable="false"
      show-icon
    >
      <template #title>
        高风险工具 ({{ tool.source || '未指定上游' }}): 可透传任意路径, 需谨慎授权
      </template>
    </el-alert>

    <section class="detail-section">
      <h4 class="section-title">缓存</h4>
      <template v-if="tool.cache_ttl_key">
        <div class="detail-kv">
          <span class="detail-key">TTL Key</span>
          <span class="detail-val"><code>{{ tool.cache_ttl_key }}</code></span>
        </div>
        <div class="detail-kv">
          <span class="detail-key">TTL</span>
          <span class="detail-val">{{ ttlText(tool) }}</span>
        </div>
      </template>
      <div v-else class="detail-kv">
        <span class="detail-key">缓存</span>
        <span class="detail-val">无</span>
      </div>
    </section>

    <section v-if="tool.routing || (tool.chain && tool.chain.length)" class="detail-section">
      <h4 class="section-title">路由</h4>
      <div v-if="tool.routing" class="detail-kv">
        <span class="detail-key">Routing Key</span>
        <span class="detail-val"><code>{{ tool.routing }}</code></span>
      </div>
      <div v-if="tool.chain && tool.chain.length" class="detail-kv">
        <span class="detail-key">Chain</span>
        <span class="detail-val chain-items">
          <template v-for="(up, i) in tool.chain" :key="`${up}-${i}`">
            <span class="chain-arrow" v-if="i > 0">→</span>
            <span class="chain-item" :class="{ primary: i === 0 }">{{ up }}</span>
          </template>
        </span>
      </div>
    </section>

    <section v-if="tool.params && tool.params.length" class="detail-section">
      <h4 class="section-title">参数</h4>
      <div class="params-list">
        <template v-for="(p, i) in tool.params" :key="i">
          <code v-if="typeof p === 'string' && !isParamPlaceholder(p)" class="param-item">
            {{ p }}
          </code>
          <code v-else-if="isParamPlaceholder(p)" class="param-item param-placeholder" title="YAML 旧写法中的占位符">?</code>
          <div v-else-if="asInfo(p)" class="param-item param-object">
            <code>{{ asInfo(p)?.name }}</code>
            <el-tag size="small" effect="plain">{{ asInfo(p)?.type || 'any' }}</el-tag>
            <el-tag
              size="small"
              :type="asInfo(p)?.required ? 'danger' : 'info'"
              effect="plain"
            >
              {{ asInfo(p)?.required ? 'required' : 'optional' }}
            </el-tag>
            <el-tag
              v-for="ev in asInfo(p)?.enum || []"
              :key="ev"
              size="small"
              type="warning"
              effect="plain"
            >
              enum: {{ ev }}
            </el-tag>
            <p v-if="asInfo(p)?.description" class="param-desc">{{ asInfo(p)?.description }}</p>
          </div>
        </template>
      </div>
    </section>

    <section v-if="tool.source" class="detail-section">
      <h4 class="section-title">数据源</h4>
      <div class="detail-kv">
        <span class="detail-key">Source</span>
        <span class="detail-val">{{ tool.source }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.detail-body {
  padding: 4px 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.danger-banner {
  margin-top: 4px;
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.section-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.detail-kv {
  display: flex;
  gap: 12px;
  font-size: 13px;
  align-items: baseline;
}

.detail-key {
  flex: 0 0 90px;
  color: var(--el-text-color-secondary);
}

.detail-val {
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.detail-val code,
.param-item code {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 12px;
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 4px;
}

.chain-items {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.chain-arrow {
  color: var(--el-text-color-placeholder);
  margin: 0 2px;
}

.chain-item {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--el-fill-color);
  color: var(--el-text-color-regular);
}

.chain-item.primary {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}

.params-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.param-object {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.param-placeholder {
  color: var(--el-text-color-placeholder);
  background: transparent;
  border: 1px dashed var(--el-border-color);
}

.param-desc {
  flex: 1 1 100%;
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
