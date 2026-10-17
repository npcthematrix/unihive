import { describe, expect, test } from 'vitest'
import {
  STALE_RUNNING_MINUTES,
  canCancelSync,
  isRecentRunning,
  resolveSyncPhase,
  shouldPoll,
  syncHint,
  type SyncPhase,
} from '@/views/omni/syncState'

/** 生成一个「now 之前 minutesAgo 分钟」的 UTC 存储格式时间串。 */
function utcAgo(minutesAgo: number, now: number): string {
  const d = new Date(now - minutesAgo * 60 * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
  )
}

describe('isRecentRunning', () => {
  const NOW = Date.UTC(2026, 9, 17, 12, 0, 0)

  test('空值 / 非 running 一律 false', () => {
    expect(isRecentRunning(null, NOW)).toBe(false)
    expect(isRecentRunning(undefined, NOW)).toBe(false)
    expect(isRecentRunning({ status: 'success', start_time: utcAgo(1, NOW) }, NOW)).toBe(false)
    expect(isRecentRunning({ status: 'running' }, NOW)).toBe(false)
  })

  test('running 且在阈值内 → true', () => {
    expect(isRecentRunning({ status: 'running', start_time: utcAgo(1, NOW) }, NOW)).toBe(true)
    expect(
      isRecentRunning({ status: 'running', start_time: utcAgo(STALE_RUNNING_MINUTES - 1, NOW) }, NOW),
    ).toBe(true)
  })

  test('running 但超过阈值 → false（僵死记录不接管）', () => {
    expect(
      isRecentRunning({ status: 'running', start_time: utcAgo(STALE_RUNNING_MINUTES + 1, NOW) }, NOW),
    ).toBe(false)
    expect(
      isRecentRunning({ status: 'running', start_time: utcAgo(STALE_RUNNING_MINUTES, NOW) }, NOW),
    ).toBe(false)
  })

  test('时间串不可解析 → false（不误接管）', () => {
    expect(isRecentRunning({ status: 'running', start_time: 'not-a-date' }, NOW)).toBe(false)
  })

  test('按 UTC 解析: 时区不影响判定', () => {
    // 同一 UTC 时刻, 换成本地时区字符串会得到不同结果; 这里验证走的是 UTC 分支
    const log = { status: 'running', start_time: utcAgo(2, NOW) }
    expect(isRecentRunning(log, NOW)).toBe(true)
    expect(isRecentRunning(log, NOW + 3 * 60 * 1000)).toBe(true)
    expect(isRecentRunning(log, NOW + 5 * 60 * 1000)).toBe(true)
  })
})

describe('resolveSyncPhase', () => {
  test('syncing=false → idle（无论 externalRun/canceled）', () => {
    expect(resolveSyncPhase(false, false, false)).toBe('idle')
    expect(resolveSyncPhase(false, true, true)).toBe('idle')
  })

  test('canceled 优先于 externalRun —— 中止等待与外部接管文案不同', () => {
    expect(resolveSyncPhase(true, true, true)).toBe('waiting-cancel')
    expect(resolveSyncPhase(true, true, false)).toBe('waiting-external')
  })

  test('syncing 且无 externalRun/canceled → running', () => {
    expect(resolveSyncPhase(true, false, false)).toBe('running')
  })
})

describe('canCancelSync', () => {
  test('仅 running 相位可中止', () => {
    expect(canCancelSync('running')).toBe(true)
    expect(canCancelSync('idle')).toBe(false)
    expect(canCancelSync('waiting-cancel')).toBe(false)
    expect(canCancelSync('waiting-external')).toBe(false)
  })
})

describe('shouldPoll', () => {
  test('idle 之外都要保持轮询, 否则 UI 无法收敛', () => {
    expect(shouldPoll('idle')).toBe(false)
    expect(shouldPoll('running')).toBe(true)
    expect(shouldPoll('waiting-cancel')).toBe(true)
    expect(shouldPoll('waiting-external')).toBe(true)
  })
})

describe('syncHint', () => {
  const phases: SyncPhase[] = ['idle', 'running', 'waiting-cancel', 'waiting-external']

  test('idle 无提示', () => {
    expect(syncHint('idle', 5, 4)).toBe('')
  })

  test('非 idle 相位都给出含超时/轮询信息的文案', () => {
    expect(syncHint('running', 5, 4)).toContain('5 分钟')
    expect(syncHint('running', 5, 4)).toContain('中止')
    expect(syncHint('waiting-cancel', 5, 4)).toContain('已停止等待')
    expect(syncHint('waiting-cancel', 5, 4)).toContain('4 秒')
    expect(syncHint('waiting-external', 5, 4)).toContain('其他页面')
    for (const p of phases) {
      if (p === 'idle') continue
      expect(syncHint(p, 5, 4).length).toBeGreaterThan(0)
    }
  })
})
