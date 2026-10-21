import { afterEach, describe, expect, it, vi } from 'vitest'
import { scrollPageTop } from '@/utils/scrollTop'

describe('scrollPageTop', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('优先归位页面内的 .view-body (真正的滚动容器)', () => {
    const body = document.createElement('div')
    body.className = 'view-body'
    const bodyScroll = vi.fn()
    body.scrollTo = bodyScroll as unknown as typeof body.scrollTo
    document.body.appendChild(body)

    const winScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    scrollPageTop()

    expect(bodyScroll).toHaveBeenCalledWith({ top: 0 })
    expect(winScroll).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
  })

  it('同时兜底滚动 el-main 与 window (不依赖谁承担滚动)', () => {
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
