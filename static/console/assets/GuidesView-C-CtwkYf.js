import{G as I,aj as w,L as v,M as C,P as t,a0 as l,u as U,W as m,_ as n,$ as u,c as h,aD as A,d as V,A as Y,F as x,ae as R,ac as j,S as B,aG as O,k as P,w as G,O as K,X as D,a1 as H,aC as F}from"./vue-hmD7xhy6.js";import{p as W}from"./hashTab-C4jxjRmY.js";import{o as J,c as S}from"./element-plus-CGe9KhpW.js";import{c as L}from"./clipboard-R61eHf4S.js";import{_ as k}from"./index-DgXqvyrn.js";const $=["human","ai"],X={human:"人工接入",ai:"AI 接入"},z={class:"code-block"},Z={class:"code-block-bar"},Q=I({__name:"CodeBlock",props:{code:{}},setup(T){const a=T,f=h(!1),s=h(!1);async function p(){f.value=!0;try{await L(a.code,""),s.value=!0,S.success("已复制"),setTimeout(()=>{s.value=!1},1500)}catch{S.error("复制失败, 请手动复制")}finally{f.value=!1}}return(g,i)=>{const d=w("el-button");return v(),C("div",z,[t("div",Z,[l(d,{size:"small",icon:U(J),class:"copy-btn",loading:f.value,onClick:p},{default:m(()=>[n(u(s.value?"已复制":"复制"),1)]),_:1},8,["icon","loading"])]),t("pre",null,[t("code",null,u(T.code),1)])])}}}),r=k(Q,[["__scopeId","data-v-6f4d9e48"]]),tt={class:"onboarding"},ot={class:"onboarding-toolbar"},et={class:"onboarding-nav","aria-label":"章节导航"},nt=["href","onClick"],at=["id"],lt={class:"onboarding-toolbox"},st=["id"],it=["id"],dt=["id"],rt=["id"],E=".\\scripts\\start_gateway.ps1",q="python -m src.unihive.gateway_server --transport stdio",ut=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,ct=`import asyncio
from fastmcp.client import Client

async def main():
    async with Client("http://127.0.0.1:18080/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_realtime_quote", {"symbol": "600000"})
        print(result.data)

asyncio.run(main())`,pt=`{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "src.unihive.gateway_server", "--transport", "stdio"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}`,ft=`import asyncio
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

asyncio.run(main())`,vt=`bar = await client.call_tool(
    "a_share_prices_historical",
    {"thscode": "600519.SH", "start_date": "2026-01-01", "end_date": "2026-03-01"}
)
print(bar.data)`,mt=`match = await client.call_tool(
    "search_stock_local",
    {"keyword": "茅台"}
)
print(match.data)`,gt=I({__name:"OnboardingPanel",setup(T){const a=[{id:"sec-quickstart",title:"快速开始"},{id:"sec-integration",title:"接入方式"},{id:"sec-interfaces",title:"MCP APIs"},{id:"sec-examples",title:"常用示例"},{id:"sec-troubleshoot",title:"故障排查"}],f=A(),s=h(a[0].id);async function p(e,o){try{await L(e,""),S.success(o)}catch{S.error("复制失败, 请手动复制")}}function g(){f.push({name:"interfaces"})}function i(e){const o=document.getElementById(e);o&&(o.scrollIntoView({behavior:"smooth",block:"start"}),s.value=e)}let d=null;function y(){const e=a.map(o=>document.getElementById(o.id)).filter(o=>!!o);e.length!==0&&(d=new IntersectionObserver(o=>{const _=o.filter(c=>c.isIntersecting).sort((c,M)=>c.target.getBoundingClientRect().top-M.target.getBoundingClientRect().top);_.length>0&&(s.value=_[0].target.id)},{rootMargin:"-20% 0px -70% 0px",threshold:0}),e.forEach(o=>d.observe(o)))}return V(()=>{y()}),Y(()=>{d==null||d.disconnect(),d=null}),(e,o)=>{const _=w("el-button");return v(),C("article",tt,[t("div",ot,[l(_,{type:"primary",onClick:g},{default:m(()=>[...o[2]||(o[2]=[n("→ MCP APIs",-1)])]),_:1})]),t("nav",et,[(v(),C(x,null,R(a,c=>t("a",{key:c.id,href:`#${c.id}`,class:B({active:s.value===c.id}),onClick:j(M=>i(c.id),["prevent"])},u(c.title),11,nt)),64))]),t("section",{id:a[0].id,class:"onboarding-section"},[t("h3",null,u(a[0].title),1),o[5]||(o[5]=t("p",null,"UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。",-1)),o[6]||(o[6]=t("h4",null,"1.1 启动网关",-1)),o[7]||(o[7]=t("p",null,[n("网关默认以 "),t("strong",null,"HTTP 模式"),n("启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:")],-1)),t("div",lt,[l(_,{onClick:o[0]||(o[0]=c=>p(E,"已复制 HTTP 启动命令"))},{default:m(()=>[...o[3]||(o[3]=[n("复制 HTTP 启动命令",-1)])]),_:1}),l(_,{onClick:o[1]||(o[1]=c=>p(q,"已复制 STDIO 启动命令"))},{default:m(()=>[...o[4]||(o[4]=[n("复制 STDIO 启动命令",-1)])]),_:1})]),l(r,{code:E}),o[8]||(o[8]=O("<p data-v-ffaa0e59>启动后, 控制台和 HTTP 网关共用端口 <code data-v-ffaa0e59>http://127.0.0.1:18080</code> (MCP 接口在 <code data-v-ffaa0e59>/mcp</code> 子路径下)。</p><h4 data-v-ffaa0e59>1.2 配置管理</h4><p data-v-ffaa0e59>日常配置在控制台<strong data-v-ffaa0e59>「配置信息」</strong>页维护, 事实源是配置库 <code data-v-ffaa0e59>data/config.db</code> — 上游启停、工具契约、FUYAO TTL 等改动<strong data-v-ffaa0e59>热生效, 无需重启</strong>。空库首次启动时自动从出厂 YAML 模板 (<code data-v-ffaa0e59>config/upstreams.yaml</code> + <code data-v-ffaa0e59>config/tools_*.yaml</code>) seed; 监听端口、控制台账号等 bootstrap 配置仍在 <code data-v-ffaa0e59>config/config.yaml</code>。</p>",3))],8,at),t("section",{id:a[1].id,class:"onboarding-section"},[t("h3",null,u(a[1].title),1),o[9]||(o[9]=t("h4",null,"2.1 HTTP 模式 (推荐)",-1)),o[10]||(o[10]=t("p",null,[n("网关以 "),t("strong",null,"Streamable HTTP"),n(" 模式对外提供 MCP 接口, 监听 "),t("code",null,"http://127.0.0.1:18080/mcp"),n("。运行 "),t("code",null,".\\scripts\\start_gateway.ps1"),n(" 会同时启动控制台和 HTTP 网关。")],-1)),o[11]||(o[11]=t("p",null,[t("strong",null,"Claude Desktop / Cursor 配置:")],-1)),l(r,{code:ut}),o[12]||(o[12]=t("p",null,[t("strong",null,"Python SDK 示例:")],-1)),l(r,{code:ct}),o[13]||(o[13]=t("h4",null,"2.2 STDIO 模式 (备选)",-1)),o[14]||(o[14]=t("p",null,"用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:",-1)),l(r,{code:pt}),o[15]||(o[15]=t("p",null,"或命令行单独启动 STDIO 模式:",-1)),l(r,{code:q})],8,st),t("section",{id:a[2].id,class:"onboarding-section"},[t("h3",null,u(a[2].title),1),o[16]||(o[16]=t("p",null,[n("当前网关对外暴露约 "),t("strong",null,"290"),n(" 个 MCP 接口 (5 套上游, 精确数量以 "),t("strong",null,'"MCP APIs"'),n(" 标签页运行时列表为准), 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在该标签页。")],-1))],8,it),t("section",{id:a[3].id,class:"onboarding-section"},[t("h3",null,u(a[3].title),1),o[17]||(o[17]=t("h4",null,"4.1 Python SDK (推荐)",-1)),o[18]||(o[18]=t("p",null,[n("通过 "),t("a",{href:"https://github.com/jlowin/fastmcp",target:"_blank",rel:"noopener"},"FastMCP"),n(" 客户端连接 HTTP 网关 (默认 "),t("code",null,"http://127.0.0.1:18080/mcp"),n(")。所有 tool 名、参数和返回结构以 "),t("strong",null,'"MCP APIs"'),n(" 标签页为准。")],-1)),l(r,{code:ft}),o[19]||(o[19]=t("h4",null,"4.2 日 K 线 (含日期范围)",-1)),l(r,{code:vt}),o[20]||(o[20]=t("h4",null,"4.3 搜索股票",-1)),l(r,{code:mt}),o[21]||(o[21]=t("div",{class:"onboarding-section-meta"},[t("strong",null,"提示:"),n(" 如果你的客户端只支持 stdio, 启动 STDIO 模式: "),t("code",null,"python -m src.unihive.gateway_server --transport stdio"),n("。 调试可用 MCP Inspector ("),t("code",null,"npx -y @modelcontextprotocol/inspector"),n("), Python 可用官方 SDK。 Windows 下 "),t("code",null,"cwd"),n(" 用项目绝对路径。 ")],-1))],8,dt),t("section",{id:a[4].id,class:"onboarding-section"},[t("h3",null,u(a[4].title),1),o[22]||(o[22]=O('<h4 data-v-ffaa0e59>5.1 连接失败</h4><ul data-v-ffaa0e59><li data-v-ffaa0e59>检查网关是否已启动: 访问 <code data-v-ffaa0e59>http://127.0.0.1:18080</code></li><li data-v-ffaa0e59>检查客户端配置中的 <code data-v-ffaa0e59>cwd</code> 路径是否正确</li><li data-v-ffaa0e59>Windows 下确认 Python 已加入 PATH</li></ul><h4 data-v-ffaa0e59>5.2 接口返回错误</h4><ul data-v-ffaa0e59><li data-v-ffaa0e59>查看控制台&quot;上游数据PROVIDERs&quot;标签页, 确认对应上游是否在线</li><li data-v-ffaa0e59>查看返回的 <code data-v-ffaa0e59>last_error</code> 字段</li><li data-v-ffaa0e59>对应上游若被禁用, 可在控制台&quot;配置信息 → 上游&quot;标签页直接热切换启用 (实时生效并持久化到配置库 <code data-v-ffaa0e59>data/config.db</code>), 无需重启</li></ul><h4 data-v-ffaa0e59>5.3 参数错误</h4><ul data-v-ffaa0e59><li data-v-ffaa0e59>股票代码由网关自动标准化 (<code data-v-ffaa0e59>000001</code>、<code data-v-ffaa0e59>sh600519</code>、<code data-v-ffaa0e59>600519.SH</code>、<code data-v-ffaa0e59>sz000001</code> 都能识别)</li><li data-v-ffaa0e59>日期统一使用 <code data-v-ffaa0e59>YYYYMMDD</code> (如 <code data-v-ffaa0e59>20260301</code>)</li><li data-v-ffaa0e59>具体参数以 <strong data-v-ffaa0e59>&quot;MCP APIs&quot;</strong> 标签页每个 tool 行的&quot;详情&quot;展开为准</li></ul><h4 data-v-ffaa0e59>5.4 反馈渠道</h4><p data-v-ffaa0e59>遇到问题请提交 Issue 到 <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener" data-v-ffaa0e59>github.com/unihive/unihive</a>, 并附上控制台截图与接口返回内容。点击 <strong data-v-ffaa0e59>&quot;MCP APIs&quot;</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>',8))],8,rt)])}}}),_t=k(gt,[["__scopeId","data-v-ffaa0e59"]]);function N(){return typeof location>"u"?"http://127.0.0.1:18080":location.origin}function b(){return`${N()}/mcp`}const Ct={class:"ai-onboarding"},Tt={class:"ai-section"},Pt={class:"muted"},yt={class:"ai-section"},bt={class:"ai-section"},ht={class:"ai-section"},St="~290",It=I({__name:"AiOnboardingPanel",setup(T){const a=A(),f=P(()=>N()),s=P(()=>b()),p=P(()=>JSON.stringify({mcpServers:{unihive:{url:b()}}},null,2)),g=P(()=>JSON.stringify({mcpServers:{unihive:{command:"python",args:["-m","src.unihive.gateway_server","--transport","stdio"],cwd:"D:/fintech_workspace/unihive"}}},null,2)),i=`## 金融数据工具

你可以通过 UniHive MCP 获取中国 A 股、基金、指数的金融数据。

### MCP 配置
MCP Server 地址: ${b()}

### 调用方式
使用 fastmcp 客户端:
\`\`\`python
from fastmcp import Client

async with Client("${b()}") as client:
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
概念板块: 886042.TI`;function d(){a.push({name:"status"})}return(y,e)=>{const o=w("el-button");return v(),C("article",Ct,[e[21]||(e[21]=t("section",{class:"ai-section"},[t("h3",null,"选择你的 AI 客户端"),t("p",null," UniHive 通过标准 MCP 协议暴露, 兼容 Claude Desktop、Cursor、Cline 等主流 AI 客户端。 以下是 3 个最常用客户端的配置示例, 复制后粘贴到对应配置文件即可接入。 "),t("p",null,[n(" 推荐使用 "),t("strong",null,"HTTP 模式"),n("(无需启动额外进程);如果客户端不支持 HTTP MCP, 可回退到 "),t("strong",null,"STDIO 模式"),n("。 ")])],-1)),t("section",Tt,[e[1]||(e[1]=t("h3",null,"HTTP 模式 (推荐)",-1)),t("p",Pt,[e[0]||(e[0]=n(" 当前网关地址: ",-1)),t("code",null,u(s.value),1)]),e[2]||(e[2]=t("h4",null,"Claude Desktop",-1)),e[3]||(e[3]=t("p",null,[n(" 配置文件路径: "),t("code",null,"%APPDATA%\\Claude\\claude_desktop_config.json"),n(" (Windows) / "),t("code",null,"~/Library/Application Support/Claude/claude_desktop_config.json"),n(" (macOS) / "),t("code",null,"~/.config/Claude/claude_desktop_config.json"),n(" (Linux) ")],-1)),l(r,{code:p.value},null,8,["code"]),e[4]||(e[4]=t("h4",null,"Cursor",-1)),e[5]||(e[5]=t("p",null,[n(" 配置文件路径: "),t("code",null,"~/.cursor/mcp.json"),n("(全局) 或 项目根目录 "),t("code",null,".cursor/mcp.json"),n("(项目级优先) ")],-1)),l(r,{code:p.value},null,8,["code"]),e[6]||(e[6]=t("h4",null,"Cline (VS Code 扩展)",-1)),e[7]||(e[7]=t("p",null,[n(' 在 VS Code 左侧活动栏点击 Cline 图标 → 顶栏 "MCP Servers" → "Configure" 按钮, 会自动打开 '),t("code",null,"cline_mcp_settings.json"),n("。配置 schema 与 Cursor 相同。 ")],-1)),l(r,{code:p.value},null,8,["code"])]),t("section",yt,[e[8]||(e[8]=t("h3",null,"STDIO 模式 (备选)",-1)),e[9]||(e[9]=t("p",null,[n(" 适用于不支持 HTTP MCP 的旧客户端。需要在配置文件路径下用 "),t("code",null,"command"),n(" + "),t("code",null,"args"),n(" + "),t("code",null,"cwd"),n(" 三段指定启动方式。"),t("code",null,"cwd"),n(" 必须指向 UniHive 项目根目录。 ")],-1)),l(r,{code:g.value},null,8,["code"])]),t("section",bt,[e[10]||(e[10]=t("h3",null,"AI Agent 系统提示词 (可选)",-1)),e[11]||(e[11]=t("p",null," 把以下提示词添加到 AI 客户端的 system prompt / custom instructions 字段, 让 Agent 自动知道如何调用 UniHive 工具。 ",-1)),l(r,{code:i})]),t("section",ht,[e[19]||(e[19]=t("h3",null,"验证是否接入成功",-1)),t("ol",null,[e[12]||(e[12]=t("li",null,"重启 AI 客户端 (Claude Desktop 完全退出后再打开)。",-1)),t("li",null," 在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的 "+u(St)+" 个工具 (按上游分组)。 "),e[13]||(e[13]=t("li",null,' 向 AI 提一个简单请求: "搜索股票 茅台"。若返回结果则接入成功。 ',-1))]),e[20]||(e[20]=t("p",null,"若失败, 按以下顺序排查:",-1)),t("ul",null,[t("li",null,[e[14]||(e[14]=n("确认网关已启动: 浏览器访问 ",-1)),t("code",null,u(f.value),1),e[15]||(e[15]=n(" 能看到控制台首页。",-1))]),e[16]||(e[16]=t("li",null,[n("查看 "),t("strong",null,'"上游数据PROVIDERs"'),n(" 标签页, 确认上游是否全绿。")],-1)),e[17]||(e[17]=t("li",null,'查看客户端日志: Claude Desktop 的 "Developer" 菜单 / Cursor 的 "Output → MCP"。',-1))]),l(o,{type:"primary",onClick:d},{default:m(()=>[...e[18]||(e[18]=[n("→ 上游数据PROVIDERs",-1)])]),_:1})]),e[22]||(e[22]=t("section",{class:"ai-section"},[t("h3",null,"自定义端口"),t("p",null,[n(" 如果你修改了启动端口 (例如改成 "),t("code",null,"19080"),n("), 上面 JSON 示例中的 URL 也要相应替换。 推荐保持默认 "),t("code",null,"18080"),n(" 以便与控制台共用。 ")])],-1))])}}}),wt=k(It,[["__scopeId","data-v-fd4f19f0"]]),kt={class:"cfg-subtabs",role:"tablist","aria-label":"指南子标签"},At=["aria-selected","onClick"],Mt={class:"guides-panel-stack"},Ot={class:"cfg-panel","aria-label":"人工接入指南"},Dt={class:"cfg-panel","aria-label":"AI 接入指南"},Ht=I({__name:"GuidesView",setup(T){const a=F(),f=A(),s=h("human");G(()=>a.query.tab,i=>{s.value=W(i,$,"human")},{immediate:!0});function p(i){s.value!==i&&(s.value=i,f.replace({query:{...a.query,tab:i}}))}const g=P(()=>$.map(i=>({key:i,title:X[i]})));return(i,d)=>{const y=w("el-card");return v(),K(y,{shadow:"never",class:"panel-card"},{header:m(()=>[...d[0]||(d[0]=[t("div",{class:"card-toolbar"},[t("h2",{class:"card-title"},"接入指南")],-1)])]),default:m(()=>[t("nav",kt,[(v(!0),C(x,null,R(g.value,e=>(v(),C("button",{key:e.key,type:"button",class:B(["cfg-subtab",{active:s.value===e.key}]),role:"tab","aria-selected":s.value===e.key,onClick:o=>p(e.key)},u(e.title),11,At))),128))]),t("div",Mt,[D(t("section",Ot,[l(_t)],512),[[H,s.value==="human"]]),D(t("section",Dt,[l(wt)],512),[[H,s.value==="ai"]])])]),_:1})}}}),Bt=k(Ht,[["__scopeId","data-v-d1486e0f"]]);export{Bt as default};
