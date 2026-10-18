import { describe, expect, it } from 'vitest'
import { MENU } from '@/constants/menu'
import { router } from '@/router'

/**
 * 侧栏菜单 ↔ 路由的一致性。
 *
 * el-menu 的 default-active 按 index 字符串精确匹配当前 route.path, 菜单项
 * 的 path 一旦与路由真实 path 不一致, 该项就永远不高亮 —— 点击时 URL 会被
 * 重定向成真实路径, 看起来"点了没反应", 但页面其实跳了。这是曾经真实发生
 * 过的静默故障 (菜单 /l1-cache, 路由 /gateway-monitor)。
 */
describe('侧栏菜单', () => {
  const byPath = new Map(router.getRoutes().map((r) => [r.path, r]))

  it('每一项都对应一条真实路由, 而不是 redirect 占位', () => {
    for (const item of MENU) {
      const record = byPath.get(item.path)
      expect(record, `菜单项 ${item.path} 没有对应路由`).toBeTruthy()
      expect(
        record?.redirect,
        `菜单项 ${item.path} 指向的是一条重定向, 高亮永远对不上`,
      ).toBeFalsy()
    }
  })

  it('每一项都能解析出已命名的路由', () => {
    for (const item of MENU) {
      expect(router.resolve(item.path).name, `菜单项 ${item.path} 解析失败`).toBeTruthy()
    }
  })

  it('path 与标题均不重复', () => {
    expect(new Set(MENU.map((m) => m.path)).size).toBe(MENU.length)
    expect(new Set(MENU.map((m) => m.title)).size).toBe(MENU.length)
  })
})
