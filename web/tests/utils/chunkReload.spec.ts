import { describe, expect, it, vi } from 'vitest'

/**
 * Round-13 簇M: 发布新版后 finalize-build 会删旧 hash chunk。
 * 用户标签页里的旧 HTML 再做懒加载导航时, 动态 import 拿到 404 ——
 * 必须识别这类 chunk 加载失败并自动拉取新 HTML (location.reload),
 * 同时用冷却时间戳防止 "新 HTML 也是坏的" 时无限刷新循环。
 */
import {
  CHUNK_RELOAD_COOLDOWN_MS,
  CHUNK_RELOAD_TS_KEY,
  installChunkReloadGuard,
  isChunkLoadError,
  reloadForStaleChunk,
  shouldReloadNow,
} from '@/utils/chunkReload'

describe('isChunkLoadError', () => {
  it('Chrome/Edge 动态 import 404 文案 → true', () => {
    expect(
      isChunkLoadError(
        new Error(
          'Failed to fetch dynamically imported module: http://127.0.0.1:18080/static/console/assets/StatusView-abc.js',
        ),
      ),
    ).toBe(true)
  })

  it('Firefox 动态 import 失败文案 → true', () => {
    expect(isChunkLoadError(new Error('error loading dynamically imported module'))).toBe(true)
  })

  it('Safari module script 失败文案 → true', () => {
    expect(isChunkLoadError(new Error('Importing a module script failed.'))).toBe(true)
  })

  it('ChunkLoadError name → true (webpack-style / 包装错误)', () => {
    const e = Object.assign(new Error('boom'), { name: 'ChunkLoadError' })
    expect(isChunkLoadError(e)).toBe(true)
  })

  it('普通错误 (401/网络错误/500/取消) → false', () => {
    expect(isChunkLoadError(new Error('Network Error'))).toBe(false)
    expect(isChunkLoadError(new Error('Request failed with status code 500'))).toBe(false)
    expect(isChunkLoadError(new Error('unauthorized'))).toBe(false)
    const canceled = Object.assign(new Error('canceled'), { name: 'CanceledError' })
    expect(isChunkLoadError(canceled)).toBe(false)
  })

  it('null/undefined/字符串/无 message 对象 → false (不抛)', () => {
    expect(isChunkLoadError(null)).toBe(false)
    expect(isChunkLoadError(undefined)).toBe(false)
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false)
    expect(isChunkLoadError({})).toBe(false)
  })
})

describe('shouldReloadNow — 刷新冷却判定', () => {
  it('从未刷新过 (null) → 允许', () => {
    expect(shouldReloadNow(null, 1_000_000)).toBe(true)
  })

  it('冷却期内 → 拒绝 (防无限刷新循环)', () => {
    expect(shouldReloadNow(1_000_000, 1_000_000 + CHUNK_RELOAD_COOLDOWN_MS - 1)).toBe(false)
  })

  it('冷却期边界 (恰好等于) → 允许', () => {
    expect(shouldReloadNow(1_000_000, 1_000_000 + CHUNK_RELOAD_COOLDOWN_MS)).toBe(true)
  })

  it('超过冷却期 → 允许', () => {
    expect(shouldReloadNow(1_000_000, 1_000_000 + CHUNK_RELOAD_COOLDOWN_MS + 1)).toBe(true)
  })
})

