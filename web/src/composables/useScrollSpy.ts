/**
 * 滚动监听 — 接入指南页的章节导航高亮。
 *
 * 2026-10-17 审视后重构:
 * - sections 改为 `MaybeRefOrGetter`, 切子标签时章节集合变化能真正触发重挂。
 *   旧实现 `watch(() => sections, ...)` 里传的是模块级静态数组, 永不触发 (死代码)。
 * - 「多节可见时选哪个」抽到 `@/utils/scrollSpy` 的 pickActiveSection, 可单测。
 * - scrollTo 增加平滑滚动期间的高亮抑制: 否则滚动途中经过的章节会把高亮
 *   一格格抢走, 点完「故障排查」标题先闪回中间几节再落位。
 * - 尊重 prefers-reduced-motion。
 *
 * rootMargin: '-20% 0px -70% 0px' — 视口顶部 20% 内才算可见, 底部 70%
 * 留白让「上一个滚走」的节停止被选中。
 */
import { onBeforeUnmount, onMounted, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'
import { pickActiveSection, type ScrollSpySection } from '@/utils/scrollSpy'

export type { ScrollSpySection }

export interface UseScrollSpyResult {
  /** 当前高亮的 section id。 */
  activeId: Ref<string>
  /** 点击 nav 项时滚到对应位置。 */
  scrollTo(id: string): void
}

/** 平滑滚动大致结束的时间窗, 期间忽略 observer 回报。 */
const SCROLL_SETTLE_MS = 600

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function useScrollSpy(
  sections: MaybeRefOrGetter<readonly ScrollSpySection[]>,
): UseScrollSpyResult {
  const activeId = ref<string>(toValue(sections)[0]?.id ?? '')

  let observer: IntersectionObserver | null = null
  let suppressUntil = 0

  function scrollTo(id: string): void {
    const el = document.getElementById(id)
    if (!el) return
    activeId.value = id
    suppressUntil = Date.now() + SCROLL_SETTLE_MS
    el.scrollIntoView({
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      block: 'start',
    })
  }

  function teardown(): void {
    observer?.disconnect()
    observer = null
  }

  function setup(): void {
    teardown()
    if (typeof IntersectionObserver === 'undefined') return
    const elements = toValue(sections)
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => Boolean(el))
    if (elements.length === 0) return

    observer = new IntersectionObserver(
      (entries) => {
        // 程序化滚动进行中: 不回写高亮, 避免途中章节抢焦点
        if (Date.now() < suppressUntil) return
        const next = pickActiveSection(
          entries
            .filter((e) => e.isIntersecting && e.target.id)
            .map((e) => ({ id: e.target.id, top: e.target.getBoundingClientRect().top })),
        )
        if (next) activeId.value = next
      },
      { rootMargin: '-20% 0px -70% 0px', threshold: 0 },
    )
    elements.forEach((el) => observer!.observe(el))
  }

  onMounted(setup)
  onBeforeUnmount(teardown)

  // 章节集合变化 (切换子标签) → 重挂 observer, 并把高亮复位到首节。
  // 依赖写成 id 拼接串: 即便调用方传的是稳定引用的常量数组, 内容变化也能感知。
  watch(
    () => toValue(sections).map((s) => s.id).join('|'),
    () => {
      activeId.value = toValue(sections)[0]?.id ?? ''
      setup()
    },
  )

  return { activeId, scrollTo }
}
