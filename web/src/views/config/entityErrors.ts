/**
 * 实体编辑写接口 (PUT tool / PUT upstream) 的错误体归类 (P3-4)。
 *
 * 后端 console_api._entity_edit_error_response 是状态码契约的权威;
 * 这里把 axios 错误翻译成结构化信息, 供弹窗决定:
 *   confirmation_required → 补确认后重试 (理论上客户端已预检, 防御性保留)
 *   lint_rejected         → 弹窗内展示 issues, 不关闭
 *   revision_conflict     → 重新 GET 详情覆盖表单, 提示用户复核
 *   invalid               → 内联展示后端 message (如明文密钥)
 *   not_found             → 实体已被删除
 *   store_unavailable     → yaml 逃生模式/stdio
 */
import axios from 'axios'
import type { LintIssueBody } from '@/api/config'

export type EntityEditErrorKind =
  | 'confirmation_required'
  | 'lint_rejected'
  | 'revision_conflict'
  | 'invalid'
  | 'not_found'
  | 'store_unavailable'
  | 'server'
  | 'network'

export interface EntityEditErrorInfo {
  kind: EntityEditErrorKind
  /** 后端原始 error code 字符串。 */
  code: string
  /** 可展示消息 (优先服务端 message, 回退 axios message)。 */
  message: string
  /** confirmation_required 时后端回带的契约层字段。 */
  contractFields: string[]
  /** lint_rejected 时的全部 error 级 issue。 */
  issues: LintIssueBody[]
  /** revision_conflict 时的新旧版本。 */
  conflict: { expected: number; actual: number } | null
  status: number | null
}

interface RawErrorBody {
  error?: string
  message?: string
  contract_fields?: unknown
  issues?: unknown
  expected?: unknown
  actual?: unknown
}

function toStringList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function toIssues(v: unknown): LintIssueBody[] {
  if (!Array.isArray(v)) return []
  return v
    .filter(
      (x): x is Record<string, unknown> => typeof x === 'object' && x !== null,
    )
    .map((x) => ({
      severity: String(x.severity ?? 'error'),
      code: String(x.code ?? ''),
      message: String(x.message ?? ''),
    }))
}

/** 把任意异常归类为实体编辑错误; 非 axios/未知异常归 server。 */
export function describeEntityEditError(error: unknown): EntityEditErrorInfo {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status ?? null
    const body = (error.response?.data ?? {}) as RawErrorBody
    const code = typeof body.error === 'string' ? body.error : ''
    const message = body.message || code || error.message || '请求失败'

    if (code === 'dangerous_confirmation_required') {
      return {
        kind: 'confirmation_required',
        code,
        message,
        contractFields: toStringList(body.contract_fields),
        issues: [],
        conflict: null,
        status,
      }
    }
    if (code === 'lint_rejected') {
      return {
        kind: 'lint_rejected',
        code,
        message,
        contractFields: [],
        issues: toIssues(body.issues),
        conflict: null,
        status,
      }
    }
    if (code === 'revision_conflict') {
      return {
        kind: 'revision_conflict',
        code,
        message,
        contractFields: [],
        issues: [],
        conflict: {
          expected: Number(body.expected),
          actual: Number(body.actual),
        },
        status,
      }
    }
    if (code.startsWith('config_store_unavailable')) {
      return base('store_unavailable', code, message, status)
    }
    if (status === 404 || code.startsWith('unknown')) {
      return base('not_found', code, message, status)
    }
    if (status === 400) {
      return base('invalid', code, message, status)
    }
    if (status !== null) {
      return base('server', code, message, status)
    }
    return {
      kind: 'network',
      code,
      message: error.message || '无法连接到网关',
      contractFields: [],
      issues: [],
      conflict: null,
      status,
    }
  }
  const message = error instanceof Error ? error.message : '未知错误'
  return {
    kind: 'server',
    code: '',
    message,
    contractFields: [],
    issues: [],
    conflict: null,
    status: null,
  }
}

function base(
  kind: EntityEditErrorKind,
  code: string,
  message: string,
  status: number | null,
): EntityEditErrorInfo {
  return {
    kind,
    code,
    message,
    contractFields: [],
    issues: [],
    conflict: null,
    status,
  }
}
