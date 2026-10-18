<!--
  配置页三个面板 (Upstreams / Tools / Mapping) 共用的搜索框。

  统一为 Element Plus 的 el-input: 与「MCP APIs」「MCP 浏览器」的搜索框是同一个
  控件 —— 同一套放大镜图标、清除按钮、聚焦外环。此前这里是手写的 <input> +
  自绘 "×", 三个页面的搜索框长得不一样, 而且重复实现了 EP 已有的清除逻辑。

  组件 API 保持不变 (modelValue / placeholder / ariaLabel), 三个面板无需改动。
  宽度由 .cfg-search-wrap (全局) 与父级 flex 控制。
-->
<template>
  <div class="cfg-search-wrap">
    <el-input
      :model-value="modelValue"
      :placeholder="placeholder"
      :aria-label="ariaLabel ?? placeholder"
      :prefix-icon="Search"
      autocomplete="off"
      clearable
      @update:model-value="onInput"
    />
  </div>
</template>

<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'

interface Props {
  modelValue: string
  placeholder: string
  ariaLabel?: string
}
defineProps<Props>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(value: string): void {
  emit('update:modelValue', value)
}
</script>
