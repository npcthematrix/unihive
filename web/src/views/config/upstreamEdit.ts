/**
 * 上游连接块编辑 — JSON 文本模型 / 客户端预校验 (P3-4)。
 *
 * 连接块是异型 dict (http/omni/tdx_quant 各有各的键), 控制台用整段 JSON
 * 编辑而非逐键表单; 后端 entity_edit.validate_upstream_config 是最终权威。
 */
import type { UpstreamEntity } from '@/api/config'
import { stableJson } from './jsonCompare'

/**
 * 后端 config_loader 接受的上游 type (2026-09-18)。select 优先列这些,
 * 仍允许手输其他值 (前向兼容新 type)。
 */
export const KNOWN_UPSTREAM_TYPES = [
  'http',
  'http_jsonrpc',
  'mootdx2',
  'tdx_quant',
  'omni',
  'thsdk',
  'python',
  'npx',
] as const

/** 连接块禁止出现的列键 (后端 UPSTREAM_RESERVED_CONFIG_KEYS)。 */
export const RESERVED_CONFIG_KEYS = ['enabled', 'type'] as const

/** 实体连接块 → 编辑框初值 (2 空格缩进)。 */
export function connectionConfigToText(config: Record<string, unknown>): string {
  return JSON.stringify(config ?? {}, null, 2)
}

export interface ParsedConnectionConfig {
  value?: Record<string, unknown>
  errors: string[]
}

/**
 * 解析连接块 JSON 文本并做形态预校验:
 * - 必须是 JSON 对象 (非数组/标量);
 * - 禁止 enabled/type 列键 (type 走独立字段);
 * - 密钥键只接受 ${VAR} 占位符 (与后端 find_plaintext_secrets_in 同意图)。
 *
 * 空/全空白文本视为空对象合法 (个别上游确实无连接参数, 如描述占位上游)。
 */
export function parseConnectionConfig(text: string): ParsedConnectionConfig {
  const trimmed = text.trim()
  if (!trimmed) return { value: {}, errors: [] }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch (e) {
    return {
      errors: [`JSON 解析失败: ${e instanceof Error ? e.message : String(e)}`],
    }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { errors: ['连接块必须是 JSON 对象 ({ ... })'] }
  }
  const obj = parsed as Record<string, unknown>
  const errors: string[] = []
  for (const key of Object.keys(obj)) {
    if ((RESERVED_CONFIG_KEYS as readonly string[]).includes(key)) {
      errors.push(`连接块不能包含列键 ${key} (启用态走开关, 类型走独立字段)`)
    }
  }
  errors.push(...plaintextSecretPaths(obj))
  return errors.length ? { errors } : { value: obj, errors }
}

const SECRET_KEY_RE = /(^|[_-])(key|token|secret|password|passwd|pwd)([_-]|$)|api[_-]?key|access[_-]?key/i
const PLACEHOLDER_RE = /^\$\{[A-Za-z_][A-Za-z0-9_]*\}$/

/** 递归找疑似明文密钥字段 (与后端 _SECRET_KEY_RE/_ENV_PLACEHOLDER_RE 同意图)。 */
export function plaintextSecretPaths(
  node: unknown,
  prefix = '',
): string[] {
  const found: string[] = []
  if (typeof node === 'object' && node !== null && !Array.isArray(node)) {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      const path = prefix ? `${prefix}.${key}` : key
      if (typeof value === 'string' && SECRET_KEY_RE.test(key) && !PLACEHOLDER_RE.test(value)) {
        found.push(path)
      }
      found.push(...plaintextSecretPaths(value, path))
    }
  } else if (Array.isArray(node)) {
    node.forEach((v, i) => found.push(...plaintextSecretPaths(v, `${prefix}[${i}]`)))
  }
  return found
}

/** 连接块/类型相对实体是否有净变化 (键序无关)。 */
export function isUpstreamChanged(
  entity: UpstreamEntity,
  config: Record<string, unknown>,
  type: string,
): boolean {
  return type !== entity.type || stableJson(config) !== stableJson(entity.config || {})
}

