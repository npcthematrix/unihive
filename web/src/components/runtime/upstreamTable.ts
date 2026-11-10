/**
 * 上游总览表的纯逻辑 —— 从 UpstreamOverviewPanel.vue 抽出来以便测试。
 *
 * 本仓 vitest.config.ts 刻意不引 @vitejs/plugin-vue (插件版本不兼容), 所以
 * .vue 文件在 spec 里根本 import 不了; SFC 里的 <script setup> 逻辑没有测试
 * 通路。约定做法是把纯函数落到普通模块 (先例: views/monitor/summary.ts)。
 */
import type { UpstreamRow, UpstreamStatusValue } from '@/api/status'

/** 展示用状态一律以 enabled 为准 (H3)。
 *
 * 后端 _do_probe 只对非 HTTP 类型回 'disabled', http / http_jsonrpc 分支不看
 * enabled, 照样探活并写 online/degraded/offline —— 配置里关掉的 HTTP 上游于是
 * 顶着绿色「在线」徽标, 而同屏汇总卡算它「已禁用」、过滤片又把它归进「异常」。
 */
export function rowStatus(row: Pick<UpstreamRow, 'enabled' | 'status'>): UpstreamStatusValue {
  return row.enabled ? row.status : 'disabled'
}

/** 状态列排序权重: 健康在前, 故障与未决在后。 */
export const STATUS_ORDER: Record<string, number> = {
  online: 0,
  degraded: 1,
  configured: 2,
  offline: 3,
  disabled: 4,
  unknown: 5,
}

/**
 * 上游 type 的展示名 (T-B4)。
 *
 * 词汇与 views/config/upstreamEdit.ts 的 KNOWN_UPSTREAM_TYPES 对齐 —— 那里是
 * 后端 config_loader 接受的 type 全集, 配置页的 select 也用它。旧码这里只有
 * http/stdio/npx 三项, 而 stdio 根本不在后端词汇里 (死键), 真实在用的
 * mootdx2 / tdx_quant / omni / thsdk 全部落到兜底分支,
 * 于是「类型」列直接把 snake_case 原始 id 打在徽标里, 又长又撑破列宽。
 * 标签要短 (列宽 88px), 原始 id 由 tag 的 title 悬停取回。
 */
export const TYPE_TEXT: Record<string, string> = {
  http: 'HTTP',
  http_jsonrpc: 'JSON-RPC',
  mootdx2: 'MooTDX2',
  tdx_quant: 'TDX 量化',
  omni: 'OMNI 库',
  thsdk: 'THSDK',
  python: 'Python',
  npx: 'NPX',
}

/** 类型标签文本; 未收录的 type 原样透出 (前向兼容后端新增 type)。 */
export function typeText(type: string): string {
  return TYPE_TEXT[type] || type || '-'
}

export type UpstreamSortKey = 'name' | 'status' | 'latency_ms'

/**
 * 表格排序。key 为 null = 复位到后端配置顺序 (T-M2)。
 *
 * T-L1: latency_ms 为 null 表示「未测量」(进程内组件无网络往返, 或该类型不发
 * 探测)。旧码把它折成 -1, 一是把「本地」与「未探测」两种语义混成一个数,
 * 二是 -1 比真实的 0ms 还小 → 升序时未测量的行排在最快的那条前面, 读者会以为
 * 它们延迟最低。未测量恒排末尾, 且不随排序方向翻转 (它不是数值, 不参与比较)。
 */
export function sortUpstreamRows(
  rows: UpstreamRow[],
  key: UpstreamSortKey | null,
  asc: boolean,
): UpstreamRow[] {
  if (!key) return rows
  const dir = asc ? 1 : -1
  return [...rows].sort((a, b) => {
    if (key === 'latency_ms') {
      if (a.latency_ms === null || b.latency_ms === null) {
        if (a.latency_ms === b.latency_ms) return 0
        return a.latency_ms === null ? 1 : -1
      }
      return (a.latency_ms - b.latency_ms) * dir
    }
    const va: string | number =
      key === 'name' ? a.name : STATUS_ORDER[rowStatus(a)] ?? 99
    const vb: string | number =
      key === 'name' ? b.name : STATUS_ORDER[rowStatus(b)] ?? 99
    if (va < vb) return -1 * dir
    if (va > vb) return 1 * dir
    return 0
  })
}

/**
 * 表格空态文案 (T-M1)。
 *
 * 旧码只有两态: 有行就是「暂无匹配的上游」, 没行就是「暂无上游数据 (状态未
 * 加载或未配置上游)」—— 把「正在加载」「加载失败」「真的一个上游都没配」压成
 * 同一句话, 读者无法判断该等、该重试、还是该去配置页加上游。
 */
export function upstreamEmptyText(input: {
  rowCount: number
  filteredCount: number
  loading: boolean
  everLoaded: boolean
}): string {
  const { rowCount, filteredCount, loading, everLoaded } = input
  if (rowCount && !filteredCount) return '暂无匹配的上游'
  if (loading) return '正在加载上游状态…'
  if (!everLoaded) return '尚未取到上游状态 (首次轮询在路上, 或刷新失败 — 见上方提示)'
  return '未配置任何上游'
}
