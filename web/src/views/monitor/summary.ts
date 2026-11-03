import type { GatewayStatus } from '@/api/status'
import { isUsableUpstream } from '@/api/status'
import type { RuntimeStats } from '@/api/runtime'
import { formatUptime } from '@/utils/number'

export interface SummaryItem {
  label: string
  value: string
  /** 数值单位后缀 (次 / 个), 渲染成小号次级灰字。 */
  unit?: string
  tone?: 'good' | 'warn' | 'bad' | 'busy'
  dot?: boolean
}

/** 数据没拿到时的占位: 与「读到 0」区分开。 */
const UNKNOWN = '—'

/**
 * /api/runtime 六格: PID / 运行时长 / 在途 / 近 1 分 / 近 5 分 / 工具数。
 * runtime 缺失一律 '—' (旧码用 0, 同屏出现 "0" 和 "—" 并存, 用户无法区分
 * "无请求" 与 "未加载")。
 */
function runtimeTiles(runtime: RuntimeStats | null): SummaryItem[] {
  const active = runtime?.active_requests ?? 0
  return [
    { label: '网关进程', value: runtime ? `PID ${runtime.pid}` : UNKNOWN },
    {
      label: '运行时长',
      value:
        runtime?.uptime_seconds == null ? UNKNOWN : formatUptime(runtime.uptime_seconds),
    },
    {
      label: '在途请求',
      value: runtime ? String(active) : UNKNOWN,
      tone: runtime && active > 0 ? 'busy' : undefined,
    },
    {
      label: '近 1 分钟调用',
      value: runtime ? String(runtime.calls_last_1m ?? 0) : UNKNOWN,
      unit: '次',
    },
    {
      label: '近 5 分钟调用',
      value: runtime ? String(runtime.calls_last_5m ?? 0) : UNKNOWN,
      unit: '次',
    },
    { label: 'MCP 工具', value: runtime ? String(runtime.tool_count ?? 0) : UNKNOWN },
  ]
}

/**
 * 「网关进程」标签的全部指标 tile: 上游健康 (2) + 运行态计数 (6)。
 *
 * 口径 (2026-11 修正):
 * - **启用与否以 `enabled` 为准, 不信 `status === 'disabled'`**。「可用」判定统一
 *   走 isUsableUpstream (api/status.ts), 与「上游总览」标签同一口径 —— 后端
 *   get_upstream_status 对有运行时 client 的上游会用 client 状态桶覆盖 `status`
 *   (已禁用的上游照样可能带 online / offline), 旧码按 status 挑禁用, 于是关掉的
 *   上游既可能混进分母、被算成「可用」, 也可能在它探测失败时把面板报成
 *   「1 个上游异常」。
 * - 可用上游 = isUsableUpstream。configured 已启用、只是该类型不发探测, 同样
 *   可用; 已禁用不计入分子也**不计入分母** —— 10 个里 7 个被主动关掉不该显示成
 *   「3 / 10」那样像大面积故障。
 * - 整体状态: offline → bad; degraded / unknown → warn (unknown 是启动收敛中,
 *   不是「健康」); 全部被主动禁用 → warn; 真配置了零个上游 → 中性说明。
 * - H1: `status === null` (首屏未落地或 /api/status 失败) **不是健康**。旧码把
 *   null 和空 upstreams 压成同一个空数组, 直接渲染成绿色「整体健康 0 / 0」,
 *   与同屏页头的红色「刷新失败」互相打脸。
 */
export function deriveGatewaySummary(
  status: GatewayStatus | null,
  runtime: RuntimeStats | null,
): SummaryItem[] {
  if (!status) {
    return [
      { label: '整体状态', value: '未加载', dot: true },
      { label: '可用上游', value: UNKNOWN },
      ...runtimeTiles(runtime),
    ]
  }

  const entries = Object.values(status.upstreams ?? {})
  const enabled = entries.filter((u) => u.enabled)
  const disabled = entries.length - enabled.length
  const count = (s: string): number => enabled.filter((u) => u.status === s).length
  const problem = count('offline')
  const degraded = count('degraded')
  const unknown = count('unknown')

  const usable = enabled.filter(isUsableUpstream).length
  const enabledTotal = enabled.length

  let overallTone: SummaryItem['tone'] = 'good'
  let overallText = '整体健康'
  if (!entries.length) {
    overallTone = undefined
    overallText = '未配置上游'
  } else if (problem > 0) {
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

  // M5: 只要 usable>0 就报绿, 会让「1 个上游异常」(红) 旁边挂着「3 / 4」(绿)。
  // 满员才 good, 有缺口就 warn, 全禁用 (分母 0) 用中性色 —— 不是好消息也不是。
  const usableTone: SummaryItem['tone'] =
    enabledTotal === 0 ? undefined : usable === enabledTotal ? 'good' : 'warn'

  return [
    { label: '整体状态', value: overallText, tone: overallTone, dot: true },
    { label: '可用上游', value: `${usable} / ${enabledTotal}`, tone: usableTone },
    ...runtimeTiles(runtime),
  ]
}
