/**
 * FuyaoCachePanel 纯函数 (不依赖 Vue, 被 vitest 直接覆盖)。
 *
 * 组件只持有 reactive 状态与渲染; TTL 秒数的展示文案、snapshot vs working
 * 的差异台账、输入归一化、配置拍平都沉在这里。spec 必须 import 真实实现,
 * 不要在测试里复刻 (2026-09 第五轮审视 H2: 复刻版仍按已删除的
 * defaults/overrides 模型断言, 与扁平 tools 模型漂移)。
 */

/** tool 名 → TTL 秒数; key 不存在 = 未设置, 走全局 cache.ttl。 */
export type FuyaoToolsMap = Record<string, number>

export interface FuyaoTtlDiff {
  /** 配置实体段内的 JSON path, 如 tools.get_foo。 */
  path: string
  beforeText: string
  afterText: string
}

/** 秒数 → 人类可读时长; 0 = 实时不缓存。 */
export function formatSeconds(s: number): string {
  if (s === 0) return '实时 / 不缓存'
  if (s < 60) return `${s} 秒`
  if (s < 3600) {
    const m = s / 60
    return `${Number.isInteger(m) ? m : m.toFixed(1)} 分钟`
  }
  if (s < 86400) {
    const h = s / 3600
    return `${Number.isInteger(h) ? h : h.toFixed(1)} 小时`
  }
  const d = s / 86400
  return `${Number.isInteger(d) ? d : d.toFixed(1)} 天`
}

/**
 * snapshot (已保存值) vs working (草稿) 的差异台账。
 *
 * 三种情形: 未设置→设值 / 设值→清空 / 值变化; 两边相同不出条目。
 * 条目顺序 = snapshot/working key 首次出现的并集顺序 (插入序),
 * 调用方依赖此顺序渲染, 不要排序。
 */
export function buildFuyaoTtlDiffs(
  snapshot: FuyaoToolsMap,
  working: FuyaoToolsMap,
): FuyaoTtlDiff[] {
  const all = new Set<string>([...Object.keys(snapshot), ...Object.keys(working)])
  const out: FuyaoTtlDiff[] = []
  for (const tool of all) {
    const before = snapshot[tool]
    const after = working[tool]
    if (before === undefined && after !== undefined) {
      out.push({
        path: `tools.${tool}`,
        beforeText: '(未设置, 走全局)',
        afterText: `${after}s (${formatSeconds(after)})`,
      })
    } else if (before !== undefined && after === undefined) {
      out.push({
        path: `tools.${tool}`,
        beforeText: `${before}s (${formatSeconds(before)})`,
        afterText: '(已清空, 回落全局 cache.ttl)',
      })
    } else if (before !== undefined && after !== undefined && before !== after) {
      out.push({
        path: `tools.${tool}`,
        beforeText: `${before}s (${formatSeconds(before)})`,
        afterText: `${after}s (${formatSeconds(after)})`,
      })
    }
  }
  return out
}

/**
 * el-input-number 输入 → TTL 秒数。
 * undefined / null / NaN / Infinity / 负数一律视为"清空 override"
 * (返回 undefined); 有限非负数向下取整 (含 0 = 实时)。
 */
export function normalizeTtlSeconds(
  v: number | undefined | null,
): number | undefined {
  if (v === undefined || v === null || Number.isNaN(v)) return undefined
  if (!Number.isFinite(v) || v < 0) return undefined
  return Math.floor(v)
}

/**
 * 把服务端 FuyaoCacheConfig.tools 拍平成快照字典:
 * 只收非负有限数 (防御后端/手改 YAML 的脏值, 与保存门同口径)。
 */
export function fuyaoToolsSnapshot(
  tools: Record<string, unknown> | undefined,
): FuyaoToolsMap {
  const out: FuyaoToolsMap = {}
  for (const [k, v] of Object.entries(tools || {})) {
    if (typeof v === 'number' && Number.isFinite(v) && v >= 0) out[k] = v
  }
  return out
}
