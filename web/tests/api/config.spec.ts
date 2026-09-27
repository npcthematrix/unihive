import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  PoolConfigEditResult,
  PoolConfigSnapshot,
} from '../../src/api/config'

/**
 * /api/runtime/pool-config — 配置信息页"全局池参数"卡片数据源 (Phase D 2026-09-27)。
 * /api/config/pool — Phase E (2026-09-27) PUT 端点, 落 config.db, 重启生效。
 *
 * 不重复 client.spec.ts 已覆盖的 axios.create/401/重试机制; 这里只
 * 验证 getPoolConfig/updatePoolConfig 走正确的路径、解析返回结构、
 * 不破坏邻居字段。
 */

const h = vi.hoisted(() => {
  const mockHttpGet = vi.fn()
  const mockHttpPut = vi.fn()
  return { mockHttpGet, mockHttpPut }
})

vi.mock('@/api/client', () => ({
  http: { get: h.mockHttpGet, put: h.mockHttpPut },
}))

let getPoolConfig: typeof import('@/api/config').getPoolConfig
let updatePoolConfig: typeof import('@/api/config').updatePoolConfig

beforeEach(async () => {
  vi.clearAllMocks()
  vi.resetModules()
  const mod = await import('@/api/config')
  getPoolConfig = mod.getPoolConfig
  updatePoolConfig = mod.updatePoolConfig
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('getPoolConfig', () => {
  it('GET /api/runtime/pool-config', async () => {
    const snapshot: PoolConfigSnapshot = {
      mootdx2_reader_limit: 4,
      mootdx2_worker_threads: 8,
      tdx_quant_worker_threads: 10,
      sources: {
        mootdx2_reader_limit: 'cfg',
        mootdx2_worker_threads: 'env',
        tdx_quant_worker_threads: 'default',
      },
      timestamp: 1738000000,
    }
    h.mockHttpGet.mockResolvedValueOnce({ data: snapshot })

    const result = await getPoolConfig()

    expect(h.mockHttpGet).toHaveBeenCalledTimes(1)
    expect(h.mockHttpGet).toHaveBeenCalledWith('/api/runtime/pool-config')
    expect(result).toEqual(snapshot)
  })

  it('三个 int 字段 + sources dict + timestamp 都透传', async () => {
    const snapshot: PoolConfigSnapshot = {
      mootdx2_reader_limit: 6,
      mootdx2_worker_threads: 12,
      tdx_quant_worker_threads: 16,
      sources: {
        mootdx2_reader_limit: 'default',
        mootdx2_worker_threads: 'default',
        tdx_quant_worker_threads: 'default',
      },
      timestamp: 1738000123,
    }
    h.mockHttpGet.mockResolvedValueOnce({ data: snapshot })

    const result = await getPoolConfig()
    expect(result.mootdx2_reader_limit).toBe(6)
    expect(result.mootdx2_worker_threads).toBe(12)
    expect(result.tdx_quant_worker_threads).toBe(16)
    expect(result.sources.mootdx2_reader_limit).toBe('default')
    expect(result.timestamp).toBe(1738000123)
  })

  it('异常透传 (handler 返 404/500 时, axios reject 由调用方捕获)', async () => {
    const err = new Error('pool_config_not_available')
    h.mockHttpGet.mockRejectedValueOnce(err)

    await expect(getPoolConfig()).rejects.toBe(err)
  })
})

describe('updatePoolConfig', () => {
  const fields = {
    mootdx2_reader_limit: 6,
    mootdx2_worker_threads: 12,
    tdx_quant_worker_threads: 16,
  }

  it('PUT /api/config/pool, body 含 fields + revision', async () => {
    const result: PoolConfigEditResult = {
      changed: true,
      revision: 42,
      persisted: fields,
      running: {
        ...fields,
        sources: {
          mootdx2_reader_limit: 'cfg',
          mootdx2_worker_threads: 'cfg',
          tdx_quant_worker_threads: 'cfg',
        },
      },
      pending_restart: {
        mootdx2_reader_limit: true,
        mootdx2_worker_threads: true,
        tdx_quant_worker_threads: true,
      },
    }
    h.mockHttpPut.mockResolvedValueOnce({ data: result })

    const got = await updatePoolConfig(fields, { revision: 42 })

    expect(h.mockHttpPut).toHaveBeenCalledTimes(1)
    expect(h.mockHttpPut).toHaveBeenCalledWith('/api/config/pool', {
      fields,
      revision: 42,
    })
    expect(got).toEqual(result)
  })

  it('revision=undefined 时省略该字段 (不发送 revision 键)', async () => {
    h.mockHttpPut.mockResolvedValueOnce({
      data: { changed: false, revision: 0, persisted: fields, running: { ...fields, sources: {} }, pending_restart: { mootdx2_reader_limit: false, mootdx2_worker_threads: false, tdx_quant_worker_threads: false } },
    })

    await updatePoolConfig(fields)

    const body = h.mockHttpPut.mock.calls[0][1]
    expect(body).toEqual({ fields })
    expect('revision' in body).toBe(false)
  })
})