/**
 * 接入指南页的常量与纯函数。
 *
 * 章节定义放在这里而不是各面板内: 「章节导航」(GuidesView) 与「章节正文」
 * (两个面板) 必须读同一份 id/title —— 原先两处各写一遍, id 一旦漂移,
 * 锚点会静默失效 (点了没反应, 也没有任何报错)。
 */

export const GUIDE_TAB_KEYS = ['human', 'ai'] as const
export type GuideTabKey = (typeof GUIDE_TAB_KEYS)[number]

export const GUIDE_TAB_TITLES: Record<GuideTabKey, string> = {
  human: '人工接入',
  ai: 'AI 接入',
}

export interface GuideSection {
  id: string
  /** 章节序号 (从 1 起)。标题与目录都从它派生, 不再把 "1. " 写进 title ——
   *  否则中间插一节, 后面所有序号都得手工重排。
   *  只到 h2 这一层: 小节 (h3) 一律不带序号。h3 的序号没有地方能派生
   *  (正文里 h3 与段落交替出现, 没法按下标算), 只能写字面量, 一旦上面的
   *  章节增删就整体错位 —— 正是 h2 曾经踩过的坑, 干脆不再引入。 */
  num: number
  title: string
}

/** 人工接入 — 章节顺序即导航顺序。 */
export const HUMAN_SECTIONS: readonly GuideSection[] = [
  { id: 'sec-quickstart', num: 1, title: '快速开始' },
  { id: 'sec-architecture', num: 2, title: '数据源与路由' },
  { id: 'sec-interfaces', num: 3, title: 'MCP APIs' },
  { id: 'sec-troubleshoot', num: 4, title: '故障排查' },
]

/** AI 接入 — 章节顺序即导航顺序。 */
export const AI_SECTIONS: readonly GuideSection[] = [
  { id: 'sec-ai-intro', num: 1, title: '简介' },
  { id: 'sec-ai-http', num: 2, title: 'HTTP 模式' },
  { id: 'sec-ai-stdio', num: 3, title: 'STDIO 模式' },
  { id: 'sec-ai-prompt', num: 4, title: '系统提示词' },
  { id: 'sec-ai-verify', num: 5, title: '验证接入' },
  { id: 'sec-ai-port', num: 6, title: '自定义端口' },
]

const SECTIONS_BY_TAB: Record<GuideTabKey, readonly GuideSection[]> = {
  human: HUMAN_SECTIONS,
  ai: AI_SECTIONS,
}

/** 取某个子标签的章节表; 未知 key 回落到人工接入 (与路由 fallback 一致)。 */
export function sectionsForTab(tab: GuideTabKey): readonly GuideSection[] {
  return SECTIONS_BY_TAB[tab] ?? HUMAN_SECTIONS
}
