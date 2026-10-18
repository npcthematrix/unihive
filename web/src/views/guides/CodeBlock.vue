<template>
  <div class="code-block">
    <div class="code-block-bar">
      <el-button
        size="small"
        :icon="CopyDocument"
        class="copy-btn"
        :loading="copying"
        @click="onCopy"
      >{{ copied ? '已复制' : '复制' }}</el-button>
    </div>
    <pre><code>{{ code }}</code></pre>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { copyText } from '@/utils/clipboard'

const props = defineProps<{
  /** 要展示的代码片段 (原样保留空白和换行)。 */
  code: string
}>()

const COPY_FEEDBACK_MS = 1500

const copying = ref(false)
const copied = ref(false)
let resetTimer: ReturnType<typeof setTimeout> | null = null

function clearResetTimer(): void {
  if (resetTimer !== null) {
    clearTimeout(resetTimer)
    resetTimer = null
  }
}

onBeforeUnmount(clearResetTimer)

async function onCopy() {
  copying.value = true
  try {
    await copyText(props.code, '')
    // 成功只留按钮内的「已复制」—— 原先还叠一个 ElMessage.success,
    // 同一件事两处反馈, 对视觉用户是噪音。
    copied.value = true
    clearResetTimer()
    resetTimer = setTimeout(() => {
      copied.value = false
      resetTimer = null
    }, COPY_FEEDBACK_MS)
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

/* 按钮独立工具条而非浮在 pre 上: 短片段与横向滚动时代码都不会被遮挡 */
.code-block-bar {
  display: flex;
  justify-content: flex-end;
  padding: 6px 6px 0;
}

.code-block pre {
  margin: 0;
  padding: 8px 14px 14px 16px;
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
    padding: 6px 12px 10px;
    font-size: 12px;
  }
}
</style>
