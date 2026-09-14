<template>
  <article class="ai-onboarding">
    <!-- 1. AI Agent 系统提示词 -->
    <section class="ai-section">
      <h3>AI Agent 系统提示词</h3>
      <p>复制以下提示词给你的 AI Agent, 让它能够接入 UniHive MCP:</p>
      <CodeBlock :code="AGENT_PROMPT" />
    </section>

    <!-- 2. 配置 MCP Server -->
    <section class="ai-section">
      <h3>配置 MCP Server</h3>
      <p>在你的 Agent 配置文件中添加:</p>
      <CodeBlock :code="MCP_SERVER_CONFIG" />
    </section>

    <!-- 3. 工具清单 -->
    <section class="ai-section">
      <h3>工具清单</h3>
      <p>完整工具列表 (含参数说明) 请查看 <strong>"MCP APIs"</strong> 标签页。</p>
      <el-button type="primary" @click="jumpToInterfaces">→ MCP APIs</el-button>
    </section>
  </article>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import CodeBlock from './CodeBlock.vue'

const router = useRouter()

const AGENT_PROMPT = `## 金融数据工具

你可以通过 UniHive MCP 获取中国 A 股、基金、指数的金融数据。

### MCP 配置
MCP Server 地址: http://127.0.0.1:18080/mcp

### 调用方式
使用 fastmcp 客户端:
\`\`\`python
from fastmcp import Client

async with Client("http://127.0.0.1:18080/mcp") as client:
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

const MCP_SERVER_CONFIG = `{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`

function jumpToInterfaces() {
  void router.push({ name: 'interfaces' })
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

.ai-section p {
  margin: 8px 0 12px 0;
}
</style>