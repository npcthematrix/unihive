<template>
  <article class="onboarding">
    <!-- 顶部卡片: 跳转 MCP APIs / 网关运行监控 -->
    <div class="onboarding-toolbar">
      <el-button @click="jumpToInterfaces">→ MCP APIs</el-button>
      <el-button @click="jumpToMonitor">→ 网关运行监控</el-button>
    </div>

    <!-- 锚点导航 (随滚动自动高亮) -->
    <nav class="onboarding-nav" aria-label="章节导航">
      <a
        v-for="item in SECTIONS"
        :key="item.id"
        :href="`#${item.id}`"
        :class="{ active: activeId === item.id }"
        @click.prevent="scrollTo(item.id)"
      >{{ item.title }}</a>
    </nav>

    <!-- 1. 快速开始 -->
    <section :id="SECTIONS[0].id" class="onboarding-section">
      <h3>{{ SECTIONS[0].title }}</h3>
      <p>UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。</p>
      <h4>1.1 启动网关</h4>
      <p>网关默认以 <strong>HTTP 模式</strong>启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:</p>
      <CodeBlock :code="START_HTTP" />
      <p>启动后, 控制台和 HTTP 网关共用端口 <code>http://127.0.0.1:18080</code> (MCP 接口在 <code>/mcp</code> 子路径下)。</p>
      <h4>1.2 配置管理</h4>
      <p>日常配置在控制台<strong>「配置信息」</strong>页维护, 事实源是配置库 <code>data/config.db</code> — 上游启停、工具契约、FUYAO TTL 等改动<strong>热生效, 无需重启</strong>。空库首次启动时自动从出厂 YAML 模板 (<code>config/upstreams.yaml</code> + <code>config/tools_*.yaml</code>) seed; 监听端口、控制台账号等 bootstrap 配置仍在 <code>config/config.yaml</code>。</p>
    </section>

    <!-- 2. 数据源与路由 -->
    <section :id="SECTIONS[1].id" class="onboarding-section">
      <h3>{{ SECTIONS[1].title }}</h3>
      <p>网关背后是 <strong>5 套上游</strong> (合计 <strong>{{ toolCountText }}</strong> 个 MCP 工具), 按工具路由优先级链分发, 失败自动降级:</p>
      <ul>
        <li><strong>mootdx2</strong>: 通达信本地 TCP — 行情 / K 线 / 板块 (含离线指数 / ETF)</li>
        <li><strong>tdx_quant</strong>: 通达信量化终端 — 行情 / 板块 / 交易日 / 财务 / 公式 / 预警</li>
        <li><strong>fuyao</strong>: 同花顺 aicubes.cn — A 股 / 指数 / 基金 (需 internet)</li>
        <li><strong>omni</strong>: OMNIDATA 本地 SQLite — 行业/概念/地域/风格板块 (THS/TDX 双源, 需先 sync <code>board.db</code>)</li>
        <li><strong>ths_local</strong>: FQGate 本地网关 (17281) — 行情 / K 线 / 资讯 / wencai_nlp / IPO / 期权</li>
      </ul>
      <h4>2.1 路由优先级</h4>
      <p>每个 gateway 工具可在 <code>config/upstreams.yaml</code> 的 <code>routing</code> 段配置<strong>降级链</strong>: 上游 A 失败 → 自动试 B → 一直到最后。控制台「配置信息 → 工具详情」可查看和热编辑每条链。</p>
      <h4>2.2 缓存策略</h4>
      <p>按 <code>cache_ttl_key</code> 区分 TTL 档 (realtime_quote / historical / fundamentals / index_data / fund_data / ...), <strong>时段感知</strong>: 短档在交易时段刷新, 非交易时段自动缓存到下一变化边界 (9:30 开盘 / 13:00 午后开盘 / 收盘后到次日零点)。本地端 (mootdx2 / tdx_quant / thsdk) 完全不缓存 (混合链含 fuyao 时远程响应仍缓存)。</p>
    </section>

    <!-- 3. MCP APIs -->
    <section :id="SECTIONS[2].id" class="onboarding-section">
      <h3>{{ SECTIONS[2].title }}</h3>
      <p>当前网关对外暴露 <strong>{{ toolCountText }}</strong> 个 MCP 工具 (5 套上游, 精确数量以 <strong>"MCP APIs"</strong> 标签页运行时列表为准), 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在该标签页。</p>
    </section>

    <!-- 4. 故障排查 -->
    <section :id="SECTIONS[3].id" class="onboarding-section">
      <h3>{{ SECTIONS[3].title }}</h3>
      <h4>4.1 连接失败</h4>
      <ul>
        <li>检查网关是否已启动: 访问 <code>http://127.0.0.1:18080</code></li>
        <li>检查客户端配置中的 <code>cwd</code> 路径是否正确</li>
        <li>Windows 下确认 Python 已加入 PATH</li>
      </ul>
      <h4>4.2 接口返回错误</h4>
      <ul>
        <li>查看控制台「网关运行监控 → 上游总览」, 确认对应上游是否在线</li>
        <li>查看返回的 <code>last_error</code> 字段</li>
        <li>对应上游若被禁用, 可在控制台"配置信息 → 上游"标签页直接热切换启用 (实时生效并持久化到配置库 <code>data/config.db</code>), 无需重启</li>
      </ul>
      <h4>4.3 参数错误</h4>
      <ul>
        <li>股票代码由网关自动标准化 (<code>000001</code>、<code>sh600519</code>、<code>600519.SH</code>、<code>sz000001</code> 都能识别)</li>
        <li>日期统一使用 <code>YYYYMMDD</code> (如 <code>20260301</code>)</li>
        <li>具体参数以 <strong>"MCP APIs"</strong> 标签页每个 tool 行的"详情"展开为准</li>
      </ul>
      <h4>4.4 反馈渠道</h4>
      <p>遇到问题请提交 Issue 到
        <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener">github.com/unihive/unihive</a>,
        并附上控制台截图与接口返回内容。点击 <strong>"MCP APIs"</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>
    </section>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import CodeBlock from './CodeBlock.vue'
