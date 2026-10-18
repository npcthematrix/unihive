import { afterEach, describe, expect, it, vi } from 'vitest'
import { scrollPageTop } from '@/utils/scrollTop'

describe('scrollPageTop', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('同时滚动主内容容器与 window (两条都调, 不依赖谁承担滚动)', () => {
    const main = document.createElement('main')
    main.id = 'main-content'
    const mainScroll = vi.fn()
    main.scrollTo = mainScroll as unknown as typeof main.scrollTo
    document.body.appendChild(main)

    const winScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    scrollPageTop()

    expect(mainScroll).toHaveBeenCalledWith({ top: 0 })
    expect(winScroll).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
  })

  it('容器不存在时不抛错, window 仍然归位', () => {
    const winScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    expect(() => scrollPageTop()).not.toThrow()
    expect(winScroll).toHaveBeenCalledTimes(1)
  })
})
