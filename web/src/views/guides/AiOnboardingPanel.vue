<template>
  <article class="guide-article">
    <!-- 1. 简介 -->
    <section :id="AI_SECTIONS[0].id" class="guide-section">
      <h2>{{ AI_SECTIONS[0].title }}</h2>
      <p>
        UniHive 通过标准 MCP 协议暴露, 兼容 Claude Desktop、Cursor、Cline 等主流 AI 客户端。
        以下是 3 个最常用客户端的配置示例, 复制后粘贴到对应配置文件即可接入。
      </p>
      <p>
        推荐使用 <strong>HTTP 模式</strong> (网关进程已在运行, 客户端无需再起子进程);
        如果客户端不支持 HTTP MCP, 可回退到 <strong>STDIO 模式</strong>。
      </p>
    </section>

    <!-- 2. HTTP 模式 (推荐) -->
    <section :id="AI_SECTIONS[1].id" class="guide-section">
      <h2>{{ AI_SECTIONS[1].title }}</h2>
      <p class="guide-muted">当前网关地址: <code>{{ mcpUrl }}</code></p>

      <h3>Claude Desktop</h3>
      <p>
        配置文件路径:
        <code>%APPDATA%\Claude\claude_desktop_config.json</code> (Windows) /
        <code>~/Library/Application Support/Claude/claude_desktop_config.json</code> (macOS) /
        <code>~/.config/Claude/claude_desktop_config.json</code> (Linux)
      </p>
      <CodeBlock :code="httpConfig" />

      <h3>Cursor</h3>
      <p>
        配置文件路径: <code>~/.cursor/mcp.json</code> (全局) 或
        项目根目录 <code>.cursor/mcp.json</code> (项目级优先)
      </p>
      <CodeBlock :code="httpConfig" />

      <h3>Cline (VS Code 扩展)</h3>
      <p>
        在 VS Code 左侧活动栏点击 Cline 图标 → 顶栏 "MCP Servers" → "Configure", Cline 会打开
        <code>cline_mcp_settings.json</code>。字段与 Cursor 基本一致。
      </p>
      <p class="guide-muted">
        各客户端版本对远程 MCP 的 schema 要求并不完全统一。若粘贴后被拒绝, 请按该客户端文档
        补上传输类型字段 (通常形如 <code>"type": "http"</code> 或 <code>"streamableHttp"</code>)。
      </p>
      <CodeBlock :code="httpConfig" />
    </section>

    <!-- 3. STDIO 模式 (备选) -->
    <section :id="AI_SECTIONS[2].id" class="guide-section">
      <h2>{{ AI_SECTIONS[2].title }}</h2>
      <p>
        适用于不支持 HTTP MCP 的旧客户端。需要在配置文件路径下用 <code>command</code> + <code>args</code>
        + <code>cwd</code> 三段指定启动方式。
      </p>
      <div class="guide-callout">
        <strong>注意:</strong> <code>cwd</code> 必须替换为<strong>你本地 UniHive 项目根目录的绝对路径</strong>,
        例如 <code>D:/fintech_workspace/unihive</code>。下方 JSON 中的 <code>{{ STDIO_CWD_PLACEHOLDER }}</code>
        占位符不会自动展开, 复制后请手动替换。
      </div>
      <CodeBlock :code="stdioConfig" />
    </section>

    <!-- 4. AI Agent 系统提示词 -->
    <section :id="AI_SECTIONS[3].id" class="guide-section">
      <h2>{{ AI_SECTIONS[3].title }}</h2>
      <p>
        把以下提示词添加到 AI 客户端的 system prompt / custom instructions 字段,
        让 Agent 自动知道如何调用 UniHive 工具。
      </p>
      <p class="guide-muted">
        提示词不写死具体工具名 — 工具列表以控制台 <strong>「MCP APIs」</strong> 页运行时为准,
        新工具上线/下线后提示词无需同步修改。
      </p>
      <CodeBlock :code="AGENT_PROMPT" />
    </section>

    <!-- 5. 验证是否接入成功 -->
    <section :id="AI_SECTIONS[4].id" class="guide-section">
      <h2>{{ AI_SECTIONS[4].title }}</h2>
      <ol>
        <li>重启 AI 客户端 (Claude Desktop 完全退出后再打开)。</li>
        <li v-if="toolCount != null">
          在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的 <strong>{{ toolCount }}</strong> 个工具
          (按上游分组)。
        </li>
        <li v-else>
          在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的工具 (按上游分组)。
        </li>
        <li>向 AI 提一个简单请求: "搜索股票 茅台"。若返回结果则接入成功。</li>
      </ol>
      <p>若失败, 按以下顺序排查:</p>
      <ul>
        <li>确认网关已启动: 浏览器访问 <code>{{ origin }}</code> 能看到控制台首页。</li>
        <li>查看 <strong>「网关运行监控 → 上游总览」</strong>, 确认上游是否全绿。</li>
        <li>查看客户端日志: Claude Desktop 的 "Developer" 菜单 / Cursor 的 "Output → MCP"。</li>
      </ul>
    </section>

    <!-- 6. 自定义端口 -->
    <section :id="AI_SECTIONS[5].id" class="guide-section">
      <h2>{{ AI_SECTIONS[5].title }}</h2>
      <p>
        如果你修改了启动端口 (例如改成 <code>19080</code>), 上面 JSON 示例中的 URL 也要相应替换。
        推荐保持默认 <code>18080</code> 以便与控制台共用。
      </p>
      <div class="guide-callout">
        <strong>改端口 → 改启动脚本 → 改 Claude/Cursor/Cline JSON 的 URL</strong>,
        三处保持一致。三处都不改也会工作, 但 URL 里的端口必须与网关实际监听端口对齐。
      </div>
    </section>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import CodeBlock from './CodeBlock.vue'
