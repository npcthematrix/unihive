/**
 * 切子标签时把页面滚回顶部。
 *
 * 多标签页共用同一个滚动容器: 在「上游」长表格滚到一半切到「缓存」, 新面板
 * 会从同一滚动位置开始渲染 —— 用户看到的是新标签的中段, 而不是它的标题。
 * 四个带标签的页面 (配置信息 / OMNI / 网关运行监控 / 接入指南) 行为必须一致。
 *
 * 滚动容器是页面内部的 .view-body (el-main 自身 overflow:hidden, 不再滚),
 * 见 styles/view-header.css。保留 el-main / window 的兜底: 登录页等不经过
 * DefaultLayout 的页面没有 .view-body, 将来若谁改回主区滚动也不会静默失效。
 */
export function scrollPageTop(): void {
  if (typeof window === 'undefined') return
  document.querySelector<HTMLElement>('.view-body')?.scrollTo?.({ top: 0 })
  document.getElementById('main-content')?.scrollTo?.({ top: 0 })
  window.scrollTo({ top: 0, behavior: 'auto' })
}
