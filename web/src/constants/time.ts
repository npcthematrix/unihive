/** 控制台共享时间常量 (供 网关运行监控 等页面复用, 避免重复定义)。 */

/** 数据陈旧阈值 (sec) — 超过此时长视为陈旧, UI 标 stale + tab 切回自动重拉。 */
export const STALE_THRESHOLD_SEC = 30

/** 轮询间隔 (ms) — autoRefresh 开启后多久打一次 API。 */
export const POLL_INTERVAL_MS = 10_000