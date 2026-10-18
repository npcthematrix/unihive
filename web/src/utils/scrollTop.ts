/**
 * 切子标签时把页面滚回顶部。
 *
 * 多标签页共用同一个滚动容器: 在「上游」长表格滚到一半切到「缓存」, 新面板
 * 会从同一滚动位置开始渲染 —— 用户看到的是新标签的中段, 而不是它的标题。
 * 三个带标签的页面 (配置信息 / OMNI / 接入指南) 行为必须一致。
 *
 * el-main 本身没设 overflow (滚动发生在 window), 但两条都调: 无论将来谁
 * 承担滚动都不会失效。
 */
export function scrollPageTop(): void {
  if (typeof window === 'undefined') return
  document.getElementById('main-content')?.scrollTo?.({ top: 0 })
  window.scrollTo({ top: 0, behavior: 'auto' })
}
