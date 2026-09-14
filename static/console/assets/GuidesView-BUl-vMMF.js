import{G as C,aj as I,L as _,M as g,a0 as i,u as q,W as m,_ as o,$ as p,P as e,r as P,aE as w,b as N,A as R,F as D,ae as $,ac as F,S as x,aF as G,w as K,O as L,aD as Y,X as M,a1 as k,k as V}from"./vue-Bkm1mUac.js";import{c as U,a as T}from"./element-plus-C6XQG9R-.js";import{c as B}from"./clipboard-TZk9ML3h.js";import{_ as S}from"./index-BfcdAoSH.js";const O=["human","ai"],W={human:"人工接入",ai:"AI 接入"},j={class:"code-block"},X=C({__name:"CodeBlock",props:{code:{}},setup(h){const a=h,u=P(!1),l=P(!1);async function n(){u.value=!0;try{await B(a.code,""),l.value=!0,T.success("已复制"),setTimeout(()=>{l.value=!1},1500)}catch{T.error("复制失败, 请手动复制")}finally{u.value=!1}}return(b,y)=>{const s=I("el-button");return _(),g("div",j,[i(s,{size:"small",icon:q(U),class:"copy-btn",loading:u.value,onClick:n},{default:m(()=>[o(p(l.value?"已复制":"复制"),1)]),_:1},8,["icon","loading"]),e("pre",null,[e("code",null,p(h.code),1)])])}}}),d=S(X,[["__scopeId","data-v-e296e183"]]),z={class:"onboarding"},J={class:"onboarding-toolbar"},Q={class:"onboarding-nav","aria-label":"章节导航"},Z=["href","onClick"],tt=["id"],et={class:"onboarding-toolbox"},ot=["id"],nt=["id"],st=["id"],at=["id"],E=".\\scripts\\start_gateway.ps1",H="python -m src.unihive.gateway_server --transport stdio",lt=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,it=`import asyncio
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
}`,ct=`import asyncio
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

asyncio.run(main())`,dt=`bar = await client.call_tool(
    "a_share_prices_historical",
    {"thscode": "600519.SH", "start_date": "2026-01-01", "end_date": "2026-03-01"}
)
print(bar.data)`,ut=`match = await client.call_tool(
    "search_stock_local",
    {"keyword": "茅台"}
)
print(match.data)`,pt=C({__name:"OnboardingPanel",setup(h){const a=[{id:"sec-quickstart",title:"快速开始"},{id:"sec-integration",title:"接入方式"},{id:"sec-interfaces",title:"MCP APIs"},{id:"sec-examples",title:"常用示例"},{id:"sec-troubleshoot",title:"故障排查"}],u=w(),l=P(a[0].id);async function n(r,t){try{await B(r,""),T.success(t)}catch{T.error("复制失败, 请手动复制")}}function b(){u.push({name:"interfaces"})}function y(r){const t=document.getElementById(r);t&&(t.scrollIntoView({behavior:"smooth",block:"start"}),l.value=r)}let s=null;function v(){const r=a.map(t=>document.getElementById(t.id)).filter(t=>!!t);r.length!==0&&(s=new IntersectionObserver(t=>{const f=t.filter(c=>c.isIntersecting).sort((c,A)=>c.target.getBoundingClientRect().top-A.target.getBoundingClientRect().top);f.length>0&&(l.value=f[0].target.id)},{rootMargin:"-20% 0px -70% 0px",threshold:0}),r.forEach(t=>s.observe(t)))}return N(()=>{v()}),R(()=>{s==null||s.disconnect(),s=null}),(r,t)=>{const f=I("el-button");return _(),g("article",z,[e("div",J,[i(f,{type:"primary",onClick:b},{default:m(()=>[...t[2]||(t[2]=[o("→ MCP APIs",-1)])]),_:1})]),e("nav",Q,[(_(),g(D,null,$(a,c=>e("a",{key:c.id,href:`#${c.id}`,class:x({active:l.value===c.id}),onClick:F(A=>y(c.id),["prevent"])},p(c.title),11,Z)),64))]),e("section",{id:a[0].id,class:"onboarding-section"},[e("h3",null,p(a[0].title),1),t[5]||(t[5]=e("p",null,"UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。",-1)),t[6]||(t[6]=e("h4",null,"1.1 启动网关",-1)),t[7]||(t[7]=e("p",null,[o("网关默认以 "),e("strong",null,"HTTP 模式"),o("启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:")],-1)),e("div",et,[i(f,{onClick:t[0]||(t[0]=c=>n(E,"已复制 HTTP 启动命令"))},{default:m(()=>[...t[3]||(t[3]=[o("复制 HTTP 启动命令",-1)])]),_:1}),i(f,{onClick:t[1]||(t[1]=c=>n(H,"已复制 STDIO 启动命令"))},{default:m(()=>[...t[4]||(t[4]=[o("复制 STDIO 启动命令",-1)])]),_:1})]),i(d,{code:E}),t[8]||(t[8]=e("p",null,[o("启动后, 控制台和 HTTP 网关共用端口 "),e("code",null,"http://127.0.0.1:18080"),o(" (MCP 接口在 "),e("code",null,"/mcp"),o(" 子路径下)。")],-1)),t[9]||(t[9]=e("h4",null,"1.2 配置文件",-1)),t[10]||(t[10]=e("p",null,[o("上游数据源在 "),e("code",null,"config/upstreams.yaml"),o(" 中配置。修改后需要重启网关生效。")],-1))],8,tt),e("section",{id:a[1].id,class:"onboarding-section"},[e("h3",null,p(a[1].title),1),t[11]||(t[11]=e("h4",null,"2.1 HTTP 模式 (推荐)",-1)),t[12]||(t[12]=e("p",null,[o("网关以 "),e("strong",null,"Streamable HTTP"),o(" 模式对外提供 MCP 接口, 监听 "),e("code",null,"http://127.0.0.1:18080/mcp"),o("。运行 "),e("code",null,".\\scripts\\start_gateway.ps1"),o(" 会同时启动控制台和 HTTP 网关。")],-1)),t[13]||(t[13]=e("p",null,[e("strong",null,"Claude Desktop / Cursor 配置:")],-1)),i(d,{code:lt}),t[14]||(t[14]=e("p",null,[e("strong",null,"Python SDK 示例:")],-1)),i(d,{code:it}),t[15]||(t[15]=e("h4",null,"2.2 STDIO 模式 (备选)",-1)),t[16]||(t[16]=e("p",null,"用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:",-1)),i(d,{code:rt}),t[17]||(t[17]=e("p",null,"或命令行单独启动 STDIO 模式:",-1)),i(d,{code:H})],8,ot),e("section",{id:a[2].id,class:"onboarding-section"},[e("h3",null,p(a[2].title),1),t[18]||(t[18]=e("p",null,[o("当前网关对外暴露约 "),e("strong",null,"160"),o(" 个 MCP 接口, 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在 "),e("strong",null,'"MCP APIs"'),o(" 标签页。")],-1))],8,nt),e("section",{id:a[3].id,class:"onboarding-section"},[e("h3",null,p(a[3].title),1),t[19]||(t[19]=e("h4",null,"4.1 Python SDK (推荐)",-1)),t[20]||(t[20]=e("p",null,[o("通过 "),e("a",{href:"https://github.com/jlowin/fastmcp",target:"_blank",rel:"noopener"},"FastMCP"),o(" 客户端连接 HTTP 网关 (默认 "),e("code",null,"http://127.0.0.1:18080/mcp"),o(")。所有 tool 名、参数和返回结构以 "),e("strong",null,'"MCP APIs"'),o(" 标签页为准。")],-1)),i(d,{code:ct}),t[21]||(t[21]=e("h4",null,"4.2 日 K 线 (含日期范围)",-1)),i(d,{code:dt}),t[22]||(t[22]=e("h4",null,"4.3 搜索股票",-1)),i(d,{code:ut}),t[23]||(t[23]=e("div",{class:"onboarding-section-meta"},[e("strong",null,"提示:"),o(" 如果你的客户端只支持 stdio, 启动 STDIO 模式: "),e("code",null,"python -m src.unihive.gateway_server --transport stdio"),o("。 调试可用 MCP Inspector ("),e("code",null,"npx -y @modelcontextprotocol/inspector"),o("), Python 可用官方 SDK。 Windows 下 "),e("code",null,"cwd"),o(" 用项目绝对路径。 ")],-1))],8,st),e("section",{id:a[4].id,class:"onboarding-section"},[e("h3",null,p(a[4].title),1),t[24]||(t[24]=G('<h4 data-v-c5e43fb9>5.1 连接失败</h4><ul data-v-c5e43fb9><li data-v-c5e43fb9>检查网关是否已启动: 访问 <code data-v-c5e43fb9>http://127.0.0.1:18080</code></li><li data-v-c5e43fb9>检查客户端配置中的 <code data-v-c5e43fb9>cwd</code> 路径是否正确</li><li data-v-c5e43fb9>Windows 下确认 Python 已加入 PATH</li></ul><h4 data-v-c5e43fb9>5.2 接口返回错误</h4><ul data-v-c5e43fb9><li data-v-c5e43fb9>查看控制台&quot;上游状态&quot;标签页, 确认对应上游是否在线</li><li data-v-c5e43fb9>查看返回的 <code data-v-c5e43fb9>last_error</code> 字段</li><li data-v-c5e43fb9>对应上游若被禁用, 需修改 <code data-v-c5e43fb9>config/upstreams.yaml</code> 并重启</li></ul><h4 data-v-c5e43fb9>5.3 参数错误</h4><ul data-v-c5e43fb9><li data-v-c5e43fb9>股票代码由网关自动标准化 (<code data-v-c5e43fb9>000001</code>、<code data-v-c5e43fb9>sh600519</code>、<code data-v-c5e43fb9>600519.SH</code>、<code data-v-c5e43fb9>sz000001</code> 都能识别)</li><li data-v-c5e43fb9>日期统一使用 <code data-v-c5e43fb9>YYYYMMDD</code> (如 <code data-v-c5e43fb9>20260301</code>)</li><li data-v-c5e43fb9>具体参数以 <strong data-v-c5e43fb9>&quot;MCP APIs&quot;</strong> 标签页每个 tool 行的&quot;详情&quot;展开为准</li></ul><h4 data-v-c5e43fb9>5.4 反馈渠道</h4><p data-v-c5e43fb9>遇到问题请提交 Issue 到 <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener" data-v-c5e43fb9>github.com/unihive/unihive</a>, 并附上控制台截图与接口返回内容。点击 <strong data-v-c5e43fb9>&quot;MCP APIs&quot;</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>',8))],8,at)])}}}),vt=S(pt,[["__scopeId","data-v-c5e43fb9"]]),ft={class:"ai-onboarding"},_t={class:"ai-section"},mt={class:"ai-section"},bt={class:"ai-section"},gt=`## 金融数据工具

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
概念板块: 886042.TI`,ht=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,yt=C({__name:"AiOnboardingPanel",setup(h){const a=w();function u(){a.push({name:"interfaces"})}return(l,n)=>{const b=I("el-button");return _(),g("article",ft,[e("section",_t,[n[0]||(n[0]=e("h3",null,"AI Agent 系统提示词",-1)),n[1]||(n[1]=e("p",null,"复制以下提示词给你的 AI Agent, 让它能够接入 UniHive MCP:",-1)),i(d,{code:gt})]),e("section",mt,[n[2]||(n[2]=e("h3",null,"配置 MCP Server",-1)),n[3]||(n[3]=e("p",null,"在你的 Agent 配置文件中添加:",-1)),i(d,{code:ht})]),e("section",bt,[n[5]||(n[5]=e("h3",null,"工具清单",-1)),n[6]||(n[6]=e("p",null,[o("完整工具列表 (含参数说明) 请查看 "),e("strong",null,'"MCP APIs"'),o(" 标签页。")],-1)),i(b,{type:"primary",onClick:u},{default:m(()=>[...n[4]||(n[4]=[o("→ MCP APIs",-1)])]),_:1})])])}}}),Pt=S(yt,[["__scopeId","data-v-729a6702"]]),Tt={class:"cfg-subtabs","aria-label":"指南子标签"},Ct=["aria-current","onClick"],It={class:"guides-panel-stack"},St={class:"cfg-panel","aria-label":"人工接入指南"},wt={class:"cfg-panel","aria-label":"AI 接入指南"},At=C({__name:"GuidesView",setup(h){const a=Y(),u=w(),l=P("human");function n(s){const v=Array.isArray(s)?s[0]:s,r=typeof v=="string"?v:"";return O.includes(r)?r:"human"}l.value=n(a.query.tab),K(()=>a.query.tab,s=>{l.value=n(s)});function b(s){l.value!==s&&(l.value=s,u.replace({query:{...a.query,tab:s}}))}const y=V(()=>O.map(s=>({key:s,title:W[s]})));return(s,v)=>{const r=I("el-card");return _(),L(r,{shadow:"never",class:"panel-card"},{header:m(()=>[...v[0]||(v[0]=[e("div",{class:"card-toolbar"},[e("h2",{class:"card-title"},"接入指南")],-1)])]),default:m(()=>[e("nav",Tt,[(_(!0),g(D,null,$(y.value,t=>(_(),g("button",{key:t.key,type:"button",class:x(["cfg-subtab",{active:l.value===t.key}]),"aria-current":l.value===t.key?"page":void 0,onClick:f=>b(t.key)},p(t.title),11,Ct))),128))]),e("div",It,[M(e("section",St,[i(vt)],512),[[k,l.value==="human"]]),M(e("section",wt,[i(Pt)],512),[[k,l.value==="ai"]])])]),_:1})}}}),Ht=S(At,[["__scopeId","data-v-528b5506"]]);export{Ht as default};
