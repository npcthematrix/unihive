<script setup lang="ts">
/**
 * 展开行: 原始 tool 对象的详情。
 *
 * 这里展示的是上游发什么就显示什么 —— 不改写、不补默认值。JSON 一律走
 * {{ }} 插值进 <pre>, 绝不使用 v-html: payload 完全由上游控制, 插值到文本
 * 节点是唯一没有注入面的做法 (同 HighlightText.vue 的立场)。
 */
import { computed, ref } from 'vue'
import type { RawTool } from '@/api/mcp-browser'
import { copyText } from '@/utils/clipboard'
import {
  annotationFlags,
  extraKeys,
  formatBytes,
  formatToolJson,
  hasMeta,
  hasOutputSchema,
  jsonBytes,
  schemaProperties,
  toolTitle,
} from './tools'

/** 超过这个体积就先给个占位, 点开才渲染 —— 否则一个 40KB 的 inputSchema
 *  会生成几千行 <pre>, 卡住滚动且没法选中文本。 */
const REVEAL_THRESHOLD_BYTES = 64 * 1024

const props = defineProps<{ tool: RawTool }>()

const revealed = ref(false)

const params = computed(() => schemaProperties(props.tool))
const flags = computed(() => annotationFlags(props.tool))
const unknownKeys = computed(() => extraKeys(props.tool))
const jsonText = computed(() => formatToolJson(props.tool))
const jsonSize = computed(() => jsonBytes(jsonText.value))
const tooLarge = computed(() => jsonSize.value > REVEAL_THRESHOLD_BYTES)

const metaText = computed(() =>
  props.tool._meta === undefined ? '' : JSON.stringify(props.tool._meta, null, 2),
)
const hasOutput = computed(() => hasOutputSchema(props.tool))
const hasMetaField = computed(() => hasMeta(props.tool))

async function copyJson(): Promise<void> {
  await copyText(jsonText.value, '已复制原始 JSON')
}
</script>

<template>
  <div class="raw-panel">
    <div v-if="toolTitle(tool)" class="raw-title">{{ toolTitle(tool) }}</div>

    <!-- 参数字段 (从 inputSchema.properties 只读投影) -->
    <section v-if="params.length" class="raw-section">
      <div class="raw-section-title">inputSchema.properties</div>
      <div class="raw-params">
        <div v-for="p in params" :key="p.name" class="raw-param">
          <span class="raw-param-name">{{ p.name }}</span>
          <span v-if="p.type" class="raw-param-type">{{ p.type }}</span>
          <el-tag v-if="p.required" size="small" type="danger" effect="plain">required</el-tag>
          <span v-if="p.enumValues.length" class="raw-param-enum">
            enum: {{ p.enumValues.join(' | ') }}
          </span>
          <div v-if="p.description" class="raw-param-desc">{{ p.description }}</div>
        </div>
      </div>
    </section>
    <div v-else class="raw-section raw-muted">该工具没有声明参数</div>

    <!-- annotations (MCP 提示; 未知 key 也会带出来) -->
    <section v-if="flags.length" class="raw-section">
      <div class="raw-section-title">annotations</div>
      <div class="raw-flags">
        <el-tag
          v-for="f in flags"
          :key="f.key"
          size="small"
          effect="plain"
          :type="f.value ? 'success' : 'info'"
        >
          {{ f.label }}: {{ f.value }}
        </el-tag>
      </div>
    </section>

    <section v-if="hasOutput" class="raw-section">
      <div class="raw-section-title">outputSchema</div>
      <div class="raw-muted">该上游声明了 outputSchema (见下方原始 JSON)</div>
    </section>

    <section v-if="hasMetaField" class="raw-section">
      <div class="raw-section-title">_meta</div>
      <pre class="raw-json raw-json-small">{{ metaText }}</pre>
    </section>

    <section v-if="unknownKeys.length" class="raw-section">
      <div class="raw-section-title">界面未专门渲染的字段</div>
      <div class="raw-muted">
        这些字段没有单独的展示位, 但都在下面的原始 JSON 里:
        <code>{{ unknownKeys.join(', ') }}</code>
      </div>
    </section>

    <!-- 原始 JSON: 上游返回什么就是什么 -->
    <section class="raw-section">
      <div class="raw-section-head">
        <span class="raw-section-title">
          原始 JSON
          <span class="raw-muted-inline">(原样返回, 未做任何改写 · {{ formatBytes(jsonSize) }})</span>
        </span>
        <el-button size="small" @click="copyJson">复制 JSON</el-button>
      </div>

      <template v-if="!tooLarge || revealed">
        <pre class="raw-json">{{ jsonText }}</pre>
        <el-button v-if="tooLarge" size="small" text @click="revealed = false">
          收起
        </el-button>
      </template>
      <div v-else class="raw-large">
        <div class="raw-muted">
          该工具 payload 较大 ({{ formatBytes(jsonSize) }}), 默认不展开渲染。
        </div>
        <el-button size="small" @click="revealed = true">展开原始 JSON</el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.raw-panel {
  padding: 4px 12px 12px;
}

.raw-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.raw-section {
  margin-bottom: 12px;
}

.raw-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.raw-section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.raw-muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.raw-muted-inline {
  font-weight: 400;
  color: var(--el-text-color-placeholder);
}

.raw-params {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.raw-param {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.raw-param-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--el-color-primary);
}

.raw-param-type {
  font-size: 11px;
  color: var(--el-color-success);
}

.raw-param-enum {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.raw-param-desc {
  flex-basis: 100%;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.raw-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.raw-json {
  margin: 0;
  padding: 10px 12px;
  max-height: 420px;
  overflow: auto;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre;
}

.raw-json-small {
  max-height: 180px;
}

.raw-large {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--el-color-primary);
}
</style>