import { AI_SECTIONS } from './format'
import { gatewayOrigin, mcpHttpUrl } from '@/utils/gatewayUrl'

defineProps<{
  /** 运行时工具数; null = 尚未拿到 (不编造数值, 改由文案省略数字)。 */
  toolCount: number | null
}>()

const origin = computed(() => gatewayOrigin())
const mcpUrl = computed(() => mcpHttpUrl())

// --- HTTP 配置 (3 个客户端 schema 基本一致, 共用同一份) ---
const httpConfig = computed(() =>
  JSON.stringify({ mcpServers: { unihive: { url: mcpHttpUrl() } } }, null, 2),
)

/** STDIO 配置的 cwd 占位符 — 复制后用户必须手动替换。 */
const STDIO_CWD_PLACEHOLDER = '<替换为你本地 UniHive 项目根绝对路径>'

// --- STDIO 配置 ---
const stdioConfig = computed(() =>
  JSON.stringify(
    {
      mcpServers: {
        unihive: {
          command: 'python',
          args: ['-m', 'src.unihive.gateway_server', '--transport', 'stdio'],
          cwd: STDIO_CWD_PLACEHOLDER,
        },
      },
    },
    null,
    2,
  ),
)

// --- AI Agent 系统提示词 (通用, 不写死工具名) ---
const AGENT_PROMPT = `## 金融数据工具

你可以通过 UniHive MCP 获取中国 A 股、基金、指数的金融数据。

### MCP 配置
MCP Server 地址: ${mcpHttpUrl()}

### 调用方式
使用 fastmcp 客户端:
\`\`\`python
from fastmcp import Client

async with Client("${mcpHttpUrl()}") as client:
    result = await client.call_tool("tool_name", {...})
\`\`\`

### 工具发现
工具列表请通过 client.list_tools() 在运行时获取 — UniHive 持续接入新数据源,
不要在 prompt 里写死 tool 名, 也不要假设历史调用过的 tool 永远存在。

### 代码格式约定
- 上海: 600519.SH, 深圳: 000001.SZ
- 基金: 000001.OF
- 概念板块: 886042.TI
- 日期: YYYYMMDD (如 20260301)`
</script>
