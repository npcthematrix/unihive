/**
 * GuidesView 子组件纯函数 + 常量。
 * 不依赖 Vue, 便于复用与单测。
 */

/** 2 个 subtab 的 key 常量 */
export const TAB_KEYS = ['human', 'ai'] as const
export type GuidesTabKey = (typeof TAB_KEYS)[number]

/** subtab 展示标题 */
export const TAB_TITLES: Record<GuidesTabKey, string> = {
  human: '人工接入',
  ai: 'AI 接入',
}