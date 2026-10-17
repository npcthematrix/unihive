<!--
  统一配置页 (UpstreamsPanel / ToolsPanel / MappingPanel) 的搜索框。
  视觉与 .cfg-search-wrap / .cfg-search / .cfg-search-clear 全局样式绑定,
  本地无 scoped CSS。父级可用 flex 容器通过 .ups-toolbar .cfg-search-wrap { flex:1 }
  控制宽度; 默认 max-width 由全局样式提供。
-->
<template>
  <div class="cfg-search-wrap">
    <input
      :value="modelValue"
      type="text"
      class="cfg-search"
      :placeholder="placeholder"
      :aria-label="ariaLabel ?? placeholder"
      @input="onInput"
    >
    <button
      v-if="modelValue"
      type="button"
      class="cfg-search-clear"
      title="清空"
      aria-label="清空搜索"
      @click="onClear"
    >×</button>
  </div>
</template>

<script setup lang="ts">
interface Props {
  modelValue: string
  placeholder: string
  ariaLabel?: string
}
defineProps<Props>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(e: Event): void {
  const target = e.target as HTMLInputElement
  emit('update:modelValue', target.value)
}

function onClear(): void {
  emit('update:modelValue', '')
}
</script>