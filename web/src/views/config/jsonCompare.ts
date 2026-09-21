/** 键排序的规范化 JSON 序列化 — 用于比较实体编辑前后配置是否真的变化,
 * 与对象字面量键的书写顺序无关。 toolEdit / upstreamEdit 共用。 */
export function stableJson(v: unknown): string {
  if (v === null || typeof v !== 'object') return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(stableJson).join(',')}]`
  const obj = v as Record<string, unknown>
  return `{${Object.keys(obj)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableJson(obj[k])}`)
    .join(',')}}`
}
