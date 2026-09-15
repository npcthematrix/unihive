/**
 * 派生 MCP HTTP 端点 URL — 使用浏览器当前 origin (与 gateway 同进程部署),
 * 避免在 LAN 部署场景下出现 "AI 客户端配置贴上后无法连接到 127.0.0.1" 的问题。
 */

/** 当前浏览器 origin (协议 + host + 端口)。 例: `http://192.168.1.10:18080` */
export function gatewayOrigin(): string {
  // 在 happy-dom/SSR 环境 location 可能为 undefined, 兜底 127.0.0.1:18080 与历史默认值一致
  if (typeof location === 'undefined') return 'http://127.0.0.1:18080'
  return location.origin
}

/** MCP HTTP 端点 URL。 例: `http://127.0.0.1:18080/mcp` */
export function mcpHttpUrl(): string {
  return `${gatewayOrigin()}/mcp`
}