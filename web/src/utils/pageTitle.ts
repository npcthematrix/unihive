/**
 * 浏览器标签标题。
 *
 * 每条路由都声明了 meta.title, 但此前无人消费 —— 六个页面的标签标题恒为
 * "UniHive Console", 多开标签 / 历史记录里分不出来。落标题的逻辑抽成纯函数,
 * 便于单测 (router.afterEach 里只剩一行调用)。
 */

export const APP_TITLE = 'UniHive Console'

/**
 * 由路由 meta.title 生成浏览器标签标题。
 * 无标题 (登录页以外理论上都有, 但重定向中间态可能缺失) 时退回应用名,
 * 不产生 "undefined · UniHive Console" 这类脏标题。
 */
export function pageTitle(metaTitle: unknown): string {
  const title = typeof metaTitle === 'string' ? metaTitle.trim() : ''
  return title ? `${title} · ${APP_TITLE}` : APP_TITLE
}
