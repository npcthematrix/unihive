import { http } from './client'

// ---------------------------------------------------------------------------
// OMNI 板块数据 (offline, SQLite, 经 `python -m src.unihive.sync.board_sync` 预同步)
// ---------------------------------------------------------------------------

export type OmniSource = '' | 'fuyao' | 'tqquant'
/** 数据库存储值: THS / TDX (经 SOURCE_ALIASES 映射后)。 */
export type OmniDBSource = 'THS' | 'TDX' | string
export type OmniBoardType = 'industry' | 'concept' | 'style' | 'region'
export type OmniSyncStatus = 'success' | 'failed' | 'running'

// ----- GET /api/omni (stats + recent_logs) -----

export interface OmniStatRow {
  source: OmniDBSource
  board_type: string
  sector_count: number
  total_stocks: number
}

export interface OmniSyncLog {
  id?: number
  source: string
  board_type?: string
  status: OmniSyncStatus
  start_time?: string
  end_time?: string
  message?: string
  record_count?: number
  [key: string]: unknown
}

export interface OmniStatsResponse {
  status: 'success' | 'failed'
  message?: string
  stats: OmniStatRow[]
  recent_logs: OmniSyncLog[]
}

/** GET /api/omni — 数据统计 + 最近同步日志。console 端可单飞共享 in-flight。 */
export async function getOmniStats(): Promise<OmniStatsResponse> {
  const { data } = await http.get<OmniStatsResponse>('/api/omni')
  return data
}

// ----- GET /api/omni/query (sectors + pagination) -----

export interface OmniSector {
  id: number
  source: OmniDBSource
  board_type: string
  code: string
  name: string
  stock_count: number
  update_time?: string
}

export interface OmniQueryResponse {
  status?: 'success' | 'failed'
  message?: string
  sectors: OmniSector[]
  total: number
  /** 后端 total_count 是 COUNT(*) 全量; total 是当前页行数 (受 LIMIT 影响)。 */
  total_count: number
  page: number
  page_size: number
}

export interface OmniQueryParams {
  source?: OmniSource
  board_type?: OmniBoardType | ''
  keyword?: string
  page?: number
  page_size?: number
}

/** GET /api/omni/query — 板块查询 + 分页。 */
export async function queryOmniSectors(
  params: OmniQueryParams,
  signal?: AbortSignal,
): Promise<OmniQueryResponse> {
  const search = new URLSearchParams()
  if (params.source) search.set('source', params.source)
  if (params.board_type) search.set('board_type', params.board_type)
  if (params.keyword) search.set('keyword', params.keyword)
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  const qs = search.toString()
  const { data } = await http.get<OmniQueryResponse>(
    `/api/omni/query${qs ? `?${qs}` : ''}`,
    { signal },
  )
  return data
}

// ----- POST /api/omni/sync (returns immediately, backend may run for 5 min) -----

export interface OmniSyncRequest {
  source?: 'all' | OmniSource
  board_type?: 'all' | OmniBoardType
}

export interface OmniSyncResponse {
  status: OmniSyncStatus
  message?: string
  record_count?: number
  /** 后端实际写入 sectors 表的条数, 与 record_count 同义。 */
  stocks?: number
  [key: string]: unknown
}

/**
 * POST /api/omni/sync — 触发同步; 调用方负责 AbortController 取消。
 * 服务端 wait_for 在 300s 取消任务，之后还要写收尾日志，故客户端给到
 * 310s 余量（两者相等会在临界点先超时、toast 与后端实际状态不符）;
 * 必须覆盖全局 15s timeout。POST 不参与拦截器自动重试, 不会双发。
 */
const SYNC_TIMEOUT_MS = 310_000

export async function runOmniSync(
  body: OmniSyncRequest,
  signal?: AbortSignal,
): Promise<OmniSyncResponse> {
  const { data } = await http.post<OmniSyncResponse>('/api/omni/sync', body, {
    signal,
    timeout: SYNC_TIMEOUT_MS,
  })
  return data
}

// ----- GET /api/omni/sector-stocks -----

export interface OmniSectorStock {
  stock_code: string
  stock_name?: string
  rank?: number
  [key: string]: unknown
}

export interface OmniSectorStocksResponse {
  status?: 'success' | 'failed'
  message?: string
  sector: {
    id: number
    source: OmniDBSource
    board_type: string
    code: string
    name: string
    stock_count: number
  }
  stocks: OmniSectorStock[]
}

export interface OmniSectorStocksParams {
  sector_id?: number
  code?: string
  source?: string
}

/** GET /api/omni/sector-stocks — 板块成分股; sector_id 优先, 否则 code+source 兜底。 */
export async function getOmniSectorStocks(
  params: OmniSectorStocksParams,
  signal?: AbortSignal,
): Promise<OmniSectorStocksResponse> {
  const search = new URLSearchParams()
  if (params.sector_id) search.set('sector_id', String(params.sector_id))
  if (params.code) search.set('code', params.code)
  if (params.source) search.set('source', params.source)
  const qs = search.toString()
  const { data } = await http.get<OmniSectorStocksResponse>(
    `/api/omni/sector-stocks${qs ? `?${qs}` : ''}`,
    { signal },
  )
  return data
}
