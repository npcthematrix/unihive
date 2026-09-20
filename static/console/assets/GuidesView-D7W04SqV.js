import{G as I,aj as k,L as g,M as T,P as t,a0 as s,u as R,W as _,_ as e,$ as u,c as S,aD as H,d as Y,A as j,F as x,ae as B,ac as G,S as L,aG as M,k as m,w as K,O as V,X as O,a1 as D,aC as F}from"./vue-hmD7xhy6.js";import{p as W}from"./hashTab-C4jxjRmY.js";import{o as J,c as w}from"./element-plus-CGe9KhpW.js";import{c as N}from"./clipboard-R61eHf4S.js";import{_ as A}from"./index-CRTk5Kjp.js";const $=["human","ai"],X={human:"人工接入",ai:"AI 接入"},z={class:"code-block"},Z={class:"code-block-bar"},Q=I({__name:"CodeBlock",props:{code:{}},setup(y){const l=y,f=S(!1),i=S(!1);async function p(){f.value=!0;try{await N(l.code,""),i.value=!0,w.success("已复制"),setTimeout(()=>{i.value=!1},1500)}catch{w.error("复制失败, 请手动复制")}finally{f.value=!1}}return(C,c)=>{const d=k("el-button");return g(),T("div",z,[t("div",Z,[s(d,{size:"small",icon:R(J),class:"copy-btn",loading:f.value,onClick:p},{default:_(()=>[e(u(i.value?"已复制":"复制"),1)]),_:1},8,["icon","loading"])]),t("pre",null,[t("code",null,u(y.code),1)])])}}}),r=A(Q,[["__scopeId","data-v-6f4d9e48"]]),tt={class:"onboarding"},ot={class:"onboarding-toolbar"},nt={class:"onboarding-nav","aria-label":"章节导航"},et=["href","onClick"],lt=["id"],st={class:"onboarding-toolbox"},it=["id"],at=["id"],ct=["id"],dt=["id"],E=".\\scripts\\start_gateway.ps1",q="python -m src.unihive.gateway_server --transport stdio",rt=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,ut=`import asyncio
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
print(match.data)`,gt=I({__name:"OnboardingPanel",setup(y){const l=[{id:"sec-quickstart",title:"快速开始"},{id:"sec-integration",title:"接入方式"},{id:"sec-interfaces",title:"MCP APIs"},{id:"sec-examples",title:"常用示例"},{id:"sec-troubleshoot",title:"故障排查"}],f=H(),i=S(l[0].id);async function p(a,o){try{await N(a,""),w.success(o)}catch{w.error("复制失败, 请手动复制")}}function C(){f.push({name:"interfaces"})}function c(a){const o=document.getElementById(a);o&&(o.scrollIntoView({behavior:"smooth",block:"start"}),i.value=a)}let d=null;function P(){const a=l.map(o=>document.getElementById(o.id)).filter(o=>!!o);a.length!==0&&(d=new IntersectionObserver(o=>{const v=o.filter(n=>n.isIntersecting).sort((n,b)=>n.target.getBoundingClientRect().top-b.target.getBoundingClientRect().top);v.length>0&&(i.value=v[0].target.id)},{rootMargin:"-20% 0px -70% 0px",threshold:0}),a.forEach(o=>d.observe(o)))}return Y(()=>{P()}),j(()=>{d==null||d.disconnect(),d=null}),(a,o)=>{const v=k("el-button");return g(),T("article",tt,[t("div",ot,[s(v,{type:"primary",onClick:C},{default:_(()=>[...o[2]||(o[2]=[e("→ MCP APIs",-1)])]),_:1})]),t("nav",nt,[(g(),T(x,null,B(l,n=>t("a",{key:n.id,href:`#${n.id}`,class:L({active:i.value===n.id}),onClick:G(b=>c(n.id),["prevent"])},u(n.title),11,et)),64))]),t("section",{id:l[0].id,class:"onboarding-section"},[t("h3",null,u(l[0].title),1),o[5]||(o[5]=t("p",null,"UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。",-1)),o[6]||(o[6]=t("h4",null,"1.1 启动网关",-1)),o[7]||(o[7]=t("p",null,[e("网关默认以 "),t("strong",null,"HTTP 模式"),e("启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:")],-1)),t("div",st,[s(v,{onClick:o[0]||(o[0]=n=>p(E,"已复制 HTTP 启动命令"))},{default:_(()=>[...o[3]||(o[3]=[e("复制 HTTP 启动命令",-1)])]),_:1}),s(v,{onClick:o[1]||(o[1]=n=>p(q,"已复制 STDIO 启动命令"))},{default:_(()=>[...o[4]||(o[4]=[e("复制 STDIO 启动命令",-1)])]),_:1})]),s(r,{code:E}),o[8]||(o[8]=M("<p data-v-fc53c0c3>启动后, 控制台和 HTTP 网关共用端口 <code data-v-fc53c0c3>http://127.0.0.1:18080</code> (MCP 接口在 <code data-v-fc53c0c3>/mcp</code> 子路径下)。</p><h4 data-v-fc53c0c3>1.2 配置管理</h4><p data-v-fc53c0c3>日常配置在控制台<strong data-v-fc53c0c3>「配置信息」</strong>页维护, 事实源是配置库 <code data-v-fc53c0c3>data/config.db</code> — 上游启停、工具契约、FUYAO TTL 等改动<strong data-v-fc53c0c3>热生效, 无需重启</strong>。空库首次启动时自动从出厂 YAML 模板 (<code data-v-fc53c0c3>config/upstreams.yaml</code> + <code data-v-fc53c0c3>config/tools_*.yaml</code>) seed; 监听端口、控制台账号等 bootstrap 配置仍在 <code data-v-fc53c0c3>config/config.yaml</code>。</p>",3))],8,lt),t("section",{id:l[1].id,class:"onboarding-section"},[t("h3",null,u(l[1].title),1),o[9]||(o[9]=t("h4",null,"2.1 HTTP 模式 (推荐)",-1)),o[10]||(o[10]=t("p",null,[e("网关以 "),t("strong",null,"Streamable HTTP"),e(" 模式对外提供 MCP 接口, 监听 "),t("code",null,"http://127.0.0.1:18080/mcp"),e("。运行 "),t("code",null,".\\scripts\\start_gateway.ps1"),e(" 会同时启动控制台和 HTTP 网关。")],-1)),o[11]||(o[11]=t("p",null,[t("strong",null,"Claude Desktop / Cursor 配置:")],-1)),s(r,{code:rt}),o[12]||(o[12]=t("p",null,[t("strong",null,"Python SDK 示例:")],-1)),s(r,{code:ut}),o[13]||(o[13]=t("h4",null,"2.2 STDIO 模式 (备选)",-1)),o[14]||(o[14]=t("p",null,"用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:",-1)),s(r,{code:pt}),o[15]||(o[15]=t("p",null,"或命令行单独启动 STDIO 模式:",-1)),s(r,{code:q})],8,it),t("section",{id:l[2].id,class:"onboarding-section"},[t("h3",null,u(l[2].title),1),o[16]||(o[16]=t("p",null,[e("当前网关对外暴露约 "),t("strong",null,"290"),e(" 个 MCP 接口 (5 套上游, 精确数量以 "),t("strong",null,'"MCP APIs"'),e(" 标签页运行时列表为准), 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在该标签页。")],-1))],8,at),t("section",{id:l[3].id,class:"onboarding-section"},[t("h3",null,u(l[3].title),1),o[17]||(o[17]=t("h4",null,"4.1 Python SDK (推荐)",-1)),o[18]||(o[18]=t("p",null,[e("通过 "),t("a",{href:"https://github.com/jlowin/fastmcp",target:"_blank",rel:"noopener"},"FastMCP"),e(" 客户端连接 HTTP 网关 (默认 "),t("code",null,"http://127.0.0.1:18080/mcp"),e(")。所有 tool 名、参数和返回结构以 "),t("strong",null,'"MCP APIs"'),e(" 标签页为准。")],-1)),s(r,{code:ft}),o[19]||(o[19]=t("h4",null,"4.2 日 K 线 (含日期范围)",-1)),s(r,{code:vt}),o[20]||(o[20]=t("h4",null,"4.3 搜索股票",-1)),s(r,{code:mt}),o[21]||(o[21]=t("div",{class:"onboarding-section-meta"},[t("strong",null,"提示:"),e(" 如果你的客户端只支持 stdio, 启动 STDIO 模式: "),t("code",null,"python -m src.unihive.gateway_server --transport stdio"),e("。 调试可用 MCP Inspector ("),t("code",null,"npx -y @modelcontextprotocol/inspector"),e("), Python 可用官方 SDK。 Windows 下 "),t("code",null,"cwd"),e(" 用项目绝对路径。 ")],-1))],8,ct),t("section",{id:l[4].id,class:"onboarding-section"},[t("h3",null,u(l[4].title),1),o[22]||(o[22]=M('<h4 data-v-fc53c0c3>5.1 连接失败</h4><ul data-v-fc53c0c3><li data-v-fc53c0c3>检查网关是否已启动: 访问 <code data-v-fc53c0c3>http://127.0.0.1:18080</code></li><li data-v-fc53c0c3>检查客户端配置中的 <code data-v-fc53c0c3>cwd</code> 路径是否正确</li><li data-v-fc53c0c3>Windows 下确认 Python 已加入 PATH</li></ul><h4 data-v-fc53c0c3>5.2 接口返回错误</h4><ul data-v-fc53c0c3><li data-v-fc53c0c3>查看控制台&quot;上游状态&quot;标签页, 确认对应上游是否在线</li><li data-v-fc53c0c3>查看返回的 <code data-v-fc53c0c3>last_error</code> 字段</li><li data-v-fc53c0c3>对应上游若被禁用, 可在控制台&quot;配置信息 → 上游&quot;标签页直接热切换启用 (实时生效并持久化到配置库 <code data-v-fc53c0c3>data/config.db</code>), 无需重启</li></ul><h4 data-v-fc53c0c3>5.3 参数错误</h4><ul data-v-fc53c0c3><li data-v-fc53c0c3>股票代码由网关自动标准化 (<code data-v-fc53c0c3>000001</code>、<code data-v-fc53c0c3>sh600519</code>、<code data-v-fc53c0c3>600519.SH</code>、<code data-v-fc53c0c3>sz000001</code> 都能识别)</li><li data-v-fc53c0c3>日期统一使用 <code data-v-fc53c0c3>YYYYMMDD</code> (如 <code data-v-fc53c0c3>20260301</code>)</li><li data-v-fc53c0c3>具体参数以 <strong data-v-fc53c0c3>&quot;MCP APIs&quot;</strong> 标签页每个 tool 行的&quot;详情&quot;展开为准</li></ul><h4 data-v-fc53c0c3>5.4 反馈渠道</h4><p data-v-fc53c0c3>遇到问题请提交 Issue 到 <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener" data-v-fc53c0c3>github.com/unihive/unihive</a>, 并附上控制台截图与接口返回内容。点击 <strong data-v-fc53c0c3>&quot;MCP APIs&quot;</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>',8))],8,dt)])}}}),_t=A(gt,[["__scopeId","data-v-fc53c0c3"]]);function U(){return typeof location>"u"?"http://127.0.0.1:18080":location.origin}function h(){return`${U()}/mcp`}const Ct={class:"ai-onboarding"},Tt={class:"ai-section"},yt={class:"muted"},Pt={class:"ai-section"},bt={class:"ai-section"},ht={class:"ai-section"},St="~290",wt=I({__name:"AiOnboardingPanel",setup(y){const l=H(),f=m(()=>U()),i=m(()=>h());function p(){return JSON.stringify({mcpServers:{unihive:{url:h()}}},null,2)}const C=m(p),c=m(p),d=m(p),P=m(()=>JSON.stringify({mcpServers:{unihive:{command:"python",args:["-m","src.unihive.gateway_server","--transport","stdio"],cwd:"D:/fintech_workspace/unihive"}}},null,2)),a=`## 金融数据工具

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
概念板块: 886042.TI`;function o(){l.push({name:"status"})}return(v,n)=>{const b=k("el-button");return g(),T("article",Ct,[n[21]||(n[21]=t("section",{class:"ai-section"},[t("h3",null,"选择你的 AI 客户端"),t("p",null," UniHive 通过标准 MCP 协议暴露, 兼容 Claude Desktop、Cursor、Cline 等主流 AI 客户端。 以下是 3 个最常用客户端的配置示例, 复制后粘贴到对应配置文件即可接入。 "),t("p",null,[e(" 推荐使用 "),t("strong",null,"HTTP 模式"),e("(无需启动额外进程);如果客户端不支持 HTTP MCP, 可回退到 "),t("strong",null,"STDIO 模式"),e("。 ")])],-1)),t("section",Tt,[n[1]||(n[1]=t("h3",null,"HTTP 模式 (推荐)",-1)),t("p",yt,[n[0]||(n[0]=e(" 当前网关地址: ",-1)),t("code",null,u(i.value),1)]),n[2]||(n[2]=t("h4",null,"Claude Desktop",-1)),n[3]||(n[3]=t("p",null,[e(" 配置文件路径: "),t("code",null,"%APPDATA%\\Claude\\claude_desktop_config.json"),e(" (Windows) / "),t("code",null,"~/Library/Application Support/Claude/claude_desktop_config.json"),e(" (macOS) / "),t("code",null,"~/.config/Claude/claude_desktop_config.json"),e(" (Linux) ")],-1)),s(r,{code:C.value},null,8,["code"]),n[4]||(n[4]=t("h4",null,"Cursor",-1)),n[5]||(n[5]=t("p",null,[e(" 配置文件路径: "),t("code",null,"~/.cursor/mcp.json"),e("(全局) 或 项目根目录 "),t("code",null,".cursor/mcp.json"),e("(项目级优先) ")],-1)),s(r,{code:c.value},null,8,["code"]),n[6]||(n[6]=t("h4",null,"Cline (VS Code 扩展)",-1)),n[7]||(n[7]=t("p",null,[e(' 在 VS Code 左侧活动栏点击 Cline 图标 → 顶栏 "MCP Servers" → "Configure" 按钮, 会自动打开 '),t("code",null,"cline_mcp_settings.json"),e("。配置 schema 与 Cursor 相同。 ")],-1)),s(r,{code:d.value},null,8,["code"])]),t("section",Pt,[n[8]||(n[8]=t("h3",null,"STDIO 模式 (备选)",-1)),n[9]||(n[9]=t("p",null,[e(" 适用于不支持 HTTP MCP 的旧客户端。需要在配置文件路径下用 "),t("code",null,"command"),e(" + "),t("code",null,"args"),e(" + "),t("code",null,"cwd"),e(" 三段指定启动方式。"),t("code",null,"cwd"),e(" 必须指向 UniHive 项目根目录。 ")],-1)),s(r,{code:P.value},null,8,["code"])]),t("section",bt,[n[10]||(n[10]=t("h3",null,"AI Agent 系统提示词 (可选)",-1)),n[11]||(n[11]=t("p",null," 把以下提示词添加到 AI 客户端的 system prompt / custom instructions 字段, 让 Agent 自动知道如何调用 UniHive 工具。 ",-1)),s(r,{code:a})]),t("section",ht,[n[19]||(n[19]=t("h3",null,"验证是否接入成功",-1)),t("ol",null,[n[12]||(n[12]=t("li",null,"重启 AI 客户端 (Claude Desktop 完全退出后再打开)。",-1)),t("li",null," 在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的 "+u(St)+" 个工具 (按上游分组)。 "),n[13]||(n[13]=t("li",null,' 向 AI 提一个简单请求: "搜索股票 茅台"。若返回结果则接入成功。 ',-1))]),n[20]||(n[20]=t("p",null,"若失败, 按以下顺序排查:",-1)),t("ul",null,[t("li",null,[n[14]||(n[14]=e("确认网关已启动: 浏览器访问 ",-1)),t("code",null,u(f.value),1),n[15]||(n[15]=e(" 能看到控制台首页。",-1))]),n[16]||(n[16]=t("li",null,[e("查看 "),t("strong",null,'"上游状态"'),e(" 标签页, 确认上游是否全绿。")],-1)),n[17]||(n[17]=t("li",null,'查看客户端日志: Claude Desktop 的 "Developer" 菜单 / Cursor 的 "Output → MCP"。',-1))]),s(b,{type:"primary",onClick:o},{default:_(()=>[...n[18]||(n[18]=[e("→ 上游状态",-1)])]),_:1})]),n[22]||(n[22]=t("section",{class:"ai-section"},[t("h3",null,"自定义端口"),t("p",null,[e(" 如果你修改了启动端口 (例如改成 "),t("code",null,"19080"),e("), 上面 JSON 示例中的 URL 也要相应替换。 推荐保持默认 "),t("code",null,"18080"),e(" 以便与控制台共用。 ")])],-1))])}}}),It=A(wt,[["__scopeId","data-v-de259a00"]]),kt={class:"cfg-subtabs",role:"tablist","aria-label":"指南子标签"},At=["aria-selected","onClick"],Ht={class:"guides-panel-stack"},Mt={class:"cfg-panel","aria-label":"人工接入指南"},Ot={class:"cfg-panel","aria-label":"AI 接入指南"},Dt=I({__name:"GuidesView",setup(y){const l=F(),f=H(),i=S("human");K(()=>l.query.tab,c=>{i.value=W(c,$,"human")},{immediate:!0});function p(c){i.value!==c&&(i.value=c,f.replace({query:{...l.query,tab:c}}))}const C=m(()=>$.map(c=>({key:c,title:X[c]})));return(c,d)=>{const P=k("el-card");return g(),V(P,{shadow:"never",class:"panel-card"},{header:_(()=>[...d[0]||(d[0]=[t("div",{class:"card-toolbar"},[t("h2",{class:"card-title"},"接入指南")],-1)])]),default:_(()=>[t("nav",kt,[(g(!0),T(x,null,B(C.value,a=>(g(),T("button",{key:a.key,type:"button",class:L(["cfg-subtab",{active:i.value===a.key}]),role:"tab","aria-selected":i.value===a.key,onClick:o=>p(a.key)},u(a.title),11,At))),128))]),t("div",Ht,[O(t("section",Mt,[s(_t)],512),[[D,i.value==="human"]]),O(t("section",Ot,[s(It)],512),[[D,i.value==="ai"]])])]),_:1})}}}),Lt=A(Dt,[["__scopeId","data-v-d1486e0f"]]);export{Lt as default};
