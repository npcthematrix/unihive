/**
 * 滚动高亮的纯决策逻辑 —— 不依赖 Vue / DOM, 便于直接单测。
 *
 * happy-dom 虽然提供了 IntersectionObserver, 但观测回调的时机不可控;
 * 把「多个章节同时可见时该高亮哪个」抽出来测, 比挂载组件稳得多。
 */

export interface ScrollSpySection {
  id: string
  title: string
}

/** 候选章节: id + 相对视口顶部的距离 (getBoundingClientRect().top)。 */
export interface SectionCandidate {
  id: string
  top: number
}

/**
 * 从当前可见的章节里选出应高亮的一个: **取最靠上的一节**。
 *
 * 与旧内联实现一致 (原先在 IntersectionObserver 回调里 filter + sort 取首个),
 * 改成单次遍历; 空 id 视为无效候选 (防止锚点未设置时把高亮清成空串)。
 */
export function pickActiveSection(visible: readonly SectionCandidate[]): string | null {
  let best: SectionCandidate | null = null
  for (const item of visible) {
    if (!item.id) continue
    if (best === null || item.top < best.top) best = item
  }
  return best ? best.id : null
}
