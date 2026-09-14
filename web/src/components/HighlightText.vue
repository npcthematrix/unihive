<script setup lang="ts">
/**
 * 关键词高亮 — 替代旧版 highlight() 的 innerHTML + escapeHtml 方案。
 * 这里全部走文本节点切分, 查询词仅包进 <mark>, 不存在 HTML 注入面。
 */
import { computed } from 'vue'
import { escapeRegExp } from '@/utils/text'

const props = defineProps<{
  text: string | null | undefined
  query: string
}>()

interface Segment {
  value: string
  match: boolean
}

const segments = computed<Segment[]>(() => {
  const text = props.text ?? ''
  const query = props.query.trim()
  if (!query) return [{ value: text, match: false }]
  let re: RegExp
  try {
    re = new RegExp(`(${escapeRegExp(query)})`, 'gi')
  } catch {
    return [{ value: text, match: false }]
  }
  return text
    .split(re)
    .filter((s) => s !== '')
    .map((value) => ({ value, match: value.toLowerCase() === query.toLowerCase() }))
})
</script>

<template>
  <template v-for="(seg, i) in segments" :key="i">
    <mark v-if="seg.match" class="hl">{{ seg.value }}</mark>
    <template v-else>{{ seg.value }}</template>
  </template>
</template>

<style scoped>
.hl {
  background: var(--el-color-warning-light-7, #faecd8);
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
}
</style>
