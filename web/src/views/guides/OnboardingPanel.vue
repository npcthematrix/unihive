<template>
  <article class="onboarding">
    <!-- 顶部卡片: 跳转 MCP APIs -->
    <div class="onboarding-toolbar">
      <el-button type="primary" @click="jumpToInterfaces">→ MCP APIs</el-button>
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
      <div class="onboarding-toolbox">
        <el-button @click="copyCommand(START_HTTP, '已复制 HTTP 启动命令')">复制 HTTP 启动命令</el-button>
        <el-button @click="copyCommand(START_STDIO, '已复制 STDIO 启动命令')">复制 STDIO 启动命令</el-button>
      </div>
      <CodeBlock :code="START_HTTP" />
      <p>启动后, 控制台和 HTTP 网关共用端口 <code>http://127.0.0.1:18080</code> (MCP 接口在 <code>/mcp</code> 子路径下)。</p>
      <h4>1.2 配置文件</h4>
      <p>上游数据源在 <code>config/upstreams.yaml</code> 中配置。修改后需要重启网关生效。</p>
    </section>

    <!-- 2. 接入方式 -->
    <section :id="SECTIONS[1].id" class="onboarding-section">
      <h3>{{ SECTIONS[1].title }}</h3>
      <h4>2.1 HTTP 模式 (推荐)</h4>
      <p>网关以 <strong>Streamable HTTP</strong> 模式对外提供 MCP 接口, 监听 <code>http://127.0.0.1:18080/mcp</code>。运行 <code>.\scripts\start_gateway.ps1</code> 会同时启动控制台和 HTTP 网关。</p>
      <p><strong>Claude Desktop / Cursor 配置:</strong></p>
      <CodeBlock :code="HTTP_CONFIG_JSON" />
      <p><strong>Python SDK 示例:</strong></p>
      <CodeBlock :code="HTTP_PY_SDK" />
      <h4>2.2 STDIO 模式 (备选)</h4>
      <p>用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:</p>
      <CodeBlock :code="STDIO_CONFIG_JSON" />
      <p>或命令行单独启动 STDIO 模式:</p>
      <CodeBlock :code="START_STDIO" />
    </section>

    <!-- 3. MCP APIs -->
    <section :id="SECTIONS[2].id" class="onboarding-section">
      <h3>{{ SECTIONS[2].title }}</h3>
      <p>当前网关对外暴露约 <strong>160</strong> 个 MCP 接口, 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在 <strong>"MCP APIs"</strong> 标签页。</p>
    </section>

    <!-- 4. 常用示例 -->
    <section :id="SECTIONS[3].id" class="onboarding-section">
      <h3>{{ SECTIONS[3].title }}</h3>
      <h4>4.1 Python SDK (推荐)</h4>
      <p>通过 <a href="https://github.com/jlowin/fastmcp" target="_blank" rel="noopener">FastMCP</a> 客户端连接 HTTP 网关 (默认 <code>http://127.0.0.1:18080/mcp</code>)。所有 tool 名、参数和返回结构以 <strong>"MCP APIs"</strong> 标签页为准。</p>
      <CodeBlock :code="EXAMPLE_PY_SDK" />
      <h4>4.2 日 K 线 (含日期范围)</h4>
      <CodeBlock :code="EXAMPLE_KLINE" />
      <h4>4.3 搜索股票</h4>
      <CodeBlock :code="EXAMPLE_SEARCH" />
      <div class="onboarding-section-meta">
        <strong>提示:</strong> 如果你的客户端只支持 stdio, 启动 STDIO 模式:
        <code>python -m src.unihive.gateway_server --transport stdio</code>。
        调试可用 MCP Inspector (<code>npx -y @modelcontextprotocol/inspector</code>), Python 可用官方 SDK。
        Windows 下 <code>cwd</code> 用项目绝对路径。
      </div>
    </section>

    <!-- 5. 故障排查 -->
    <section :id="SECTIONS[4].id" class="onboarding-section">
      <h3>{{ SECTIONS[4].title }}</h3>
      <h4>5.1 连接失败</h4>
      <ul>
        <li>检查网关是否已启动: 访问 <code>http://127.0.0.1:18080</code></li>
        <li>检查客户端配置中的 <code>cwd</code> 路径是否正确</li>
        <li>Windows 下确认 Python 已加入 PATH</li>
      </ul>
      <h4>5.2 接口返回错误</h4>
      <ul>
        <li>查看控制台"上游状态"标签页, 确认对应上游是否在线</li>
        <li>查看返回的 <code>last_error</code> 字段</li>
        <li>对应上游若被禁用, 需修改 <code>config/upstreams.yaml</code> 并重启</li>
      </ul>
      <h4>5.3 参数错误</h4>
      <ul>
        <li>股票代码由网关自动标准化 (<code>000001</code>、<code>sh600519</code>、<code>600519.SH</code>、<code>sz000001</code> 都能识别)</li>
        <li>日期统一使用 <code>YYYYMMDD</code> (如 <code>20260301</code>)</li>
        <li>具体参数以 <strong>"MCP APIs"</strong> 标签页每个 tool 行的"详情"展开为准</li>
      </ul>
      <h4>5.4 反馈渠道</h4>
      <p>遇到问题请提交 Issue 到
        <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener">github.com/unihive/unihive</a>,
        并附上控制台截图与接口返回内容。点击 <strong>"MCP APIs"</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>
    </section>
  </article>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { copyText } from '@/utils/clipboard'
import CodeBlock from './CodeBlock.vue'

interface SectionDef {
  id: string
  title: string
}

const SECTIONS: readonly SectionDef[] = [
  { id: 'sec-quickstart', title: '快速开始' },
  { id: 'sec-integration', title: '接入方式' },
  { id: 'sec-interfaces', title: 'MCP APIs' },
  { id: 'sec-examples', title: '常用示例' },
  { id: 'sec-troubleshoot', title: '故障排查' },
] as const

const router = useRouter()
const activeId = ref<string>(SECTIONS[0].id)

const START_HTTP = '.\\scripts\\start_gateway.ps1'
const START_STDIO = 'python -m src.unihive.gateway_server --transport stdio'

const HTTP_CONFIG_JSON = `{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`

const HTTP_PY_SDK = `import asyncio
from fastmcp.client import Client

async def main():
    async with Client("http://127.0.0.1:18080/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_realtime_quote", {"symbol": "600000"})
        print(result.data)

asyncio.run(main())`

const STDIO_CONFIG_JSON = `{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "src.unihive.gateway_server", "--transport", "stdio"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}`

const EXAMPLE_PY_SDK = `import asyncio
from fastmcp.client import Client

GATEWAY_URL = "http://127.0.0.1:18080/mcp"

async def main():
    async with Client(GATEWAY_URL) as client:
        # 1) 列工具名
        tools = await client.list_tools()
        print("available tools:", [t.name for t in tools])

        # 2) 调用实时行情 (任意上游的 tool 都能这样调)
        quote = await client.call_tool(
            "a_share_prices_snapshot",
            {"thscodes": "600519.SH"}
        )
        print(quote.data)

asyncio.run(main())`

const EXAMPLE_KLINE = `bar = await client.call_tool(
    "a_share_prices_historical",
    {"thscode": "600519.SH", "start_date": "2026-01-01", "end_date": "2026-03-01"}
)
print(bar.data)`

const EXAMPLE_SEARCH = `match = await client.call_tool(
    "search_stock_local",
    {"keyword": "茅台"}
)
print(match.data)`

async function copyCommand(text: string, successMsg: string) {
  try {
    await copyText(text, '')
    ElMessage.success(successMsg)
  } catch {
    ElMessage.error('复制失败, 请手动复制')
  }
}

function jumpToInterfaces() {
  void router.push({ name: 'interfaces' })
}

function scrollTo(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeId.value = id
}

// ===== scroll-spy: 跟随滚动自动高亮 =====
let observer: IntersectionObserver | null = null

function setupObserver() {
  const sections = SECTIONS
    .map((s) => document.getElementById(s.id))
    .filter((el): el is HTMLElement => Boolean(el))
  if (sections.length === 0) return

  observer = new IntersectionObserver(
    (entries) => {
      // 取第一个仍可见的 (rootMargin 让顶部 20% 视口内才算)
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => a.target.getBoundingClientRect().top - b.target.getBoundingClientRect().top)
      if (visible.length > 0) {
        activeId.value = visible[0].target.id
      }
    },
    { rootMargin: '-20% 0px -70% 0px', threshold: 0 },
  )
  sections.forEach((s) => observer!.observe(s))
}

onMounted(() => {
  setupObserver()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})
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
}

.onboarding-nav {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding: 10px 14px;
  margin-bottom: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.onboarding-nav a {
  color: var(--el-text-color-regular);
  text-decoration: none;
  padding: 4px 10px;
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