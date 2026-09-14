<template>
  <div class="code-block">
    <el-button
      size="small"
      :icon="CopyDocument"
      class="copy-btn"
      :loading="copying"
      @click="onCopy"
    >{{ copied ? '已复制' : '复制' }}</el-button>
    <pre><code>{{ code }}</code></pre>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { copyText } from '@/utils/clipboard'

const props = defineProps<{
  /** 要展示的代码片段 (原样保留空白和换行)。 */
  code: string
}>()

const copying = ref(false)
const copied = ref(false)

async function onCopy() {
  copying.value = true
  try {
    await copyText(props.code, '')
    copied.value = true
    ElMessage.success('已复制')
    setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    ElMessage.error('复制失败, 请手动复制')
  } finally {
    copying.value = false
  }
}
</script>

<style scoped>
.code-block {
  position: relative;
  margin: 12px 0;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.code-block .copy-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
}

.code-block pre {
  margin: 0;
  padding: 14px 14px 14px 16px;
  overflow-x: auto;
  font-family: var(--el-font-family-monospace, 'Consolas', 'Monaco', monospace);
  font-size: 13px;
  line-height: 1.55;
  color: var(--el-text-color-regular);
  white-space: pre;
}

.code-block code {
  font-family: inherit;
}

/* 窄屏(< 480px) 压缩 padding + 字号, 避免横向占满整屏 */
@media (max-width: 480px) {
  .code-block pre {
    padding: 10px 12px;
    font-size: 12px;
  }
  .code-block .copy-btn {
    top: 4px;
    right: 4px;
  }
}
</style>