import { useToolCount } from '@/composables/useToolCount'
import { useScrollSpy } from '@/composables/useScrollSpy'

const router = useRouter()
const { toolCount } = useToolCount()

interface SectionDef {
  id: string
  title: string
}

const SECTIONS: readonly SectionDef[] = [
  { id: 'sec-quickstart', title: '快速开始' },
  { id: 'sec-architecture', title: '数据源与路由' },
  { id: 'sec-interfaces', title: 'MCP APIs' },
  { id: 'sec-troubleshoot', title: '故障排查' },
] as const

const { activeId, scrollTo } = useScrollSpy(SECTIONS)

const START_HTTP = '.\\scripts\\start_gateway.ps1'

/** 工具数显示: 拿到用数字, 未拿到给"约 N"兜底 (CLAUDE.md 当前实测值) */
const TOOL_COUNT_FALLBACK = 302
const toolCountText = computed(() =>
  toolCount.value == null ? `约 ${TOOL_COUNT_FALLBACK}` : `${toolCount.value}`,
)

function jumpToInterfaces() {
  void router.push({ name: 'interfaces' })
}

function jumpToMonitor() {
  void router.push({ name: 'gateway-monitor', query: { tab: 'overview' } })
}
</script>

<style scoped>
.onboarding {
  font-size: 14px;
  line-height: 1.65;
  color: var(--el-text-color-primary);
}

.onboarding-toolbar {
  margin-bottom: 12px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.onboarding-nav {
  position: sticky;
  /* topbar 是普通文档流 (随页面滚走), 不是 fixed —— 偏移必须是 0,
     否则吸顶时上方留 52px 缝隙, 页面内容会从缝隙里滚过。 */
  top: 0;
  z-index: 10;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding: 8px 12px;
  margin-bottom: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.onboarding-nav a {
  color: var(--el-text-color-regular);
  text-decoration: none;
  padding: 2px 8px;
  font-size: 13px;
  border-radius: 4px;
  transition: background-color 120ms ease, color 120ms ease;
}

.onboarding-nav a:hover {
  background: var(--el-fill-color-light);
  color: var(--el-color-primary);
}

.onboarding-nav a.active {
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary);
  font-weight: 600;
}

.onboarding-section {
  padding: 16px 0 24px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}

.onboarding-section:last-child {
  border-bottom: none;
}

.onboarding-section h3 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 12px 0;
}

.onboarding-section h4 {
  font-size: 15px;
  font-weight: 600;
  margin: 16px 0 8px 0;
}

.onboarding-section p,
.onboarding-section ul {
  margin: 8px 0;
}

.onboarding-section ul {
  padding-left: 22px;
}

.onboarding-section code {
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 3px;
  font-family: var(--el-font-family-monospace, 'Consolas', 'Monaco', monospace);
  font-size: 13px;
}

.onboarding-toolbox {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0 12px 0;
}

.onboarding-section-meta {
  margin-top: 12px;
  padding: 10px 14px;
  background: var(--el-color-info-light-9, #f4f4f5);
  border-left: 3px solid var(--el-color-info, #909399);
  border-radius: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
