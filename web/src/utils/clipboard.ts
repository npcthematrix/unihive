import { ElMessage } from 'element-plus'

/**
 * 复制文本到剪贴板并给全局反馈。
 * 返回是否成功, 调用方需要按钮内联"已复制"反馈时可据此处理。
 */
export async function copyText(text: string, successMsg = '已复制到剪贴板'): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    if (successMsg) ElMessage.success(successMsg)
    return true
  } catch {
    ElMessage.error('复制失败, 请手动选择文本复制')
    return false
  }
}
