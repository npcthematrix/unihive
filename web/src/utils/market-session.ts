/**
 * 上海市场时段判定 (纯前端, 与后端 cache_strategy.intraday_session_ttl
 * 的边界语义保持一致):
 *
 *   周末      休市, 短档缓存到次日零点 (跨周末逐日续期)
 *   盘前      00:00–9:15 / 9:25–9:30, 短档缓存到 9:30 开盘
 *   集合竞价  9:15–9:25, 通用短档仍缓存到 9:30; 竞价快照走 60s 档
 *             (cache_strategy.auction_snapshot_ttl), 9:25 定格
 *   上午盘    9:30–11:30, 短档按配置值生效
 *   午间休市  11:30–13:00, 短档缓存到 13:00 开盘
 *   下午盘    13:00–15:00, 短档按配置值生效
 *   收盘后    15:00–24:00, 短档缓存到次日零点; 龙虎榜等盘后榜单另有
 *             18:00 发布边界 (cache_strategy M9)
 *
 * 不区分法定节假日 (与后端一致, 代价仅是节假日多几次无害重拉)。
 * 时间一律按 UTC+8 显式换算, 不依赖浏览器所在时区。
 */

export type MarketPhase =
  | 'weekend'
  | 'pre_open'
  | 'auction'
  | 'morning'
  | 'lunch'
  | 'afternoon'
  | 'closed'

export interface MarketSessionInfo {
  phase: MarketPhase
  /** 时段名, 如「上午盘」 */
  label: string
  /** 钟点范围, 如「9:30–11:30」 */
  range: string
  /** 短档是否在该时段按配置值生效 (仅连续竞价两盘) */
  shortTtlActive: boolean
  /** 下一变化边界 (盘内为该盘收盘) 的钟点标签 */
  boundaryLabel: string
  /** 距边界秒数 (≥0) */
  secondsToBoundary: number
}

const SHANGHAI_OFFSET_MS = 8 * 3600_000

/** 用上海墙历分量构造对应绝对时刻 (UTC+8 → UTC)。 */
function shanghaiWallTime(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
): Date {
  return new Date(
    Date.UTC(year, month, day, hour, minute, 0) - SHANGHAI_OFFSET_MS,
  )
}

export function marketSession(now: Date = new Date()): MarketSessionInfo {
  // 上海墙钟: 把 UTC 时刻 +8h 后按 UTC 分量读取
  const wall = new Date(now.getTime() + SHANGHAI_OFFSET_MS)
  const year = wall.getUTCFullYear()
  const month = wall.getUTCMonth()
  const day = wall.getUTCDate()
  const dow = wall.getUTCDay()
  const hour = wall.getUTCHours()
  const minute = wall.getUTCMinutes()

  const midnight = shanghaiWallTime(year, month, day, 0, 0)
  const at = (h: number, m: number): Date =>
    shanghaiWallTime(year, month, day, h, m)
  const nextMidnight = new Date(midnight.getTime() + 24 * 3600_000)

  const secondsTo = (target: Date): number =>
    Math.max(0, Math.round((target.getTime() - now.getTime()) / 1000))

  // JS: 0=周日 … 6=周六
  if (dow === 0 || dow === 6) {
    return {
      phase: 'weekend',
      label: '周末休市',
      range: '周六 / 周日',
      shortTtlActive: false,
      boundaryLabel: '次日零点',
      secondsToBoundary: secondsTo(nextMidnight),
    }
  }
  if (
    hour < 9
    || (hour === 9 && minute < 15)
    || (hour === 9 && minute >= 25 && minute < 30)
  ) {
    return {
      phase: 'pre_open',
      label: '盘前',
      range: '00:00–9:30',
      shortTtlActive: false,
      boundaryLabel: '9:30 开盘',
      secondsToBoundary: secondsTo(at(9, 30)),
    }
  }
  if (hour === 9 && minute < 25) {
    // 9:15–9:25 集合竞价: 通用短档语义不变 (9:30 前数据会变, 缓存到 9:30);
    // 仅竞价快照工具特殊 — 竞价进行中走 60s 档, 由时段条注释另行说明。
    return {
      phase: 'auction',
      label: '集合竞价',
      range: '9:15–9:25',
      shortTtlActive: false,
      boundaryLabel: '9:30 开盘',
      secondsToBoundary: secondsTo(at(9, 30)),
    }
  }
  if (hour < 11 || (hour === 11 && minute < 30)) {
    return {
      phase: 'morning',
      label: '上午盘',
      range: '9:30–11:30',
      shortTtlActive: true,
      boundaryLabel: '11:30 午休',
      secondsToBoundary: secondsTo(at(11, 30)),
    }
  }
  if (hour < 13) {
    return {
      phase: 'lunch',
      label: '午间休市',
      range: '11:30–13:00',
      shortTtlActive: false,
      boundaryLabel: '13:00 开盘',
      secondsToBoundary: secondsTo(at(13, 0)),
    }
  }
  if (hour < 15) {
    return {
      phase: 'afternoon',
      label: '下午盘',
      range: '13:00–15:00',
      shortTtlActive: true,
      boundaryLabel: '15:00 收盘',
      secondsToBoundary: secondsTo(at(15, 0)),
    }
  }
  return {
    phase: 'closed',
    label: '收盘后',
    range: '15:00–24:00',
    shortTtlActive: false,
    boundaryLabel: '次日零点',
    secondsToBoundary: secondsTo(nextMidnight),
  }
}

/** 倒计时格式化: 「2h05m」 / 「12m30s」 / 「45s」。 */
export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds))
  const h = Math.floor(safe / 3600)
  const m = Math.floor((safe % 3600) / 60)
  const s = safe % 60
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`
  if (m > 0) return `${m}m${String(s).padStart(2, '0')}s`
  return `${s}s`
}
