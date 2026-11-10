import { http } from './client'

/**
 * 上游原样返回的 tool 对象。
 *
 * 刻意不做类型收窄 —— 页面要展示的就是原始 payload。不同上游发出来的形状
 * 并不一致 (有的上游带 title/annotations/_meta, fuyao 带 outputSchema),
 * 用封闭 interface 描述它等于对外宣称我们知道所有上游的字段, 那是假的。
 */
export type RawTool = Record<string, unknown>

/** 一个 "自身即 MCP server" 的上游 */
export interface McpBrowserUpstream {
  name: string
  /** Python 侧 client 类名, 例如 FuyaoClient / ThsLocalClient */
  type: string
  status: string
  available: boolean
}

export interface McpBrowserUpstreamsResponse {
  timestamp: number
  upstreams: McpBrowserUpstream[]
}

export interface RawToolsResponse {
  upstream: string
  timestamp: number
  ok: boolean
  error: string | null
  errorKind: string | null
  /** 实际拉取的页数 (上游分页拉取时 > 1) */
  pages: number
  truncated: boolean
  truncatedReason: string | null
  count: number
  /** 上游响应体累计字节数 */
  bytes: number
  tools: RawTool[]
}

/** GET /api/mcp-browser/upstreams — 列出所有 MCP 上游 */
export async function getMcpBrowserUpstreams(): Promise<McpBrowserUpstreamsResponse> {
  const { data } = await http.get<McpBrowserUpstreamsResponse>(
    '/api/mcp-browser/upstreams',
  )
  return data
}

/** GET /api/mcp-browser/<upstream>/tools — 拉某个上游的原始 tools/list */
export async function getUpstreamRawTools(
  upstream: string,
): Promise<RawToolsResponse> {
  const { data } = await http.get<RawToolsResponse>(
    `/api/mcp-browser/${encodeURIComponent(upstream)}/tools`,
  )
  return data
}
