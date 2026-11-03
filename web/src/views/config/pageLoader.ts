import { ref, type Ref } from 'vue'
import { errorMessage } from '@/api/client'
import type {
  ConfigEntities,
  ConfigTool,
  GatewayConfig,
  PoolConfigSnapshot,
  ProxySettings,
} from '@/api/config'
import type { ToolParam, ToolSpec } from '@/api/interfaces'

/**
 * Round-13 簇O: ConfigView 的数据加载编排。
 *
 * 旧实现先 await getConfig(), 实体摘要 / 全量工具清单 / proxy 三路
 * 独立请求等它落地后才发出 —— 无谓瀑布。本 composable 让各路请求
 * 在同一 tick 并发 (Promise.allSettled), 且失败通道彼此独立:
 * - getConfig 失败 → loadError (配置面板内联 alert)
 * - entities 失败 → toast + entitiesError, 不阻塞配置渲染
 * - interfaces 失败 → toast + toolsError (「复制 JSON」就绪判定用 toolsLoaded)
 * - pool 失败 → poolError (上游面板「全局池参数」折叠区内联降级)
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
  getPool: () => Promise<PoolConfigSnapshot>
}

export interface ConfigPageLoader {
  config: Ref<GatewayConfig | null>
  entities: Ref<ConfigEntities | null>
  interfaceTools: Ref<ConfigTool[]>
  proxy: Ref<ProxySettings | null>
  /** 启动期池参数运行态快照 (「全局池参数」折叠区的数据源)。 */
  pool: Ref<PoolConfigSnapshot | null>
  /** 池快照通道的最近一次错误; 不清空 pool (失败保留上次可用数据)。 */
  poolError: Ref<string>
  loading: Ref<boolean>
  manualLoading: Ref<boolean>
  proxyLoading: Ref<boolean>
  loadError: Ref<string>
  proxyError: Ref<string>
  /**
   * 实体摘要通道的最近一次错误。与 loadError 不同, 它不清空 entities ——
   * 用于区分「yaml 逃生模式」与「请求失败」: 两者都会让 entities 不可用,
   * 但只有后者需要给用户一个可重试的错误面 (否则热编辑入口会静默消失)。
   */
  entitiesError: Ref<string>
  /**
   * 工具清单通道的最近一次错误 (含后端契约漂移)。同样不清空
   * interfaceTools, 但 toolsLoaded 不会因此变 true —— 「复制 JSON」
   * 必须只在真的拿到过清单时才就绪, 否则会复制到一份缺 tools 的快照。
   */
  toolsError: Ref<string>
  /** /api/interfaces 是否成功落地过至少一轮; 复制 JSON 的就绪判定用。 */
  toolsLoaded: Ref<boolean>
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
  /** 池参数快照单独重拉 (「全局池参数」折叠区的重试按钮)。 */
  loadPool: () => Promise<void>
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
  const pool = ref<PoolConfigSnapshot | null>(null)
  const loading = ref(false)
  const manualLoading = ref(false)
  const proxyLoading = ref(false)
  const loadError = ref('')
  const proxyError = ref('')
  const poolError = ref('')
  const entitiesError = ref('')
  const toolsError = ref('')
  const toolsLoaded = ref(false)
  const lastRefresh = ref<number | null>(null)
  const proxyLastRefresh = ref<number | null>(null)

  // 每通道单调 generation: 只有最新一次调用允许写 ref,
  // 并发 load() 与独立重试之间互不覆盖。
  let configGen = 0
  let entitiesGen = 0
  let toolsGen = 0
  let proxyGen = 0
  let poolGen = 0
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
      if (gen === entitiesGen) {
        entities.value = value
        entitiesError.value = ''
      }
    } catch (error) {
      // 瞬时失败不阻塞配置渲染, 也不清空上次数据; 面板下次整体刷新时重试。
      // 错误额外记到 entitiesError —— 上层据此区分「yaml 逃生模式」(预期)
      // 与「请求失败」(非预期, 需给出可重试的错误面)。
      if (gen === entitiesGen) {
        const message = errorMessage(error, '未知错误')
        entitiesError.value = message
        notify(`加载配置库实体失败: ${message}`)
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
        toolsError.value = '响应缺少 tools 字段'
        notify('加载工具清单失败: 响应缺少 tools 字段')
        return
      }
      interfaceTools.value = mapInterfaceTools(raw as InterfaceToolRow[])
      toolsError.value = ''
      // 只有真的拿到清单才算「已加载」: 失败/契约漂移即便保留了旧数据,
      // 也不能让「复制 JSON」据此认为自己复制的是完整快照。
      toolsLoaded.value = true
    } catch (error) {
      if (gen === toolsGen) {
        const message = errorMessage(error, '未知错误')
        toolsError.value = message
        notify(`加载工具清单失败: ${message}`)
      }
    }
  }

  /**
   * 池参数快照: 与 proxy 同款独立错误面 (折叠区内联降级文本, 不打 toast ——
   * 该端点在网关未初始化池时本就返回 404/503, 属于预期降级)。
   */
  async function fetchPool(promise: Promise<PoolConfigSnapshot>): Promise<void> {
    const gen = ++poolGen
    try {
      const value = await promise
      if (gen !== poolGen) return
      pool.value = value
      poolError.value = ''
    } catch (error) {
      if (gen === poolGen) {
        poolError.value = errorMessage(error, '加载池参数失败')
      }
    }
  }

  async function load(isManual = false): Promise<void> {
    const gen = ++loadGen
    if (isManual) manualLoading.value = true
    else loading.value = true
    loadError.value = ''

    // 各路请求同一 tick 全部发出, 谁也不等 config 落地。
    const jobs = [
      fetchConfig(apis.getConfig()),
      fetchEntities(apis.getEntities()),
      fetchTools(apis.getInterfaces()),
      fetchPool(apis.getPool()),
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
    pool,
    poolError,
    loading,
    manualLoading,
    proxyLoading,
    loadError,
    proxyError,
    entitiesError,
    toolsError,
    toolsLoaded,
    lastRefresh,
    proxyLastRefresh,
    load,
    loadProxy: () => applyProxy(apis.getProxy()),
    loadEntities: () => fetchEntities(apis.getEntities()),
    loadInterfaceTools: () => fetchTools(apis.getInterfaces()),
    loadPool: () => fetchPool(apis.getPool()),
  }
}
