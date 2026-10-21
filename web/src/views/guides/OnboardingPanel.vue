<template>
  <article class="guide-article">
    <!-- 1. 快速开始 -->
    <section :id="HUMAN_SECTIONS[0].id" class="guide-section">
      <h2>
        <span class="guide-h2-num">{{ HUMAN_SECTIONS[0].num }}.</span>{{ HUMAN_SECTIONS[0].title }}
      </h2>
      <p>UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。</p>

      <h3>启动网关</h3>
      <p>网关默认以 <strong>HTTP 模式</strong>启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:</p>
      <CodeBlock :code="START_HTTP" />
      <p>启动后, 控制台和 HTTP 网关共用端口 <code>http://127.0.0.1:18080</code> (MCP 接口在 <code>/mcp</code> 子路径下)。</p>
      <p class="guide-muted">
        macOS / Linux 没有该 PowerShell 脚本, 在项目根目录执行
        <code>python -m src.unihive.gateway_server --transport http</code> 等价
        (工作目录必须是项目根, <code>-m</code> 才解析得到 <code>src.unihive</code>)。
      </p>

      <h3>配置管理</h3>
      <p>
        日常配置在控制台<strong>「配置信息」</strong>页维护, 事实源是配置库 <code>data/config.db</code> —
        上游启停、工具契约、FUYAO TTL 等改动<strong>热生效, 无需重启</strong>。空库首次启动时自动从出厂
        YAML 模板 (<code>config/upstreams.yaml</code> + <code>config/tools_*.yaml</code>) seed;
        监听端口、控制台账号等 bootstrap 配置仍在 <code>config/config.yaml</code>。
      </p>
    </section>

    <!-- 2. 数据源与路由 -->
    <section :id="HUMAN_SECTIONS[1].id" class="guide-section">
      <h2>
        <span class="guide-h2-num">{{ HUMAN_SECTIONS[1].num }}.</span>{{ HUMAN_SECTIONS[1].title }}
      </h2>
      <p>网关背后是 <strong>9 个上游端点</strong>, 按工具路由分发, 失败自动降级:</p>
      <ul>
        <li><strong>mootdx2</strong> — 通达信本地 TCP: 行情 / K 线 / 板块 (含离线指数 / ETF)</li>
        <li><strong>tdx_quant</strong> — 通达信量化终端: 行情 / 板块 / 交易日 / 财务 / 公式 / 交易 / 预警</li>
        <li>
          <strong>fuyao_ashare</strong> / <strong>fuyao_index</strong> / <strong>fuyao_meta</strong> /
          <strong>fuyao_fund</strong> — 同花顺 aicubes.cn 的 4 个端点: A 股行情与财报 / 指数与板块目录 /
          跨市场 ticker / 基金 (需 internet)
        </li>
        <li>
          <strong>omni</strong> — OMNIDATA 本地 SQLite: 行业 / 概念 / 地域 / 风格板块 (THS + TDX 双源,
          需先在「OMNI板块管理」页同步 <code>data/board.db</code>)
        </li>
        <li>
          <strong>thsdk</strong> — 同花顺 thsdk (社区包): 16 个只读工具常驻, 自选写工具需
          <code>ALLOW_WATCHLIST_WRITE=true</code>
        </li>
        <li><strong>THS-LOCAL-GATEWAY</strong> — FQGate 本地 MCP 网关: 行情 / K 线 / 资讯 / wencai / IPO / 期权</li>
      </ul>
      <p class="guide-muted">
        上表是能力划分, 不含运行态; 每个上游的启用状态与连通性以「配置信息 → 上游」页为准。
      </p>

      <h3>路由与降级链</h3>
      <p>
        每个 gateway 工具声明一个<strong>路由键</strong> (<code>routing</code>), 写在所属数据源的
        <code>config/tools_&lt;源&gt;.yaml</code> 里。路由键对应一条调用链: 链定义在
        <code>config/upstreams.yaml</code> 的 <code>routing</code> 段
        (<code>chain: [上游A, 上游B]</code>, A 失败自动试 B, 一直到最后);
        未定义链时退回 <code>upstream_tool_mapping</code> 的单上游映射。
      </p>
      <p>
        控制台<strong>「配置信息 → 工具映射」</strong>可查看每个路由键的实际调用链,
        <strong>「工具规格」</strong>页可热编辑工具的路由键与缓存档。
      </p>

      <h3>缓存策略</h3>
      <p>
        按工具的 <code>cache_ttl_key</code> 分档, 档位见 <code>config/upstreams.yaml</code> 的
        <code>cache.ttl</code> (historical / fundamentals / special_data / index_data / fund_* 等)。
        <strong>时段感知</strong>: 不超过 1 小时的短档在交易时段按该值缓存, 非交易时段自动延长到下一个
        变化边界 (9:30 开盘 / 13:00 午后开盘 / 次日零点)。
      </p>
      <p>
        <code>realtime_quote</code> 故意不配置档位 —— 实时行情不缓存。本地端 (mootdx2 / tdx_quant / thsdk)
        不缓存; 混合链含 fuyao 时远程响应仍按档缓存。
      </p>
    </section>

    <!-- 3. MCP APIs -->
    <section :id="HUMAN_SECTIONS[2].id" class="guide-section">
      <h2>
        <span class="guide-h2-num">{{ HUMAN_SECTIONS[2].num }}.</span>{{ HUMAN_SECTIONS[2].title }}
      </h2>
      <p v-if="toolCount != null">
        当前网关对外暴露 <strong>{{ toolCount }}</strong> 个 MCP 工具 (9 个上游端点), 按上游自动分组、
        路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在 <strong>「MCP APIs」</strong> 页。
      </p>
      <p v-else>
        网关对外暴露的 MCP 工具按上游自动分组、路由与缓存。精确数量与完整列表
        (含每个 tool 的描述、参数、缓存策略) 以 <strong>「MCP APIs」</strong> 页的运行时列表为准。
      </p>
    </section>

    <!-- 4. 故障排查 -->
    <section :id="HUMAN_SECTIONS[3].id" class="guide-section">
      <h2>
        <span class="guide-h2-num">{{ HUMAN_SECTIONS[3].num }}.</span>{{ HUMAN_SECTIONS[3].title }}
      </h2>

      <h3>连接失败</h3>
      <ul>
        <li>检查网关是否已启动: 访问 <code>http://127.0.0.1:18080</code></li>
        <li>检查客户端配置中的 <code>cwd</code> 路径是否正确</li>
        <li>Windows 下确认 Python 已加入 PATH</li>
      </ul>

      <h3>接口返回错误</h3>
      <ul>
        <li>查看控制台「网关运行监控 → 上游总览」, 确认对应上游是否在线</li>
        <li>查看返回的 <code>last_error</code> 字段</li>
        <li>
          对应上游若被禁用, 可在控制台「配置信息 → 上游」标签页直接热切换启用
          (实时生效并持久化到配置库 <code>data/config.db</code>), 无需重启
        </li>
      </ul>

      <h3>参数错误</h3>
      <ul>
        <li>股票代码由网关自动标准化 (<code>000001</code>、<code>sh600519</code>、<code>600519.SH</code>、<code>sz000001</code> 都能识别)</li>
        <li>日期统一使用 <code>YYYYMMDD</code> (如 <code>20260301</code>)</li>
        <li>具体参数以 <strong>「MCP APIs」</strong> 页每个 tool 行的「详情」展开为准</li>
      </ul>

      <h3>反馈渠道</h3>
      <p>
        遇到问题请提交 Issue 到
        <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener">github.com/unihive/unihive</a>,
        并附上控制台截图与接口返回内容。点击 <strong>「MCP APIs」</strong> → 对应 tool 行 → 详情按钮即可拿到
        完整 tool 名与参数。
      </p>
    </section>
  </article>
</template>

<script setup lang="ts">
import CodeBlock from './CodeBlock.vue'
import { HUMAN_SECTIONS } from './format'

defineProps<{
  /** 运行时工具数; null = 尚未拿到 (不编造数值, 改由文案指向控制台)。 */
  toolCount: number | null
}>()

const START_HTTP = '.\\scripts\\start_gateway.ps1'
</script>
