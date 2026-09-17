/** 控制台共享时间常量 (避免 StatusView/L1CacheView 重复定义)。 */

/** 数据陈旧阈值 (sec) — 超过此时长视为陈旧, UI 标 stale + tab 切回自动重拉。 */
export const STALE_THRESHOLD_SEC = 30

/** 轮询间隔 (ms) — autoRefresh 开启后多久打一次 API。 */
export const POLL_INTERVAL_MS = 10_000