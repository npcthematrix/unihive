/**
 * 滚动监听 — 接入指南页两个面板共享的 sticky nav 高亮逻辑。
 *
 * 抽出以便脱离 plugin-vue 直接单测, 也避免在两个人类/AI 面板里复制
 * IntersectionObserver + rootMargin + 排序取首个可见的实现细节。
 *
 * rootMargin: '-20% 0px -70% 0px' — 视口顶部 20% 内才算可见, 底部 70%
 * 留白让"上一个滚走"的节停止被选中。
 */
import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

export interface ScrollSpySection {
  id: string
  title: string
}

export interface UseScrollSpyResult {
  /** 当前高亮的 section id。 */
  activeId: Ref<string>
  /** 点击 nav 项时滚到对应位置。 */
  scrollTo(id: string): void
}

export function useScrollSpy(sections: readonly ScrollSpySection[]): UseScrollSpyResult {
  const initialId = sections[0]?.id ?? ''
  const activeId = ref<string>(initialId)

  function scrollTo(id: string): void {
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    activeId.value = id
  }

  let observer: IntersectionObserver | null = null

  function setup(): void {
    const elements = sections
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => Boolean(el))
    if (elements.length === 0) return

    observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) =>
              a.target.getBoundingClientRect().top -
              b.target.getBoundingClientRect().top,
          )
        if (visible.length > 0 && visible[0].target.id) {
          activeId.value = visible[0].target.id
        }
      },
      { rootMargin: '-20% 0px -70% 0px', threshold: 0 },
    )
    elements.forEach((el) => observer!.observe(el))
  }

  function teardown(): void {
    observer?.disconnect()
    observer = null
  }

  onMounted(() => {
    setup()
  })

  onBeforeUnmount(() => {
    teardown()
  })

  // sections 改变时重挂 observer (跨 subtab 切换不需要, 但保持通用性)
  watch(
    () => sections,
    () => {
      teardown()
      setup()
    },
  )

  return { activeId, scrollTo }
}
