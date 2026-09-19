import { describe, expect, it } from 'vitest'
import type { AxiosError } from 'axios'
import { describeEntityEditError } from '@/views/config/entityErrors'

/** axios.isAxiosError 对 isAxiosError===true 的对象做鸭子判定, 无需 mock。 */
function axiosError(status: number | undefined, data: unknown, message = 'fail'): AxiosError {
  return {
    isAxiosError: true,
    message,
    response: status === undefined ? undefined : { status, data, statusText: '', headers: {}, config: {} as never },
    config: {} as never,
  } as unknown as AxiosError
}

describe('describeEntityEditError', () => {
  it('400 dangerous_confirmation_required → confirmation_required + 字段清单', () => {
    const info = describeEntityEditError(
      axiosError(400, {
        error: 'dangerous_confirmation_required',
        message: 'contract-layer field(s) require ...',
        contract_fields: ['name', 'mapping'],
      }),
    )
    expect(info.kind).toBe('confirmation_required')
    expect(info.contractFields).toEqual(['name', 'mapping'])
    expect(info.status).toBe(400)
  })

  it('422 lint_rejected → issues 透传', () => {
    const info = describeEntityEditError(
      axiosError(422, {
        error: 'lint_rejected',
        message: 'entity lint rejected (1 error(s))',
        issues: [
          { severity: 'error', code: 'M001', message: 'unknown upstream x' },
          { severity: 'error', code: 'T001', message: 'bad spec' },
        ],
      }),
    )
    expect(info.kind).toBe('lint_rejected')
    expect(info.issues.map((i) => i.code)).toEqual(['M001', 'T001'])
  })

  it('409 revision_conflict → expected/actual', () => {
    const info = describeEntityEditError(
      axiosError(409, {
        error: 'revision_conflict',
        expected: 2,
        actual: 5,
      }),
    )
    expect(info.kind).toBe('revision_conflict')
    expect(info.conflict).toEqual({ expected: 2, actual: 5 })
  })

  it('400 invalid (明文密钥等) → invalid, 保留后端消息', () => {
    const info = describeEntityEditError(
      axiosError(400, {
        error: 'invalid request: ValueError: Refusing to write plaintext secret field(s): api_key',
      }),
    )
    expect(info.kind).toBe('invalid')
    expect(info.message).toContain('plaintext secret')
  })

  it('404 unknown entity → not_found', () => {
    const info = describeEntityEditError(
      axiosError(404, { error: 'unknown entity: \'ghost\'' }),
    )
    expect(info.kind).toBe('not_found')
  })

  it('503 config_store_unavailable → store_unavailable', () => {
    const info = describeEntityEditError(
      axiosError(503, {
        error: 'config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)',
      }),
    )
    expect(info.kind).toBe('store_unavailable')
  })

  it('500 其他 → server', () => {
    const info = describeEntityEditError(
      axiosError(500, { error: 'runtime failure: RuntimeError: boom' }),
    )
    expect(info.kind).toBe('server')
  })

  it('无 response → network', () => {
    const info = describeEntityEditError(axiosError(undefined, undefined, 'Network Error'))
    expect(info.kind).toBe('network')
    expect(info.message).toBe('Network Error')
  })

  it('非 axios 异常 → server', () => {
    const info = describeEntityEditError(new Error('boom'))
    expect(info.kind).toBe('server')
    expect(info.message).toBe('boom')
  })

  it('畸形错误体安全降级不抛异常', () => {
    const info = describeEntityEditError(
      axiosError(422, { error: 'lint_rejected', issues: 'nope' }),
    )
    expect(info.kind).toBe('lint_rejected')
    expect(info.issues).toEqual([])
  })
})