describe('reloadForStaleChunk', () => {
  function makeDeps(initial?: string | null, now = 2_000_000) {
    const store = new Map<string, string>(
      initial === undefined ? [] : [[CHUNK_RELOAD_TS_KEY, initial]],
    )
    const reload = vi.fn()
    const deps = {
      now: vi.fn(() => now),
      storage: {
        getItem: vi.fn((key: string) => (store.has(key) ? store.get(key)! : null)),
        setItem: vi.fn((key: string, value: string) => void store.set(key, value)),
      },
      reload,
    }
    return { deps, store, reload }
  }

  it('首次: 写时间戳并 reload, 返回 true', () => {
    const { deps, store, reload } = makeDeps(null)

    expect(reloadForStaleChunk(deps)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(store.get(CHUNK_RELOAD_TS_KEY)).toBe('2000000')
  })

  it('冷却期内第二次: 不 reload, 返回 false', () => {
    const first = makeDeps(null, 2_000_000)
    reloadForStaleChunk(first.deps)

    const second = makeDeps(first.store.get(CHUNK_RELOAD_TS_KEY) ?? null, 2_000_005)
    expect(reloadForStaleChunk(second.deps)).toBe(false)
    expect(second.reload).not.toHaveBeenCalled()
  })

  it('时间戳已过冷却期: 再次 reload 并刷新时间戳', () => {
    const { deps, store, reload } = makeDeps(String(2_000_000), 2_000_000 + CHUNK_RELOAD_COOLDOWN_MS + 5)

    expect(reloadForStaleChunk(deps)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(store.get(CHUNK_RELOAD_TS_KEY)).toBe(String(2_000_000 + CHUNK_RELOAD_COOLDOWN_MS + 5))
  })

  it('时间戳损坏 (非数字) → 按首次处理, 允许 reload', () => {
    const { deps, reload } = makeDeps('not-a-number')
    expect(reloadForStaleChunk(deps)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('storage.getItem 抛 SecurityError (cookie 被禁) → 不炸, 仍 reload', () => {
    const reload = vi.fn()
    const deps = {
      now: () => 2_000_000,
      reload,
      storage: {
        getItem: vi.fn(() => {
          throw new DOMException('storage blocked', 'SecurityError')
        }),
        setItem: vi.fn(),
      },
    }
    expect(reloadForStaleChunk(deps)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('storage.setItem 抛错 (Safari 隐私模式) → 时间戳尽力而为, 仍 reload', () => {
    const reload = vi.fn()
    const deps = {
      now: () => 2_000_000,
      reload,
      storage: {
        getItem: vi.fn(() => null),
        setItem: vi.fn(() => {
          throw new DOMException('storage blocked', 'SecurityError')
        }),
      },
    }
    expect(reloadForStaleChunk(deps)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('storage 不可用时用进程内时间戳保冷却: 连续第二次仍被抑制', () => {
    const reload = vi.fn()
    const deps = {
      // 起始时间远离同文件其他用例, 避开模块级内存时间戳跨用例残留
      now: (() => {
        let t = 9_000_000
        return () => t++
      })(),
      reload,
      storage: {
        getItem: vi.fn(() => {
          throw new DOMException('blocked', 'SecurityError')
        }),
        setItem: vi.fn(() => {
          throw new DOMException('blocked', 'SecurityError')
        }),
      },
    }
    expect(reloadForStaleChunk(deps)).toBe(true)
    expect(reloadForStaleChunk(deps)).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe('installChunkReloadGuard', () => {
  // 每个用例独立的 storage —— router.onError 用例与 window 事件用例
  // 共享真实 sessionStorage 时, 冷却时间戳会互相抑制。
  function setup() {
    const store = new Map<string, string>()
    const onErrorHandlers: Array<(error: unknown) => unknown> = []
    // 模拟 vue-router: onError 返回的注销器负责把 handler 从内部列表摘掉
    const offError = vi.fn()
    const fakeRouter = {
      onError: vi.fn((h: (error: unknown) => unknown) => {
        onErrorHandlers.push(h)
        return () => {
          const idx = onErrorHandlers.indexOf(h)
          if (idx >= 0) onErrorHandlers.splice(idx, 1)
          offError()
        }
      }),
    }
    const reload = vi.fn()
    const uninstall = installChunkReloadGuard(fakeRouter, {
      now: () => 5_000_000,
      reload,
      storage: {
        getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
        setItem: (key: string, value: string) => void store.set(key, value),
      },
    })
    return { fakeRouter, onErrorHandlers, reload, uninstall, offError }
  }

  it('注册 router.onError 且返回卸载函数', () => {
    const { fakeRouter, uninstall } = setup()
    expect(fakeRouter.onError).toHaveBeenCalledTimes(1)
    expect(typeof uninstall).toBe('function')
  })

  it('router.onError 收到 chunk 加载错误 → reload', () => {
    const { onErrorHandlers, reload } = setup()
    onErrorHandlers[0](new Error('Failed to fetch dynamically imported module: x'))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('router.onError 收到普通错误 → 不 reload', () => {
    const { onErrorHandlers, reload } = setup()
    onErrorHandlers[0](new Error('boom'))
    expect(reload).not.toHaveBeenCalled()
  })

  it('window vite:preloadError 事件 → preventDefault 并 reload', () => {
    const { reload } = setup()
    const event = new Event('vite:preloadError', { cancelable: true })
    window.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('冷却期内重复 vite:preloadError 只 reload 一次, 第二次不 preventDefault (放行 vite 抛错中止导航)', () => {
    const { reload } = setup()
    const first = new Event('vite:preloadError', { cancelable: true })
    window.dispatchEvent(first)
    const second = new Event('vite:preloadError', { cancelable: true })
    window.dispatchEvent(second)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(first.defaultPrevented).toBe(true)
    // 抑制刷新时不能吞掉 vite 的错误, 否则 __vitePreload resolve undefined → 空白路由
    expect(second.defaultPrevented).toBe(false)
  })

  it('uninstall 同时移除 window 监听与 router.onError 处理函数', () => {
    const { reload, uninstall, onErrorHandlers, offError } = setup()
    uninstall()
    window.dispatchEvent(new Event('vite:preloadError', { cancelable: true }))
    expect(reload).not.toHaveBeenCalled()
    expect(offError).toHaveBeenCalledTimes(1)
    // 注销器已把 handler 从 router 内部列表摘掉: router 再 emit 同类错误无人接收
    expect(onErrorHandlers).toHaveLength(0)
    for (const h of onErrorHandlers) {
      h(new Error('Failed to fetch dynamically imported module: x'))
    }
    expect(reload).not.toHaveBeenCalled()
  })
})
