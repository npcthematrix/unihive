import { ref, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import type {
  ConfigEntities,
  ConfigTool,
  GatewayConfig,
  ProxySettings,
} from '@/api/config'
import type { ToolParam, ToolSpec } from '@/api/interfaces'

/**
 * Round-13 簇O: ConfigView 的数据加载编排。
 *
 * 旧实现先 await getConfig(), 实体摘要 / 全量工具清单 / proxy 三路
 * 独立请求等它落地后才发出 —— 无谓瀑布。本 composable 让四路请求
 * 在同一 tick 并发 (Promise.allSettled), 且失败通道彼此独立:
 * - getConfig 失败 → loadError (配置面板内联 alert)
 * - entities / interfaces 失败 → toast, 不阻塞配置渲染
 * - proxy 失败 → proxyError (proxy 面板独立 alert)
 *
 * 并发安全 (审查 HIGH-1): 每通道单调 generation 计数, 过期调用
 * (上一轮 load / 早先的独立重试) 的结果一律丢弃, 不会覆盖新一轮
 * 数据或错误态; loading 标志只有最新一轮 load 能清。
 *
 * 失败保留上次可用数据 (审查 MEDIUM-1): 瞬时失败只显错误, 不清空
 * 已渲染的 config/entities/tools/proxy —— 否则 isDbMode 会翻转、
 * 热编辑入口和面板数据会无故消失。首次加载失败时各 ref 仍是初始空值。
 */

/** /api/interfaces 里 loader 实际消费的字段。 */
export type InterfaceToolRow = Pick<
  ToolSpec,
  'name' | 'description' | 'dangerous' | 'cache_ttl_key'
> & {
  params: ToolParam[]
}

export interface InterfaceToolsResponse {
  tools: InterfaceToolRow[]
}

export interface ConfigPageApis {
  getConfig: () => Promise<GatewayConfig>
  getEntities: () => Promise<ConfigEntities>
  getInterfaces: () => Promise<InterfaceToolsResponse>
  getProxy: () => Promise<ProxySettings>
}

export interface ConfigPageLoader {
  config: Ref<GatewayConfig | null>
  entities: Ref<ConfigEntities | null>
  interfaceTools: Ref<ConfigTool[]>
  proxy: Ref<ProxySettings | null>
  loading: Ref<boolean>
  manualLoading: Ref<boolean>
  proxyLoading: Ref<boolean>
  loadError: Ref<string>
  proxyError: Ref<string>
  lastRefresh: Ref<number | null>
  proxyLastRefresh: Ref<number | null>
  /** 全量加载; isManual=true 时只驱动按钮 loading 态。 */
  load: (isManual?: boolean) => Promise<void>
  /** proxy 通道单独加载 (proxy 面板重试按钮)。 */
  loadProxy: () => Promise<void>
  /** 配置库实体摘要单独重拉 (热编辑后刷新 origin/updated_at)。 */
  loadEntities: () => Promise<void>
  /** 全量工具清单单独重拉 (热编辑/发布态变化后)。 */
  loadInterfaceTools: () => Promise<void>
}

function mapInterfaceTools(tools: InterfaceToolRow[]): ConfigTool[] {
  // interfaces 的 ToolSpec 无 ConfigTool 的字符串索引签名, 结构上兼容;
  // 显式收窄 (ToolsPanel 只读这 5 个字段)。
  return tools.map((t) => ({
    name: t.name,
    description: t.description,
    dangerous: t.dangerous,
    cache_ttl_key: t.cache_ttl_key,
    params: t.params,
  })) as ConfigTool[]
}

export function createConfigPageLoader(
  apis: ConfigPageApis,
  notify: (message: string) => void,
  isProxyActive: () => boolean,
): ConfigPageLoader {
  const config = ref<GatewayConfig | null>(null)
  const entities = ref<ConfigEntities | null>(null)
  const interfaceTools = ref<ConfigTool[]>([])
  const proxy = ref<ProxySettings | null>(null)
  const loading = ref(false)
  const manualLoading = ref(false)
  const proxyLoading = ref(false)
  const loadError = ref('')
  const proxyError = ref('')
  const lastRefresh = ref<number | null>(null)
  const proxyLastRefresh = ref<number | null>(null)

  // 每通道单调 generation: 只有最新一次调用允许写 ref,
  // 并发 load() 与独立重试之间互不覆盖。
  let configGen = 0
  let entitiesGen = 0
  let toolsGen = 0
  let proxyGen = 0
  let loadGen = 0

  /** proxy 通道: 独立 loading/error, 自身不抛 (外层 load 永不因 proxy reject)。 */
  async function applyProxy(promise: Promise<ProxySettings>): Promise<void> {
    const gen = ++proxyGen
    proxyLoading.value = true
    try {
      const value = await promise
      if (gen !== proxyGen) return
      proxy.value = value
      proxyError.value = ''
      proxyLastRefresh.value = Date.now()
    } catch (error) {
      if (gen !== proxyGen) return
      proxyError.value = errorMessage(error, '加载代理设置失败')
    } finally {
      if (gen === proxyGen) proxyLoading.value = false
    }
  }

  async function fetchConfig(promise: Promise<GatewayConfig>): Promise<void> {
    const gen = ++configGen
    try {
      const value = await promise
      if (gen !== configGen) return
      config.value = value
      lastRefresh.value = Date.now()
      loadError.value = ''
    } catch (error) {
      // 保留上次可用配置 (首次失败时 config 仍为初始 null)。
      if (gen === configGen) {
        loadError.value = errorMessage(error, '加载配置失败')
      }
    }
  }

  async function fetchEntities(promise: Promise<ConfigEntities>): Promise<void> {
    const gen = ++entitiesGen
    try {
      const value = await promise
      if (gen === entitiesGen) entities.value = value
    } catch (error) {
      // 瞬时失败不阻塞配置渲染, 也不清空上次数据; 面板下次整体刷新时重试。
      if (gen === entitiesGen) {
        notify(`加载配置库实体失败: ${errorMessage(error, '未知错误')}`)
      }
    }
  }

  async function fetchTools(
    promise: Promise<InterfaceToolsResponse>,
  ): Promise<void> {
    const gen = ++toolsGen
    try {
      const data = await promise
      if (gen !== toolsGen) return
      // 后端契约漂移 (tools 缺失/非数组) 不当异常抛穿, 保留旧清单 + toast。
      const raw: unknown = data?.tools
      if (!Array.isArray(raw)) {
        notify('加载工具清单失败: 响应缺少 tools 字段')
        return
      }
      interfaceTools.value = mapInterfaceTools(raw as InterfaceToolRow[])
    } catch (error) {
      if (gen === toolsGen) {
        notify(`加载工具清单失败: ${errorMessage(error, '未知错误')}`)
      }
    }
  }

  async function load(isManual = false): Promise<void> {
    const gen = ++loadGen
    if (isManual) manualLoading.value = true
    else loading.value = true
    loadError.value = ''

    // 四路请求同一 tick 全部发出, 谁也不等 config 落地。
    const jobs = [
      fetchConfig(apis.getConfig()),
      fetchEntities(apis.getEntities()),
      fetchTools(apis.getInterfaces()),
    ]
    const proxyJob = applyProxy(apis.getProxy())

    try {
      if (isProxyActive()) {
        // 用户停在 proxy tab: 等 proxy 也落地, 面板立即可见数据。
        await Promise.allSettled([...jobs, proxyJob])
      } else {
        // 非 proxy tab: proxy 后台落地即可, 不拖整体 load 延迟。
        await Promise.allSettled(jobs)
      }
    } finally {
      // 只有最新一轮 load 能清标志: 旧轮先结束不得摘掉新轮的 loading。
      if (gen === loadGen) {
        loading.value = false
        manualLoading.value = false
      }
    }
  }

  return {
    config,
    entities,
    interfaceTools,
    proxy,
    loading,
    manualLoading,
    proxyLoading,
    loadError,
    proxyError,
    lastRefresh,
    proxyLastRefresh,
    load,
    loadProxy: () => applyProxy(apis.getProxy()),
    loadEntities: () => fetchEntities(apis.getEntities()),
    loadInterfaceTools: () => fetchTools(apis.getInterfaces()),
  }
}
