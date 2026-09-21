import { http } from './client'

/** 按工具聚合的缓存条目行 (GET /api/cache/tool-breakdown) */
export interface CacheToolBreakdownRow {
  tool: string
  entries: number
  /** 其中未过期条目数 */
  live: number
}

export interface CacheBreakdownResponse {
  tools: CacheToolBreakdownRow[]
  timestamp: number
}

export interface CacheCleanupResponse {
  /** 本次删除条数 */
  deleted: number
  timestamp: number
}

export interface CacheClearResponse {
  cleared: boolean
  timestamp: number
}

export interface CacheVacuumResponse {
  db_size_mb: number
  wal_size_bytes: number
  timestamp: number
}

export interface UpstreamL1ResetResponse {
  upstream: string
  result: {
    enabled: boolean
    cleared: string[]
  }
  timestamp: number
}

/** GET /api/cache/tool-breakdown — 各工具缓存条目 Top N */
export async function getCacheToolBreakdown(
  limit = 20,
): Promise<CacheBreakdownResponse> {
  const { data } = await http.get<CacheBreakdownResponse>(
    '/api/cache/tool-breakdown',
    { params: { limit } },
  )
  return data
}

/** POST /api/cache/cleanup — 立即清理过期条目 */
export async function cleanupCache(): Promise<CacheCleanupResponse> {
  const { data } = await http.post<CacheCleanupResponse>(
    '/api/cache/cleanup',
    {},
  )
  return data
}

/** POST /api/cache/clear — 清空全部缓存 (后端要求 confirm:true) */
export async function clearCache(): Promise<CacheClearResponse> {
  const { data } = await http.post<CacheClearResponse>('/api/cache/clear', {
    confirm: true,
  })
  return data
}

/** POST /api/cache/vacuum — VACUUM 回收死页 */
export async function vacuumCache(): Promise<CacheVacuumResponse> {
  const { data } = await http.post<CacheVacuumResponse>(
    '/api/cache/vacuum',
    {},
  )
  return data
}

/** POST /api/upstreams/<name>/l1/reset — 手动清空上游 L1 内存缓存 */
export async function resetUpstreamL1(
  name: string,
): Promise<UpstreamL1ResetResponse> {
  const { data } = await http.post<UpstreamL1ResetResponse>(
    `/api/upstreams/${encodeURIComponent(name)}/l1/reset`,
    {},
  )
  return data
}
