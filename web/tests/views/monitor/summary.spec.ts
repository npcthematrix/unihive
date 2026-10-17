import { describe, expect, test } from 'vitest'
import { deriveGatewaySummary, type SummaryItem } from '@/views/monitor/summary'
import type { GatewayStatus, UpstreamStatus, UpstreamStatusValue } from '@/api/status'
import type { RuntimeStats } from '@/api/runtime'

function makeStatus(statuses: UpstreamStatusValue[]): GatewayStatus {
  const upstreams: Record<string, UpstreamStatus> = {}
  statuses.forEach((status, i) => {
    upstreams[`u${i}`] = {
      enabled: status !== 'disabled',
      type: 'http',
      description: '',
      status,
      latency_ms: null,
      last_error: null,
    }
  })
  return {
    timestamp: 1,
    pid: 1,
    summary: {
      total: statuses.length,
      online: 0,
      degraded: 0,
      offline: 0,
      configured: 0,
      disabled: 0,
    },
    upstreams,
    cache: { enabled: true, timestamp: 1 },
  }
}

const runtime: RuntimeStats = {
  pid: 1,
  uptime_seconds: 10,
  active_requests: 3,
  tool_count: 10,
  calls_last_1m: 1,
  calls_last_5m: 42,
  timestamp: 1,
}

function pick(items: SummaryItem[], label: string): SummaryItem {
  const found = items.find((i) => i.label === label)
  if (!found) throw new Error(`missing summary item: ${label}`)
  return found
}

describe('deriveGatewaySummary — 整体状态口径', () => {
  test('全部在线: good + 整体健康', () => {
    const items = deriveGatewaySummary(makeStatus(['online', 'online']), runtime)
    const overall = pick(items, '整体状态')
    expect(overall.value).toBe('整体健康')
    expect(overall.tone).toBe('good')
    expect(overall.dot).toBe(true)
  })

  test('configured 与 online 同视为可用, 不降级', () => {
    const items = deriveGatewaySummary(makeStatus(['online', 'configured']), runtime)
    expect(pick(items, '整体状态').tone).toBe('good')
    expect(pick(items, '可用上游').value).toBe('2 / 2')
  })

  test('offline → bad', () => {
    const items = deriveGatewaySummary(makeStatus(['online', 'offline']), runtime)
    expect(pick(items, '整体状态').tone).toBe('bad')
    expect(pick(items, '整体状态').value).toBe('1 个上游异常')
  })

  test('degraded → warn', () => {
    const items = deriveGatewaySummary(makeStatus(['online', 'degraded']), runtime)
    expect(pick(items, '整体状态').tone).toBe('warn')
    expect(pick(items, '整体状态').value).toBe('1 个上游降级')
  })

  test('回归: unknown 是启动收敛中 → warn, 旧码误配绿色 good', () => {
    const items = deriveGatewaySummary(makeStatus(['online', 'unknown']), runtime)
    const overall = pick(items, '整体状态')
    expect(overall.tone).toBe('warn')
    expect(overall.value).toBe('1 个未知 (启动中)')
  })

  test('offline 优先级高于 degraded / unknown', () => {
    const items = deriveGatewaySummary(
      makeStatus(['offline', 'degraded', 'unknown']),
      runtime,
    )
    expect(pick(items, '整体状态').tone).toBe('bad')
  })

  test('回归: 全部被主动禁用 → warn, 不能报「整体健康」', () => {
    const items = deriveGatewaySummary(makeStatus(['disabled', 'disabled']), runtime)
    const overall = pick(items, '整体状态')
    expect(overall.tone).toBe('warn')
    expect(overall.value).toBe('全部 2 个上游已禁用')
    expect(pick(items, '可用上游').value).toBe('0 / 0')
  })

  test('无上游 (首屏未加载): 保持 good, 可用 0 / 0', () => {
    const items = deriveGatewaySummary(null, null)
    expect(pick(items, '整体状态').tone).toBe('good')
    expect(pick(items, '可用上游').value).toBe('0 / 0')
  })
})

describe('deriveGatewaySummary — 可用上游分母口径', () => {
  test('回归: disabled 不计入分母 (旧码 10 里 7 禁用会显示 3 / 10)', () => {
    const items = deriveGatewaySummary(
      makeStatus(['online', 'online', 'configured', 'disabled', 'disabled']),
      runtime,
    )
    expect(pick(items, '可用上游').value).toBe('3 / 3')
  })

  test('可用数为 0 但存在启用上游 → 可用上游 tone 为 warn', () => {
    const items = deriveGatewaySummary(makeStatus(['offline', 'offline']), runtime)
    const usable = pick(items, '可用上游')
    expect(usable.value).toBe('0 / 2')
    expect(usable.tone).toBe('warn')
  })

  test('正常情况可用上游 tone 为 good', () => {
    const items = deriveGatewaySummary(makeStatus(['online', 'disabled']), runtime)
    const usable = pick(items, '可用上游')
    expect(usable.value).toBe('1 / 1')
    expect(usable.tone).toBe('good')
  })
})

describe('deriveGatewaySummary — 运行态指标', () => {
  test('在途请求 / 近 5 分钟调用 读 runtime', () => {
    const items = deriveGatewaySummary(makeStatus(['online']), runtime)
    expect(pick(items, '在途请求').value).toBe('3')
    expect(pick(items, '近 5 分钟调用').value).toBe('42')
  })

  test('runtime 缺失回退 0', () => {
    const items = deriveGatewaySummary(makeStatus(['online']), null)
    expect(pick(items, '在途请求').value).toBe('0')
    expect(pick(items, '近 5 分钟调用').value).toBe('0')
  })
})
