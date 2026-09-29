import{G as w,L as g,M as _,P as t,a0 as a,u as N,W as T,_ as n,$ as f,c as b,aj as I,d as U,A as V,F as q,ae as x,aG as O,aD as k,ac as Y,S as R,k as y,w as j,X as D,a1 as H,aC as G}from"./vue-hmD7xhy6.js";import{p as K}from"./hashTab-C4jxjRmY.js";import{p as F,g as S}from"./element-plus-D8yFet9M.js";import{c as B}from"./clipboard-DPGtmGRI.js";import{_ as A}from"./index-9UBOdT3W.js";const W={class:"code-block"},J={class:"code-block-bar"},X=w({__name:"CodeBlock",props:{code:{}},setup(P){const l=P,m=b(!1),i=b(!1);async function v(){m.value=!0;try{await B(l.code,""),i.value=!0,S.success("已复制"),setTimeout(()=>{i.value=!1},1500)}catch{S.error("复制失败, 请手动复制")}finally{m.value=!1}}return(d,C)=>{const r=I("el-button");return g(),_("div",W,[t("div",J,[a(r,{size:"small",icon:N(F),class:"copy-btn",loading:m.value,onClick:v},{default:T(()=>[n(f(i.value?"已复制":"复制"),1)]),_:1},8,["icon","loading"])]),t("pre",null,[t("code",null,f(P.code),1)])])}}}),s=A(X,[["__scopeId","data-v-6f4d9e48"]]),z={class:"onboarding"},Z={class:"onboarding-toolbar"},Q={class:"onboarding-nav","aria-label":"章节导航"},tt=["href","onClick"],ot=["id"],et={class:"onboarding-toolbox"},nt=["id"],at=["id"],lt=["id"],st=["id"],$=".\\scripts\\start_gateway.ps1",E="python -m src.unihive.gateway_server --transport stdio",it=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,dt=`import asyncio
from fastmcp.client import Client

async def main():
    async with Client("http://127.0.0.1:18080/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_realtime_quote", {"symbol": "600000"})
        print(result.data)

asyncio.run(main())`,rt=`{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "src.unihive.gateway_server", "--transport", "stdio"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}`,ut=`import asyncio
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

asyncio.run(main())`,ct=`bar = await client.call_tool(
    "a_share_prices_historical",
    {"thscode": "600519.SH", "start_date": "2026-01-01", "end_date": "2026-03-01"}
)
print(bar.data)`,pt=`match = await client.call_tool(
    "search_stock_local",
    {"keyword": "茅台"}
)
print(match.data)`,ft=w({__name:"OnboardingPanel",setup(P){const l=[{id:"sec-quickstart",title:"快速开始"},{id:"sec-integration",title:"接入方式"},{id:"sec-interfaces",title:"MCP APIs"},{id:"sec-examples",title:"常用示例"},{id:"sec-troubleshoot",title:"故障排查"}],m=k(),i=b(l[0].id);async function v(e,o){try{await B(e,""),S.success(o)}catch{S.error("复制失败, 请手动复制")}}function d(){m.push({name:"interfaces"})}function C(e){const o=document.getElementById(e);o&&(o.scrollIntoView({behavior:"smooth",block:"start"}),i.value=e)}let r=null;function u(){const e=l.map(o=>document.getElementById(o.id)).filter(o=>!!o);e.length!==0&&(r=new IntersectionObserver(o=>{const c=o.filter(p=>p.isIntersecting).sort((p,M)=>p.target.getBoundingClientRect().top-M.target.getBoundingClientRect().top);c.length>0&&(i.value=c[0].target.id)},{rootMargin:"-20% 0px -70% 0px",threshold:0}),e.forEach(o=>r.observe(o)))}return U(()=>{u()}),V(()=>{r==null||r.disconnect(),r=null}),(e,o)=>{const c=I("el-button");return g(),_("article",z,[t("div",Z,[a(c,{type:"primary",onClick:d},{default:T(()=>[...o[2]||(o[2]=[n("→ MCP APIs",-1)])]),_:1})]),t("nav",Q,[(g(),_(q,null,x(l,p=>t("a",{key:p.id,href:`#${p.id}`,class:R({active:i.value===p.id}),onClick:Y(M=>C(p.id),["prevent"])},f(p.title),11,tt)),64))]),t("section",{id:l[0].id,class:"onboarding-section"},[t("h3",null,f(l[0].title),1),o[5]||(o[5]=t("p",null,"UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。",-1)),o[6]||(o[6]=t("h4",null,"1.1 启动网关",-1)),o[7]||(o[7]=t("p",null,[n("网关默认以 "),t("strong",null,"HTTP 模式"),n("启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:")],-1)),t("div",et,[a(c,{onClick:o[0]||(o[0]=p=>v($,"已复制 HTTP 启动命令"))},{default:T(()=>[...o[3]||(o[3]=[n("复制 HTTP 启动命令",-1)])]),_:1}),a(c,{onClick:o[1]||(o[1]=p=>v(E,"已复制 STDIO 启动命令"))},{default:T(()=>[...o[4]||(o[4]=[n("复制 STDIO 启动命令",-1)])]),_:1})]),a(s,{code:$}),o[8]||(o[8]=O("<p data-v-ffaa0e59>启动后, 控制台和 HTTP 网关共用端口 <code data-v-ffaa0e59>http://127.0.0.1:18080</code> (MCP 接口在 <code data-v-ffaa0e59>/mcp</code> 子路径下)。</p><h4 data-v-ffaa0e59>1.2 配置管理</h4><p data-v-ffaa0e59>日常配置在控制台<strong data-v-ffaa0e59>「配置信息」</strong>页维护, 事实源是配置库 <code data-v-ffaa0e59>data/config.db</code> — 上游启停、工具契约、FUYAO TTL 等改动<strong data-v-ffaa0e59>热生效, 无需重启</strong>。空库首次启动时自动从出厂 YAML 模板 (<code data-v-ffaa0e59>config/upstreams.yaml</code> + <code data-v-ffaa0e59>config/tools_*.yaml</code>) seed; 监听端口、控制台账号等 bootstrap 配置仍在 <code data-v-ffaa0e59>config/config.yaml</code>。</p>",3))],8,ot),t("section",{id:l[1].id,class:"onboarding-section"},[t("h3",null,f(l[1].title),1),o[9]||(o[9]=t("h4",null,"2.1 HTTP 模式 (推荐)",-1)),o[10]||(o[10]=t("p",null,[n("网关以 "),t("strong",null,"Streamable HTTP"),n(" 模式对外提供 MCP 接口, 监听 "),t("code",null,"http://127.0.0.1:18080/mcp"),n("。运行 "),t("code",null,".\\scripts\\start_gateway.ps1"),n(" 会同时启动控制台和 HTTP 网关。")],-1)),o[11]||(o[11]=t("p",null,[t("strong",null,"Claude Desktop / Cursor 配置:")],-1)),a(s,{code:it}),o[12]||(o[12]=t("p",null,[t("strong",null,"Python SDK 示例:")],-1)),a(s,{code:dt}),o[13]||(o[13]=t("h4",null,"2.2 STDIO 模式 (备选)",-1)),o[14]||(o[14]=t("p",null,"用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:",-1)),a(s,{code:rt}),o[15]||(o[15]=t("p",null,"或命令行单独启动 STDIO 模式:",-1)),a(s,{code:E})],8,nt),t("section",{id:l[2].id,class:"onboarding-section"},[t("h3",null,f(l[2].title),1),o[16]||(o[16]=t("p",null,[n("当前网关对外暴露约 "),t("strong",null,"290"),n(" 个 MCP 接口 (5 套上游, 精确数量以 "),t("strong",null,'"MCP APIs"'),n(" 标签页运行时列表为准), 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在该标签页。")],-1))],8,at),t("section",{id:l[3].id,class:"onboarding-section"},[t("h3",null,f(l[3].title),1),o[17]||(o[17]=t("h4",null,"4.1 Python SDK (推荐)",-1)),o[18]||(o[18]=t("p",null,[n("通过 "),t("a",{href:"https://github.com/jlowin/fastmcp",target:"_blank",rel:"noopener"},"FastMCP"),n(" 客户端连接 HTTP 网关 (默认 "),t("code",null,"http://127.0.0.1:18080/mcp"),n(")。所有 tool 名、参数和返回结构以 "),t("strong",null,'"MCP APIs"'),n(" 标签页为准。")],-1)),a(s,{code:ut}),o[19]||(o[19]=t("h4",null,"4.2 日 K 线 (含日期范围)",-1)),a(s,{code:ct}),o[20]||(o[20]=t("h4",null,"4.3 搜索股票",-1)),a(s,{code:pt}),o[21]||(o[21]=t("div",{class:"onboarding-section-meta"},[t("strong",null,"提示:"),n(" 如果你的客户端只支持 stdio, 启动 STDIO 模式: "),t("code",null,"python -m src.unihive.gateway_server --transport stdio"),n("。 调试可用 MCP Inspector ("),t("code",null,"npx -y @modelcontextprotocol/inspector"),n("), Python 可用官方 SDK。 Windows 下 "),t("code",null,"cwd"),n(" 用项目绝对路径。 ")],-1))],8,lt),t("section",{id:l[4].id,class:"onboarding-section"},[t("h3",null,f(l[4].title),1),o[22]||(o[22]=O('<h4 data-v-ffaa0e59>5.1 连接失败</h4><ul data-v-ffaa0e59><li data-v-ffaa0e59>检查网关是否已启动: 访问 <code data-v-ffaa0e59>http://127.0.0.1:18080</code></li><li data-v-ffaa0e59>检查客户端配置中的 <code data-v-ffaa0e59>cwd</code> 路径是否正确</li><li data-v-ffaa0e59>Windows 下确认 Python 已加入 PATH</li></ul><h4 data-v-ffaa0e59>5.2 接口返回错误</h4><ul data-v-ffaa0e59><li data-v-ffaa0e59>查看控制台&quot;上游数据PROVIDERs&quot;标签页, 确认对应上游是否在线</li><li data-v-ffaa0e59>查看返回的 <code data-v-ffaa0e59>last_error</code> 字段</li><li data-v-ffaa0e59>对应上游若被禁用, 可在控制台&quot;配置信息 → 上游&quot;标签页直接热切换启用 (实时生效并持久化到配置库 <code data-v-ffaa0e59>data/config.db</code>), 无需重启</li></ul><h4 data-v-ffaa0e59>5.3 参数错误</h4><ul data-v-ffaa0e59><li data-v-ffaa0e59>股票代码由网关自动标准化 (<code data-v-ffaa0e59>000001</code>、<code data-v-ffaa0e59>sh600519</code>、<code data-v-ffaa0e59>600519.SH</code>、<code data-v-ffaa0e59>sz000001</code> 都能识别)</li><li data-v-ffaa0e59>日期统一使用 <code data-v-ffaa0e59>YYYYMMDD</code> (如 <code data-v-ffaa0e59>20260301</code>)</li><li data-v-ffaa0e59>具体参数以 <strong data-v-ffaa0e59>&quot;MCP APIs&quot;</strong> 标签页每个 tool 行的&quot;详情&quot;展开为准</li></ul><h4 data-v-ffaa0e59>5.4 反馈渠道</h4><p data-v-ffaa0e59>遇到问题请提交 Issue 到 <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener" data-v-ffaa0e59>github.com/unihive/unihive</a>, 并附上控制台截图与接口返回内容。点击 <strong data-v-ffaa0e59>&quot;MCP APIs&quot;</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>',8))],8,st)])}}}),vt=A(ft,[["__scopeId","data-v-ffaa0e59"]]);function L(){return typeof location>"u"?"http://127.0.0.1:18080":location.origin}function h(){return`${L()}/mcp`}const mt={class:"ai-onboarding"},gt={class:"ai-section"},_t={class:"muted"},Ct={class:"ai-section"},Tt={class:"ai-section"},Pt={class:"ai-section"},yt="~290",ht=w({__name:"AiOnboardingPanel",setup(P){const l=k(),m=y(()=>L()),i=y(()=>h()),v=y(()=>JSON.stringify({mcpServers:{unihive:{url:h()}}},null,2)),d=y(()=>JSON.stringify({mcpServers:{unihive:{command:"python",args:["-m","src.unihive.gateway_server","--transport","stdio"],cwd:"D:/fintech_workspace/unihive"}}},null,2)),C=`## 金融数据工具

你可以通过 UniHive MCP 获取中国 A 股、基金、指数的金融数据。

### MCP 配置
MCP Server 地址: ${h()}

### 调用方式
使用 fastmcp 客户端:
\`\`\`python
from fastmcp import Client

async with Client("${h()}") as client:
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
概念板块: 886042.TI`;function r(){l.push({name:"status"})}return(u,e)=>{const o=I("el-button");return g(),_("article",mt,[e[21]||(e[21]=t("section",{class:"ai-section"},[t("h3",null,"选择你的 AI 客户端"),t("p",null," UniHive 通过标准 MCP 协议暴露, 兼容 Claude Desktop、Cursor、Cline 等主流 AI 客户端。 以下是 3 个最常用客户端的配置示例, 复制后粘贴到对应配置文件即可接入。 "),t("p",null,[n(" 推荐使用 "),t("strong",null,"HTTP 模式"),n("(无需启动额外进程);如果客户端不支持 HTTP MCP, 可回退到 "),t("strong",null,"STDIO 模式"),n("。 ")])],-1)),t("section",gt,[e[1]||(e[1]=t("h3",null,"HTTP 模式 (推荐)",-1)),t("p",_t,[e[0]||(e[0]=n(" 当前网关地址: ",-1)),t("code",null,f(i.value),1)]),e[2]||(e[2]=t("h4",null,"Claude Desktop",-1)),e[3]||(e[3]=t("p",null,[n(" 配置文件路径: "),t("code",null,"%APPDATA%\\Claude\\claude_desktop_config.json"),n(" (Windows) / "),t("code",null,"~/Library/Application Support/Claude/claude_desktop_config.json"),n(" (macOS) / "),t("code",null,"~/.config/Claude/claude_desktop_config.json"),n(" (Linux) ")],-1)),a(s,{code:v.value},null,8,["code"]),e[4]||(e[4]=t("h4",null,"Cursor",-1)),e[5]||(e[5]=t("p",null,[n(" 配置文件路径: "),t("code",null,"~/.cursor/mcp.json"),n("(全局) 或 项目根目录 "),t("code",null,".cursor/mcp.json"),n("(项目级优先) ")],-1)),a(s,{code:v.value},null,8,["code"]),e[6]||(e[6]=t("h4",null,"Cline (VS Code 扩展)",-1)),e[7]||(e[7]=t("p",null,[n(' 在 VS Code 左侧活动栏点击 Cline 图标 → 顶栏 "MCP Servers" → "Configure" 按钮, 会自动打开 '),t("code",null,"cline_mcp_settings.json"),n("。配置 schema 与 Cursor 相同。 ")],-1)),a(s,{code:v.value},null,8,["code"])]),t("section",Ct,[e[8]||(e[8]=t("h3",null,"STDIO 模式 (备选)",-1)),e[9]||(e[9]=t("p",null,[n(" 适用于不支持 HTTP MCP 的旧客户端。需要在配置文件路径下用 "),t("code",null,"command"),n(" + "),t("code",null,"args"),n(" + "),t("code",null,"cwd"),n(" 三段指定启动方式。"),t("code",null,"cwd"),n(" 必须指向 UniHive 项目根目录。 ")],-1)),a(s,{code:d.value},null,8,["code"])]),t("section",Tt,[e[10]||(e[10]=t("h3",null,"AI Agent 系统提示词 (可选)",-1)),e[11]||(e[11]=t("p",null," 把以下提示词添加到 AI 客户端的 system prompt / custom instructions 字段, 让 Agent 自动知道如何调用 UniHive 工具。 ",-1)),a(s,{code:C})]),t("section",Pt,[e[19]||(e[19]=t("h3",null,"验证是否接入成功",-1)),t("ol",null,[e[12]||(e[12]=t("li",null,"重启 AI 客户端 (Claude Desktop 完全退出后再打开)。",-1)),t("li",null," 在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的 "+f(yt)+" 个工具 (按上游分组)。 "),e[13]||(e[13]=t("li",null,' 向 AI 提一个简单请求: "搜索股票 茅台"。若返回结果则接入成功。 ',-1))]),e[20]||(e[20]=t("p",null,"若失败, 按以下顺序排查:",-1)),t("ul",null,[t("li",null,[e[14]||(e[14]=n("确认网关已启动: 浏览器访问 ",-1)),t("code",null,f(m.value),1),e[15]||(e[15]=n(" 能看到控制台首页。",-1))]),e[16]||(e[16]=t("li",null,[n("查看 "),t("strong",null,'"上游数据PROVIDERs"'),n(" 标签页, 确认上游是否全绿。")],-1)),e[17]||(e[17]=t("li",null,'查看客户端日志: Claude Desktop 的 "Developer" 菜单 / Cursor 的 "Output → MCP"。',-1))]),a(o,{type:"primary",onClick:r},{default:T(()=>[...e[18]||(e[18]=[n("→ 上游数据PROVIDERs",-1)])]),_:1})]),e[22]||(e[22]=t("section",{class:"ai-section"},[t("h3",null,"自定义端口"),t("p",null,[n(" 如果你修改了启动端口 (例如改成 "),t("code",null,"19080"),n("), 上面 JSON 示例中的 URL 也要相应替换。 推荐保持默认 "),t("code",null,"18080"),n(" 以便与控制台共用。 ")])],-1))])}}}),bt=A(ht,[["__scopeId","data-v-fd4f19f0"]]),St={class:"view-page"},wt={class:"cfg-subtabs",role:"tablist","aria-label":"指南子标签"},It=["aria-selected","onClick"],At={class:"guides-panel-stack"},kt={class:"cfg-panel","aria-label":"人工接入指南"},Mt={class:"cfg-panel","aria-label":"AI 接入指南"},Ot=w({__name:"GuidesView",setup(P){const l=G(),m=k(),i=["human","ai"],v={human:"人工接入",ai:"AI 接入"},d=b("human");j(()=>l.query.tab,u=>{d.value=K(u,i,"human")},{immediate:!0});function C(u){d.value!==u&&(d.value=u,m.replace({query:{...l.query,tab:u}}))}const r=y(()=>i.map(u=>({key:u,title:v[u]})));return(u,e)=>{const o=I("el-card");return g(),_("div",St,[e[0]||(e[0]=t("header",{class:"view-header"},[t("h1",{class:"view-h1"},"接入指南")],-1)),a(o,{shadow:"never",class:"panel-card"},{default:T(()=>[t("nav",wt,[(g(!0),_(q,null,x(r.value,c=>(g(),_("button",{key:c.key,type:"button",class:R(["cfg-subtab",{active:d.value===c.key}]),role:"tab","aria-selected":d.value===c.key,onClick:p=>C(c.key)},f(c.title),11,It))),128))]),t("div",At,[D(t("section",kt,[a(vt)],512),[[H,d.value==="human"]]),D(t("section",Mt,[a(bt)],512),[[H,d.value==="ai"]])])]),_:1})])}}}),xt=A(Ot,[["__scopeId","data-v-d68b559c"]]);export{xt as default};
