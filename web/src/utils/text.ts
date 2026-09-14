/** 转义正则特殊字符, 供用户输入构造高亮/匹配正则。 */
export function escapeRegExp(input: string): string {
  return input.replace(/[\\^$.*+?()[\]{}|\-]/g, '\\$&')
}
