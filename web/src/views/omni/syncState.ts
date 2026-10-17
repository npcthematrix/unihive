/**
 * SyncPanel 的同步状态机 —— 纯函数, 不依赖 Vue, 便于单测。
 *
 * 背景 (2026-10-17 审视 HIGH-1): 用户点「中止」只断开浏览器侧的 POST,
 * 后端 `_omni_sync_lock` 仍持锁最长 SYNC_TIMEOUT_SECONDS(300s)。旧实现里
 * POST 抛 cancel → finally 置 syncing=false → watch(syncing) 立刻 stopPolling(),
 * 轮询被杀死, UI 显示「空闲」而任务实际在跑 —— 用户再点「立即同步」会被后端
 * fast-fail 拒绝。这里把状态显式化, 让「已中止等待」成为一个独立相位,
 * 由轮询而非 POST 响应来收敛。
 */

/** 与后端 STALE_RUNNING_MINUTES 同阈值: 超过则认为 running 记录已僵死。 */
export const STALE_RUNNING_MINUTES = 10

/**
 * running 是否仍在有效期内 (超过 STALE_RUNNING_MINUTES 视为僵死记录,
 * 不据此判定「后端正在同步」)。
 *
 * 后端存的是 UTC 'YYYY-MM-DD HH:MM:SS' (无 Z/T), 需显式补 Z 再解析。
 */
export function isRecentRunning(
  log: { status?: string; start_time?: string } | null | undefined,
  now: number = Date.now(),
): boolean {
  if (!log || log.status !== 'running' || !log.start_time) return false
  const started = new Date(log.start_time.replace(' ', 'T') + 'Z').getTime()
  return Number.isFinite(started) && now - started < STALE_RUNNING_MINUTES * 60 * 1000
}

/**
 * 同步面板的四个相位:
 * - `idle`             空闲, 没有同步在进行
 * - `running`          本面板发起的 POST 在飞, 可由用户「中止」
 * - `waiting-cancel`   用户已中止等待, 后端任务仍在收尾, 由轮询收敛
 * - `waiting-external` 检测到后端已有同步 (其他页面发起 / 页面重进), 无自有 POST
 */
export type SyncPhase = 'idle' | 'running' | 'waiting-cancel' | 'waiting-external'

export function resolveSyncPhase(
  syncing: boolean,
  externalRun: boolean,
  canceled: boolean,
): SyncPhase {
  if (!syncing) return 'idle'
  if (canceled) return 'waiting-cancel'
  return externalRun ? 'waiting-external' : 'running'
}

/** 只有「本面板发起的 POST 在飞」才允许中止 —— 接管/等待态没有可断的请求。 */
export function canCancelSync(phase: SyncPhase): boolean {
  return phase === 'running'
}

/** 是否仍需要保持 4s 轮询 (idle 之外都要, 否则 UI 无法收敛)。 */
export function shouldPoll(phase: SyncPhase): boolean {
  return phase !== 'idle'
}

/**
 * 控制区提示文案。timeoutMin 对应后端 SYNC_TIMEOUT_SECONDS, pollSec 对应
 * POLL_INTERVAL_MS —— 由调用方传入, 避免这里再抄一份常量。
 */
export function syncHint(phase: SyncPhase, timeoutMin: number, pollSec: number): string {
  switch (phase) {
    case 'running':
      return (
        `同步可能持续数十秒, 请勿关闭页面; 可点「中止」停止等待` +
        ` (后端任务会在 ${timeoutMin} 分钟超时内自行结束)。`
      )
    case 'waiting-cancel':
      return (
        `已停止等待。后端任务仍在收尾, 最长 ${timeoutMin} 分钟;` +
        `界面每 ${pollSec} 秒自动刷新, 结束后会给出结果。`
      )
    case 'waiting-external':
      return (
        `检测到后端已有同步任务在运行 (可能由其他页面发起);` +
        `界面每 ${pollSec} 秒自动刷新, 结束后会给出结果。`
      )
    default:
      return ''
  }
}
