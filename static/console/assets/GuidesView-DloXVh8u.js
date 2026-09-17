import{G as I,aj as k,L as f,M as T,a0 as s,u as R,W as _,_ as l,$ as c,P as t,c as S,az as M,d as j,A as G,F as q,ae as B,ac as K,S as N,aG as V,k as g,w as Y,O as F,aF as W,X as O,a1 as D}from"./vue-CTYOaxLZ.js";import{p as $}from"./hashTab-C4jxjRmY.js";import{n as J,c as w}from"./element-plus-C80AGGUH.js";import{c as L}from"./clipboard-C-ost6Mg.js";import{_ as A}from"./index-FouCxvRM.js";const H=["human","ai"],z={human:"人工接入",ai:"AI 接入"},X={class:"code-block"},Z=I({__name:"CodeBlock",props:{code:{}},setup(y){const e=y,v=S(!1),i=S(!1);async function p(){v.value=!0;try{await L(e.code,""),i.value=!0,w.success("已复制"),setTimeout(()=>{i.value=!1},1500)}catch{w.error("复制失败, 请手动复制")}finally{v.value=!1}}return(C,r)=>{const u=k("el-button");return f(),T("div",X,[s(u,{size:"small",icon:R(J),class:"copy-btn",loading:v.value,onClick:p},{default:_(()=>[l(c(i.value?"已复制":"复制"),1)]),_:1},8,["icon","loading"]),t("pre",null,[t("code",null,c(y.code),1)])])}}}),d=A(Z,[["__scopeId","data-v-437d41bb"]]),Q={class:"onboarding"},tt={class:"onboarding-toolbar"},ot={class:"onboarding-nav","aria-label":"章节导航"},nt=["href","onClick"],lt=["id"],et={class:"onboarding-toolbox"},st=["id"],it=["id"],at=["id"],rt=["id"],E=".\\scripts\\start_gateway.ps1",x="python -m src.unihive.gateway_server --transport stdio",ut=`{
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

asyncio.run(main())`,ct=`{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "src.unihive.gateway_server", "--transport", "stdio"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}`,pt=`import asyncio
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
print(match.data)`,gt=I({__name:"OnboardingPanel",setup(y){const e=[{id:"sec-quickstart",title:"快速开始"},{id:"sec-integration",title:"接入方式"},{id:"sec-interfaces",title:"MCP APIs"},{id:"sec-examples",title:"常用示例"},{id:"sec-troubleshoot",title:"故障排查"}],v=M(),i=S(e[0].id);async function p(a,o){try{await L(a,""),w.success(o)}catch{w.error("复制失败, 请手动复制")}}function C(){v.push({name:"interfaces"})}function r(a){const o=document.getElementById(a);o&&(o.scrollIntoView({behavior:"smooth",block:"start"}),i.value=a)}let u=null;function P(){const a=e.map(o=>document.getElementById(o.id)).filter(o=>!!o);a.length!==0&&(u=new IntersectionObserver(o=>{const m=o.filter(n=>n.isIntersecting).sort((n,b)=>n.target.getBoundingClientRect().top-b.target.getBoundingClientRect().top);m.length>0&&(i.value=m[0].target.id)},{rootMargin:"-20% 0px -70% 0px",threshold:0}),a.forEach(o=>u.observe(o)))}return j(()=>{P()}),G(()=>{u==null||u.disconnect(),u=null}),(a,o)=>{const m=k("el-button");return f(),T("article",Q,[t("div",tt,[s(m,{type:"primary",onClick:C},{default:_(()=>[...o[2]||(o[2]=[l("→ MCP APIs",-1)])]),_:1})]),t("nav",ot,[(f(),T(q,null,B(e,n=>t("a",{key:n.id,href:`#${n.id}`,class:N({active:i.value===n.id}),onClick:K(b=>r(n.id),["prevent"])},c(n.title),11,nt)),64))]),t("section",{id:e[0].id,class:"onboarding-section"},[t("h3",null,c(e[0].title),1),o[5]||(o[5]=t("p",null,"UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。",-1)),o[6]||(o[6]=t("h4",null,"1.1 启动网关",-1)),o[7]||(o[7]=t("p",null,[l("网关默认以 "),t("strong",null,"HTTP 模式"),l("启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:")],-1)),t("div",et,[s(m,{onClick:o[0]||(o[0]=n=>p(E,"已复制 HTTP 启动命令"))},{default:_(()=>[...o[3]||(o[3]=[l("复制 HTTP 启动命令",-1)])]),_:1}),s(m,{onClick:o[1]||(o[1]=n=>p(x,"已复制 STDIO 启动命令"))},{default:_(()=>[...o[4]||(o[4]=[l("复制 STDIO 启动命令",-1)])]),_:1})]),s(d,{code:E}),o[8]||(o[8]=t("p",null,[l("启动后, 控制台和 HTTP 网关共用端口 "),t("code",null,"http://127.0.0.1:18080"),l(" (MCP 接口在 "),t("code",null,"/mcp"),l(" 子路径下)。")],-1)),o[9]||(o[9]=t("h4",null,"1.2 配置文件",-1)),o[10]||(o[10]=t("p",null,[l("上游数据源在 "),t("code",null,"config/upstreams.yaml"),l(" 中配置。修改后需要重启网关生效。")],-1))],8,lt),t("section",{id:e[1].id,class:"onboarding-section"},[t("h3",null,c(e[1].title),1),o[11]||(o[11]=t("h4",null,"2.1 HTTP 模式 (推荐)",-1)),o[12]||(o[12]=t("p",null,[l("网关以 "),t("strong",null,"Streamable HTTP"),l(" 模式对外提供 MCP 接口, 监听 "),t("code",null,"http://127.0.0.1:18080/mcp"),l("。运行 "),t("code",null,".\\scripts\\start_gateway.ps1"),l(" 会同时启动控制台和 HTTP 网关。")],-1)),o[13]||(o[13]=t("p",null,[t("strong",null,"Claude Desktop / Cursor 配置:")],-1)),s(d,{code:ut}),o[14]||(o[14]=t("p",null,[t("strong",null,"Python SDK 示例:")],-1)),s(d,{code:dt}),o[15]||(o[15]=t("h4",null,"2.2 STDIO 模式 (备选)",-1)),o[16]||(o[16]=t("p",null,"用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:",-1)),s(d,{code:ct}),o[17]||(o[17]=t("p",null,"或命令行单独启动 STDIO 模式:",-1)),s(d,{code:x})],8,st),t("section",{id:e[2].id,class:"onboarding-section"},[t("h3",null,c(e[2].title),1),o[18]||(o[18]=t("p",null,[l("当前网关对外暴露约 "),t("strong",null,"160"),l(" 个 MCP 接口, 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在 "),t("strong",null,'"MCP APIs"'),l(" 标签页。")],-1))],8,it),t("section",{id:e[3].id,class:"onboarding-section"},[t("h3",null,c(e[3].title),1),o[19]||(o[19]=t("h4",null,"4.1 Python SDK (推荐)",-1)),o[20]||(o[20]=t("p",null,[l("通过 "),t("a",{href:"https://github.com/jlowin/fastmcp",target:"_blank",rel:"noopener"},"FastMCP"),l(" 客户端连接 HTTP 网关 (默认 "),t("code",null,"http://127.0.0.1:18080/mcp"),l(")。所有 tool 名、参数和返回结构以 "),t("strong",null,'"MCP APIs"'),l(" 标签页为准。")],-1)),s(d,{code:pt}),o[21]||(o[21]=t("h4",null,"4.2 日 K 线 (含日期范围)",-1)),s(d,{code:vt}),o[22]||(o[22]=t("h4",null,"4.3 搜索股票",-1)),s(d,{code:mt}),o[23]||(o[23]=t("div",{class:"onboarding-section-meta"},[t("strong",null,"提示:"),l(" 如果你的客户端只支持 stdio, 启动 STDIO 模式: "),t("code",null,"python -m src.unihive.gateway_server --transport stdio"),l("。 调试可用 MCP Inspector ("),t("code",null,"npx -y @modelcontextprotocol/inspector"),l("), Python 可用官方 SDK。 Windows 下 "),t("code",null,"cwd"),l(" 用项目绝对路径。 ")],-1))],8,at),t("section",{id:e[4].id,class:"onboarding-section"},[t("h3",null,c(e[4].title),1),o[24]||(o[24]=V('<h4 data-v-43756501>5.1 连接失败</h4><ul data-v-43756501><li data-v-43756501>检查网关是否已启动: 访问 <code data-v-43756501>http://127.0.0.1:18080</code></li><li data-v-43756501>检查客户端配置中的 <code data-v-43756501>cwd</code> 路径是否正确</li><li data-v-43756501>Windows 下确认 Python 已加入 PATH</li></ul><h4 data-v-43756501>5.2 接口返回错误</h4><ul data-v-43756501><li data-v-43756501>查看控制台&quot;上游状态&quot;标签页, 确认对应上游是否在线</li><li data-v-43756501>查看返回的 <code data-v-43756501>last_error</code> 字段</li><li data-v-43756501>对应上游若被禁用, 需修改 <code data-v-43756501>config/upstreams.yaml</code> 并重启</li></ul><h4 data-v-43756501>5.3 参数错误</h4><ul data-v-43756501><li data-v-43756501>股票代码由网关自动标准化 (<code data-v-43756501>000001</code>、<code data-v-43756501>sh600519</code>、<code data-v-43756501>600519.SH</code>、<code data-v-43756501>sz000001</code> 都能识别)</li><li data-v-43756501>日期统一使用 <code data-v-43756501>YYYYMMDD</code> (如 <code data-v-43756501>20260301</code>)</li><li data-v-43756501>具体参数以 <strong data-v-43756501>&quot;MCP APIs&quot;</strong> 标签页每个 tool 行的&quot;详情&quot;展开为准</li></ul><h4 data-v-43756501>5.4 反馈渠道</h4><p data-v-43756501>遇到问题请提交 Issue 到 <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener" data-v-43756501>github.com/unihive/unihive</a>, 并附上控制台截图与接口返回内容。点击 <strong data-v-43756501>&quot;MCP APIs&quot;</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>',8))],8,rt)])}}}),ft=A(gt,[["__scopeId","data-v-43756501"]]);function U(){return typeof location>"u"?"http://127.0.0.1:18080":location.origin}function h(){return`${U()}/mcp`}const _t={class:"ai-onboarding"},Ct={class:"ai-section"},Tt={class:"muted"},yt={class:"ai-section"},Pt={class:"ai-section"},bt={class:"ai-section"},ht="~200",St=I({__name:"AiOnboardingPanel",setup(y){const e=M(),v=g(()=>U()),i=g(()=>h());function p(){return JSON.stringify({mcpServers:{unihive:{url:h()}}},null,2)}const C=g(p),r=g(p),u=g(p),P=g(()=>JSON.stringify({mcpServers:{unihive:{command:"python",args:["-m","src.unihive.gateway_server","--transport","stdio"],cwd:"D:/fintech_workspace/unihive"}}},null,2)),a=`## 金融数据工具

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
概念板块: 886042.TI`;function o(){e.push({name:"status"})}return(m,n)=>{const b=k("el-button");return f(),T("article",_t,[n[21]||(n[21]=t("section",{class:"ai-section"},[t("h3",null,"选择你的 AI 客户端"),t("p",null," UniHive 通过标准 MCP 协议暴露, 兼容 Claude Desktop、Cursor、Cline 等主流 AI 客户端。 以下是 3 个最常用客户端的配置示例, 复制后粘贴到对应配置文件即可接入。 "),t("p",null,[l(" 推荐使用 "),t("strong",null,"HTTP 模式"),l("(无需启动额外进程);如果客户端不支持 HTTP MCP, 可回退到 "),t("strong",null,"STDIO 模式"),l("。 ")])],-1)),t("section",Ct,[n[1]||(n[1]=t("h3",null,"HTTP 模式 (推荐)",-1)),t("p",Tt,[n[0]||(n[0]=l(" 当前网关地址: ",-1)),t("code",null,c(i.value),1)]),n[2]||(n[2]=t("h4",null,"Claude Desktop",-1)),n[3]||(n[3]=t("p",null,[l(" 配置文件路径: "),t("code",null,"%APPDATA%\\Claude\\claude_desktop_config.json"),l(" (Windows) / "),t("code",null,"~/Library/Application Support/Claude/claude_desktop_config.json"),l(" (macOS) / "),t("code",null,"~/.config/Claude/claude_desktop_config.json"),l(" (Linux) ")],-1)),s(d,{code:C.value},null,8,["code"]),n[4]||(n[4]=t("h4",null,"Cursor",-1)),n[5]||(n[5]=t("p",null,[l(" 配置文件路径: "),t("code",null,"~/.cursor/mcp.json"),l("(全局) 或 项目根目录 "),t("code",null,".cursor/mcp.json"),l("(项目级优先) ")],-1)),s(d,{code:r.value},null,8,["code"]),n[6]||(n[6]=t("h4",null,"Cline (VS Code 扩展)",-1)),n[7]||(n[7]=t("p",null,[l(' 在 VS Code 左侧活动栏点击 Cline 图标 → 顶栏 "MCP Servers" → "Configure" 按钮, 会自动打开 '),t("code",null,"cline_mcp_settings.json"),l("。配置 schema 与 Cursor 相同。 ")],-1)),s(d,{code:u.value},null,8,["code"])]),t("section",yt,[n[8]||(n[8]=t("h3",null,"STDIO 模式 (备选)",-1)),n[9]||(n[9]=t("p",null,[l(" 适用于不支持 HTTP MCP 的旧客户端。需要在配置文件路径下用 "),t("code",null,"command"),l(" + "),t("code",null,"args"),l(" + "),t("code",null,"cwd"),l(" 三段指定启动方式。"),t("code",null,"cwd"),l(" 必须指向 UniHive 项目根目录。 ")],-1)),s(d,{code:P.value},null,8,["code"])]),t("section",Pt,[n[10]||(n[10]=t("h3",null,"AI Agent 系统提示词 (可选)",-1)),n[11]||(n[11]=t("p",null," 把以下提示词添加到 AI 客户端的 system prompt / custom instructions 字段, 让 Agent 自动知道如何调用 UniHive 工具。 ",-1)),s(d,{code:a})]),t("section",bt,[n[19]||(n[19]=t("h3",null,"验证是否接入成功",-1)),t("ol",null,[n[12]||(n[12]=t("li",null,"重启 AI 客户端 (Claude Desktop 完全退出后再打开)。",-1)),t("li",null," 在客户端中查看 MCP 工具列表, 应能看到 UniHive 注册的 "+c(ht)+" 个工具 (按上游分组)。 "),n[13]||(n[13]=t("li",null,' 向 AI 提一个简单请求: "搜索股票 茅台"。若返回结果则接入成功。 ',-1))]),n[20]||(n[20]=t("p",null,"若失败, 按以下顺序排查:",-1)),t("ul",null,[t("li",null,[n[14]||(n[14]=l("确认网关已启动: 浏览器访问 ",-1)),t("code",null,c(v.value),1),n[15]||(n[15]=l(" 能看到控制台首页。",-1))]),n[16]||(n[16]=t("li",null,[l("查看 "),t("strong",null,'"上游状态"'),l(" 标签页, 确认上游是否全绿。")],-1)),n[17]||(n[17]=t("li",null,'查看客户端日志: Claude Desktop 的 "Developer" 菜单 / Cursor 的 "Output → MCP"。',-1))]),s(b,{type:"primary",onClick:o},{default:_(()=>[...n[18]||(n[18]=[l("→ 上游状态",-1)])]),_:1})]),n[22]||(n[22]=t("section",{class:"ai-section"},[t("h3",null,"自定义端口"),t("p",null,[l(" 如果你修改了启动端口 (例如改成 "),t("code",null,"19080"),l("), 上面 JSON 示例中的 URL 也要相应替换。 推荐保持默认 "),t("code",null,"18080"),l(" 以便与控制台共用。 ")])],-1))])}}}),wt=A(St,[["__scopeId","data-v-8745f40c"]]),It={class:"cfg-subtabs",role:"tablist","aria-label":"指南子标签"},kt=["aria-selected","onClick"],At={class:"guides-panel-stack"},Ht={class:"cfg-panel","aria-label":"人工接入指南"},Mt={class:"cfg-panel","aria-label":"AI 接入指南"},Ot=I({__name:"GuidesView",setup(y){const e=W(),v=M(),i=S("human");i.value=$(e.query.tab,H,"human"),Y(()=>e.query.tab,r=>{i.value=$(r,H,"human")});function p(r){i.value!==r&&(i.value=r,v.replace({query:{...e.query,tab:r}}))}const C=g(()=>H.map(r=>({key:r,title:z[r]})));return(r,u)=>{const P=k("el-card");return f(),F(P,{shadow:"never",class:"panel-card"},{header:_(()=>[...u[0]||(u[0]=[t("div",{class:"card-toolbar"},[t("h2",{class:"card-title"},"接入指南")],-1)])]),default:_(()=>[t("nav",It,[(f(!0),T(q,null,B(C.value,a=>(f(),T("button",{key:a.key,type:"button",class:N(["cfg-subtab",{active:i.value===a.key}]),role:"tab","aria-selected":i.value===a.key,onClick:o=>p(a.key)},c(a.title),11,kt))),128))]),t("div",At,[O(t("section",Ht,[s(ft)],512),[[D,i.value==="human"]]),O(t("section",Mt,[s(wt)],512),[[D,i.value==="ai"]])])]),_:1})}}}),Bt=A(Ot,[["__scopeId","data-v-35b4455f"]]);export{Bt as default};
