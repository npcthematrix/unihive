/**
 * 轻量拉取网关 runtime.tool_count — 接入指南页用, 与 MCP APIs 标签页同源。
 *
 * 不复用 useGatewayRuntime: 那是监控页的全量编排 (5 源 metrics + 趋势 +
 * breakdown + 管理动作), 文档页只需要 tool_count。
 *
 * 策略:
 * - mount 时拉一次, 之后 60s 轮询 (文档页刷新频率低)
 * - AbortController: unmount 时取消在途请求, 不留悬挂连接 (与监控页同处理)
 * - 失败保留旧值 (跟监控页 runtime 源失败策略一致: 不挂告警, 不闪空)
 */
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { isCanceledError } from '@/api/client'
import { getRuntimeStats } from '@/api/runtime'

const POLL_INTERVAL_MS = 60_000

export interface UseToolCountResult {
  /** 运行时工具数; null = 尚未拿到 (首屏 / 持续失败)。 */
  toolCount: Ref<number | null>
}

export function useToolCount(): UseToolCountResult {
  const toolCount = ref<number | null>(null)
  const ac = new AbortController()
  let timer: ReturnType<typeof setInterval> | null = null

  async function load(): Promise<void> {
    try {
      const stats = await getRuntimeStats({ signal: ac.signal })
      toolCount.value = stats.tool_count
    } catch (error) {
      if (isCanceledError(error)) return
      // 失败保留旧值, 不闪空也不挂告警
    }
  }

  onMounted(() => {
    void load()
    timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      void load()
    }, POLL_INTERVAL_MS)
  })

  onBeforeUnmount(() => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    ac.abort()
  })

  return { toolCount }
}
