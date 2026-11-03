/** 控制台共享时间常量 (供 网关运行监控 等页面复用, 避免重复定义)。 */

/** 数据陈旧阈值 (sec) — 超过此时长视为陈旧, UI 标 stale + tab 切回自动重拉。 */
export const STALE_THRESHOLD_SEC = 30

/** 轮询间隔 (ms) — autoRefresh 开启后多久打一次 API。 */
export const POLL_INTERVAL_MS = 10_000

/** 轮询间隔 (sec) — 文案唯一来源; 旧码在 tooltip / 缓存卡里各写死一个 "10 秒",
 *  改 POLL_INTERVAL_MS 后那两处会静默漂移。 */
export const POLL_INTERVAL_SEC = Math.round(POLL_INTERVAL_MS / 1000)

/** 「并发」标签的快速采样间隔 (ms) — 历史K线突发 <1s, 10s 粗粒度抓不到。
 *  与主轮询同受页头「自动刷新」开关和 document.hidden 约束。 */
export const FAST_POLL_INTERVAL_MS = 2_500