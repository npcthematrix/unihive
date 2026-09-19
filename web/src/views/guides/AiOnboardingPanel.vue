<template>
  <article class="ai-onboarding">
    <!-- 1. 简介 -->
    <section class="ai-section">
      <h3>选择你的 AI 客户端</h3>
      <p>
        UniHive 通过标准 MCP 协议暴露, 兼容 Claude Desktop、Cursor、Cline 等主流 AI 客户端。
        以下是 3 个最常用客户端的配置示例, 复制后粘贴到对应配置文件即可接入。
      </p>
      <p>
        推荐使用 <strong>HTTP 模式</strong>(无需启动额外进程);如果客户端不支持 HTTP MCP,
        可回退到 <strong>STDIO 模式</strong>。
      </p>
    </section>

    <!-- 2. HTTP 模式 (推荐) -->
    <section class="ai-section">
      <h3>HTTP 模式 (推荐)</h3>
      <p class="muted">
        当前网关地址: <code>{{ mcpUrl }}</code>
      </p>

      <!-- 2.1 Claude Desktop -->
      <h4>Claude Desktop</h4>
      <p>
        配置文件路径:
        <code>%APPDATA%\Claude\claude_desktop_config.json</code> (Windows) /
        <code>~/Library/Application Support/Claude/claude_desktop_config.json</code> (macOS) /
        <code>~/.config/Claude/claude_desktop_config.json</code> (Linux)
      </p>
      <CodeBlock :code="claudeDesktopHttp" />

      <!-- 2.2 Cursor -->
      <h4>Cursor</h4>
      <p>
        配置文件路径: <code>~/.cursor/mcp.json</code>(全局) 或
        项目根目录 <code>.cursor/mcp.json</code>(项目级优先)
      </p>
      <CodeBlock :code="cursorHttp" />

      <!-- 2.3 Cline (VS Code) -->
      <h4>Cline (VS Code 扩展)</h4>
      <p>
        在 VS Code 左侧活动栏点击 Cline 图标 → 顶栏 "MCP Servers" → "Configure" 按钮, 会自动打开
        <code>cline_mcp_settings.json</code>。配置 schema 与 Cursor 相同。
      </p>
      <CodeBlock :code="clineHttp" />
    </section>

    <!-- 3. STDIO 模式 (备选) -->
    <section class="ai-section">
      <h3>STDIO 模式 (备选)</h3>
      <p>
        适用于不支持 HTTP MCP 的旧客户端。需要在配置文件路径下用 <code>command</code> + <code>args</code>
        + <code>cwd</code> 三段指定启动方式。<code>cwd</code> 必须指向 UniHive 项目根目录。
      </p>
      <CodeBlock :code="stdioConfig" />
    </section>

    <!-- 4. AI Agent 系统提示词 (可选折叠) -->
    <section class="ai-section">
      <h3>AI Agent 系统提示词 (可选)</h3>
      <p>
        把以下提示词添加到 AI 客户端的 system prompt / custom instructions 字段,
        让 Agent 自动知道如何调用 UniHive 工具。
      </p>
      <CodeBlock :code="AGENT_PROMPT" />
    </section>

    <!-- 5. 验证是否接入成功 -->
    <section class="ai-section">
      <h3>验证是否接入成功</h3>
      <ol>
        <li>重启 AI 客户端 (Claude Desktop 完全退出后再打开)。</li>
        <li>
          在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的
          {{ toolCountHint }} 个工具 (按上游分组)。
        </li>
        <li>
          向 AI 提一个简单请求: "搜索股票 茅台"。若返回结果则接入成功。
        </li>
      </ol>
      <p>若失败, 按以下顺序排查:</p>
      <ul>
        <li>确认网关已启动: 浏览器访问 <code>{{ origin }}</code> 能看到控制台首页。</li>
        <li>查看 <strong>"上游状态"</strong> 标签页, 确认上游是否全绿。</li>
        <li>查看客户端日志: Claude Desktop 的 "Developer" 菜单 / Cursor 的 "Output → MCP"。</li>
      </ul>
      <el-button type="primary" @click="jumpToStatus">→ 上游状态</el-button>
    </section>

    <!-- 6. 自定义端口 -->
    <section class="ai-section">
      <h3>自定义端口</h3>
      <p>
        如果你修改了启动端口 (例如改成 <code>19080</code>), 上面 JSON 示例中的 URL 也要相应替换。
        推荐保持默认 <code>18080</code> 以便与控制台共用。
      </p>
    </section>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import CodeBlock from './CodeBlock.vue'
import { gatewayOrigin, mcpHttpUrl } from '@/utils/gatewayUrl'

const router = useRouter()

const origin = computed(() => gatewayOrigin())
const mcpUrl = computed(() => mcpHttpUrl())

// --- HTTP 配置 (3 个客户端共享同一 schema, 仅文件名差异) ---
function buildHttpConfig(): string {
  return JSON.stringify(
    { mcpServers: { unihive: { url: mcpHttpUrl() } } },
    null,
    2,
  )
}
const claudeDesktopHttp = computed(buildHttpConfig)
const cursorHttp = computed(buildHttpConfig)
const clineHttp = computed(buildHttpConfig)

// --- STDIO 配置 (3 个客户端共享同一 schema) ---
const stdioConfig = computed(() =>
  JSON.stringify(
    {
      mcpServers: {
        unihive: {
          command: 'python',
          args: ['-m', 'src.unihive.gateway_server', '--transport', 'stdio'],
          cwd: 'D:/fintech_workspace/unihive',
        },
      },
    },
    null,
    2,
  ),
)

// --- 工具数量提示 (从 runtime 接口估算) ---
// 实际数量在运行时由控制台 "MCP APIs" 页显示;这里给个约数避免误导用户
// (2026-09: 5 套上游合计 290; FQGate 87 + fuyao 67 + tdx_quant 54 + mootdx2 33 + omni 5 等)。
const toolCountHint = '~290'

// --- AI Agent 系统提示词 (可选,作为 system prompt 模板) ---
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

### 常用工具
- 实时行情: a_share_prices_snapshot (参数: thscodes, 如 "600519.SH")
- 历史 K 线: a_share_prices_historical (参数: thscode, start_date, end_date)
- 股票搜索: search_stock_local (参数: keyword, 如 "茅台")
- 板块成分: a_share_index_constituents (参数: thscode, 如 "886042.TI")
- 基金资料: fund_profile_detail (参数: thscode, 如 "000001.OF")
- 交易日历: a_share_calendar_trading_days (参数: date)

### 代码格式
上海: 600519.SH, 深圳: 000001.SZ
基金: 000001.OF
概念板块: 886042.TI`

function jumpToStatus() {
  void router.push({ name: 'status' })
}
</script>

<style scoped>
.ai-onboarding {
  font-size: 14px;
  line-height: 1.65;
  color: var(--el-text-color-primary);
}

.ai-section {
  padding: 12px 0 24px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}

.ai-section:last-child {
  border-bottom: none;
}

.ai-section h3 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 12px 0;
}

.ai-section h4 {
  font-size: 15px;
  font-weight: 600;
  margin: 16px 0 8px 0;
}

.ai-section p {
  margin: 8px 0 12px 0;
}

.ai-section ol,
.ai-section ul {
  margin: 8px 0;
  padding-left: 22px;
}

.ai-section .muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.ai-section code {
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 3px;
  font-family: var(--el-font-family-monospace, 'Consolas', 'Monaco', monospace);
  font-size: 13px;
  word-break: break-all;
}
</style>