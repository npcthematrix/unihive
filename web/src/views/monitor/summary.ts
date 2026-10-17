import type { GatewayStatus } from '@/api/status'
import type { RuntimeStats } from '@/api/runtime'

export interface SummaryItem {
  label: string
  value: string
  tone?: 'good' | 'warn' | 'bad'
  dot?: boolean
}

/**
 * 「网关进程」标签的总体摘要: 由上游状态分布 + 运行态派生 4 个指标。
 *
 * 口径 (2026-10-17 修正):
 * - 可用上游 = online + configured。configured 已启用、只是该类型不发探测, 同样可用;
 *   已禁用是用户主动关闭 —— 不计入分子, **也不计入分母**。旧码分母含 disabled,
 *   10 个里 7 个被主动关掉会显示成「3 / 10」, 看着像大面积故障。
 * - overallTone: offline → bad; degraded / unknown → warn (unknown 是启动收敛中,
 *   不是「健康」, 旧码误配绿色 good); 全部被主动禁用 → warn (可用数为 0,
 *   不能报「整体健康」)。
 */
export function deriveGatewaySummary(
  status: GatewayStatus | null,
  runtime: RuntimeStats | null,
): SummaryItem[] {
  const entries = Object.values(status?.upstreams ?? {})
  const count = (s: string): number => entries.filter((u) => u.status === s).length
  const online = count('online')
  const configured = count('configured')
  const problem = count('offline')
  const degraded = count('degraded')
  const unknown = count('unknown')
  const disabled = count('disabled')

  const usable = online + configured
  const enabledTotal = entries.length - disabled

  let overallTone: 'good' | 'warn' | 'bad' = 'good'
  let overallText = '整体健康'
  if (problem > 0) {
    overallTone = 'bad'
    overallText = `${problem} 个上游异常`
  } else if (degraded > 0) {
    overallTone = 'warn'
    overallText = `${degraded} 个上游降级`
  } else if (unknown > 0) {
    overallTone = 'warn'
    overallText = `${unknown} 个未知 (启动中)`
  } else if (usable === 0 && disabled > 0) {
    overallTone = 'warn'
    overallText = `全部 ${disabled} 个上游已禁用`
  }

  return [
    { label: '整体状态', value: overallText, tone: overallTone, dot: true },
    {
      label: '可用上游',
      value: `${usable} / ${enabledTotal}`,
      tone: usable === 0 && enabledTotal > 0 ? 'warn' : 'good',
    },
    { label: '在途请求', value: String(runtime?.active_requests ?? 0) },
    { label: '近 5 分钟调用', value: String(runtime?.calls_last_5m ?? 0) },
  ]
}
