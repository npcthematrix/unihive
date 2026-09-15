import{G as b,aj as C,L as _,M as h,a0 as l,u as N,W as m,_ as e,$ as v,P as t,c as y,az as A,d as R,A as G,F as $,ae as q,ac as F,S as x,aG as K,w as L,O as Y,aF as V,X as k,a1 as O,k as U}from"./vue-CTYOaxLZ.js";import{p as H}from"./hashTab-C4jxjRmY.js";import{n as W,c as T}from"./element-plus-C80AGGUH.js";import{c as B}from"./clipboard-C-ost6Mg.js";import{_ as I}from"./index-DAn5C4Yu.js";const w=["human","ai"],j={human:"人工接入",ai:"AI 接入"},z={class:"code-block"},X=b({__name:"CodeBlock",props:{code:{}},setup(P){const n=P,p=y(!1),a=y(!1);async function s(){p.value=!0;try{await B(n.code,""),a.value=!0,T.success("已复制"),setTimeout(()=>{a.value=!1},1500)}catch{T.error("复制失败, 请手动复制")}finally{p.value=!1}}return(f,r)=>{const d=C("el-button");return _(),h("div",z,[l(d,{size:"small",icon:N(W),class:"copy-btn",loading:p.value,onClick:s},{default:m(()=>[e(v(a.value?"已复制":"复制"),1)]),_:1},8,["icon","loading"]),t("pre",null,[t("code",null,v(P.code),1)])])}}}),u=I(X,[["__scopeId","data-v-437d41bb"]]),J={class:"onboarding"},Z={class:"onboarding-toolbar"},Q={class:"onboarding-nav","aria-label":"章节导航"},tt=["href","onClick"],ot=["id"],et={class:"onboarding-toolbox"},nt=["id"],st=["id"],at=["id"],lt=["id"],E=".\\scripts\\start_gateway.ps1",D="python -m src.unihive.gateway_server --transport stdio",it=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,rt=`import asyncio
from fastmcp.client import Client

async def main():
    async with Client("http://127.0.0.1:18080/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_realtime_quote", {"symbol": "600000"})
        print(result.data)

asyncio.run(main())`,dt=`{
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

asyncio.run(main())`,ut=`bar = await client.call_tool(
    "a_share_prices_historical",
    {"thscode": "600519.SH", "start_date": "2026-01-01", "end_date": "2026-03-01"}
)
print(bar.data)`,pt=`match = await client.call_tool(
    "search_stock_local",
    {"keyword": "茅台"}
)
print(match.data)`,vt=b({__name:"OnboardingPanel",setup(P){const n=[{id:"sec-quickstart",title:"快速开始"},{id:"sec-integration",title:"接入方式"},{id:"sec-interfaces",title:"MCP APIs"},{id:"sec-examples",title:"常用示例"},{id:"sec-troubleshoot",title:"故障排查"}],p=A(),a=y(n[0].id);async function s(i,o){try{await B(i,""),T.success(o)}catch{T.error("复制失败, 请手动复制")}}function f(){p.push({name:"interfaces"})}function r(i){const o=document.getElementById(i);o&&(o.scrollIntoView({behavior:"smooth",block:"start"}),a.value=i)}let d=null;function S(){const i=n.map(o=>document.getElementById(o.id)).filter(o=>!!o);i.length!==0&&(d=new IntersectionObserver(o=>{const g=o.filter(c=>c.isIntersecting).sort((c,M)=>c.target.getBoundingClientRect().top-M.target.getBoundingClientRect().top);g.length>0&&(a.value=g[0].target.id)},{rootMargin:"-20% 0px -70% 0px",threshold:0}),i.forEach(o=>d.observe(o)))}return R(()=>{S()}),G(()=>{d==null||d.disconnect(),d=null}),(i,o)=>{const g=C("el-button");return _(),h("article",J,[t("div",Z,[l(g,{type:"primary",onClick:f},{default:m(()=>[...o[2]||(o[2]=[e("→ MCP APIs",-1)])]),_:1})]),t("nav",Q,[(_(),h($,null,q(n,c=>t("a",{key:c.id,href:`#${c.id}`,class:x({active:a.value===c.id}),onClick:F(M=>r(c.id),["prevent"])},v(c.title),11,tt)),64))]),t("section",{id:n[0].id,class:"onboarding-section"},[t("h3",null,v(n[0].title),1),o[5]||(o[5]=t("p",null,"UniHive MCP 是一个统一的金融数据网关, 把通达信、同花顺等数据源聚合在同一个 MCP 接口后面。",-1)),o[6]||(o[6]=t("h4",null,"1.1 启动网关",-1)),o[7]||(o[7]=t("p",null,[e("网关默认以 "),t("strong",null,"HTTP 模式"),e("启动 (推荐), 同时也支持 STDIO 模式。在项目根目录下运行:")],-1)),t("div",et,[l(g,{onClick:o[0]||(o[0]=c=>s(E,"已复制 HTTP 启动命令"))},{default:m(()=>[...o[3]||(o[3]=[e("复制 HTTP 启动命令",-1)])]),_:1}),l(g,{onClick:o[1]||(o[1]=c=>s(D,"已复制 STDIO 启动命令"))},{default:m(()=>[...o[4]||(o[4]=[e("复制 STDIO 启动命令",-1)])]),_:1})]),l(u,{code:E}),o[8]||(o[8]=t("p",null,[e("启动后, 控制台和 HTTP 网关共用端口 "),t("code",null,"http://127.0.0.1:18080"),e(" (MCP 接口在 "),t("code",null,"/mcp"),e(" 子路径下)。")],-1)),o[9]||(o[9]=t("h4",null,"1.2 配置文件",-1)),o[10]||(o[10]=t("p",null,[e("上游数据源在 "),t("code",null,"config/upstreams.yaml"),e(" 中配置。修改后需要重启网关生效。")],-1))],8,ot),t("section",{id:n[1].id,class:"onboarding-section"},[t("h3",null,v(n[1].title),1),o[11]||(o[11]=t("h4",null,"2.1 HTTP 模式 (推荐)",-1)),o[12]||(o[12]=t("p",null,[e("网关以 "),t("strong",null,"Streamable HTTP"),e(" 模式对外提供 MCP 接口, 监听 "),t("code",null,"http://127.0.0.1:18080/mcp"),e("。运行 "),t("code",null,".\\scripts\\start_gateway.ps1"),e(" 会同时启动控制台和 HTTP 网关。")],-1)),o[13]||(o[13]=t("p",null,[t("strong",null,"Claude Desktop / Cursor 配置:")],-1)),l(u,{code:it}),o[14]||(o[14]=t("p",null,[t("strong",null,"Python SDK 示例:")],-1)),l(u,{code:rt}),o[15]||(o[15]=t("h4",null,"2.2 STDIO 模式 (备选)",-1)),o[16]||(o[16]=t("p",null,"用于不支持 HTTP MCP 的旧客户端, 或本地脚本场景。需要单独启动 gateway 进程:",-1)),l(u,{code:dt}),o[17]||(o[17]=t("p",null,"或命令行单独启动 STDIO 模式:",-1)),l(u,{code:D})],8,nt),t("section",{id:n[2].id,class:"onboarding-section"},[t("h3",null,v(n[2].title),1),o[18]||(o[18]=t("p",null,[e("当前网关对外暴露约 "),t("strong",null,"160"),e(" 个 MCP 接口, 按上游自动分组、路由与缓存。完整列表 (含每个 tool 的描述、参数、缓存策略) 都在 "),t("strong",null,'"MCP APIs"'),e(" 标签页。")],-1))],8,st),t("section",{id:n[3].id,class:"onboarding-section"},[t("h3",null,v(n[3].title),1),o[19]||(o[19]=t("h4",null,"4.1 Python SDK (推荐)",-1)),o[20]||(o[20]=t("p",null,[e("通过 "),t("a",{href:"https://github.com/jlowin/fastmcp",target:"_blank",rel:"noopener"},"FastMCP"),e(" 客户端连接 HTTP 网关 (默认 "),t("code",null,"http://127.0.0.1:18080/mcp"),e(")。所有 tool 名、参数和返回结构以 "),t("strong",null,'"MCP APIs"'),e(" 标签页为准。")],-1)),l(u,{code:ct}),o[21]||(o[21]=t("h4",null,"4.2 日 K 线 (含日期范围)",-1)),l(u,{code:ut}),o[22]||(o[22]=t("h4",null,"4.3 搜索股票",-1)),l(u,{code:pt}),o[23]||(o[23]=t("div",{class:"onboarding-section-meta"},[t("strong",null,"提示:"),e(" 如果你的客户端只支持 stdio, 启动 STDIO 模式: "),t("code",null,"python -m src.unihive.gateway_server --transport stdio"),e("。 调试可用 MCP Inspector ("),t("code",null,"npx -y @modelcontextprotocol/inspector"),e("), Python 可用官方 SDK。 Windows 下 "),t("code",null,"cwd"),e(" 用项目绝对路径。 ")],-1))],8,at),t("section",{id:n[4].id,class:"onboarding-section"},[t("h3",null,v(n[4].title),1),o[24]||(o[24]=K('<h4 data-v-43756501>5.1 连接失败</h4><ul data-v-43756501><li data-v-43756501>检查网关是否已启动: 访问 <code data-v-43756501>http://127.0.0.1:18080</code></li><li data-v-43756501>检查客户端配置中的 <code data-v-43756501>cwd</code> 路径是否正确</li><li data-v-43756501>Windows 下确认 Python 已加入 PATH</li></ul><h4 data-v-43756501>5.2 接口返回错误</h4><ul data-v-43756501><li data-v-43756501>查看控制台&quot;上游状态&quot;标签页, 确认对应上游是否在线</li><li data-v-43756501>查看返回的 <code data-v-43756501>last_error</code> 字段</li><li data-v-43756501>对应上游若被禁用, 需修改 <code data-v-43756501>config/upstreams.yaml</code> 并重启</li></ul><h4 data-v-43756501>5.3 参数错误</h4><ul data-v-43756501><li data-v-43756501>股票代码由网关自动标准化 (<code data-v-43756501>000001</code>、<code data-v-43756501>sh600519</code>、<code data-v-43756501>600519.SH</code>、<code data-v-43756501>sz000001</code> 都能识别)</li><li data-v-43756501>日期统一使用 <code data-v-43756501>YYYYMMDD</code> (如 <code data-v-43756501>20260301</code>)</li><li data-v-43756501>具体参数以 <strong data-v-43756501>&quot;MCP APIs&quot;</strong> 标签页每个 tool 行的&quot;详情&quot;展开为准</li></ul><h4 data-v-43756501>5.4 反馈渠道</h4><p data-v-43756501>遇到问题请提交 Issue 到 <a href="https://github.com/unihive/unihive" target="_blank" rel="noopener" data-v-43756501>github.com/unihive/unihive</a>, 并附上控制台截图与接口返回内容。点击 <strong data-v-43756501>&quot;MCP APIs&quot;</strong> → 对应 tool 行 → 详情按钮即可拿到完整 tool 名与参数。</p>',8))],8,lt)])}}}),_t=I(vt,[["__scopeId","data-v-43756501"]]),mt={class:"ai-onboarding"},ft={class:"ai-section"},gt={class:"ai-section"},ht={class:"ai-section"},Pt=`## 金融数据工具

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
概念板块: 886042.TI`,yt=`{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}`,Tt=b({__name:"AiOnboardingPanel",setup(P){const n=A();function p(){n.push({name:"interfaces"})}return(a,s)=>{const f=C("el-button");return _(),h("article",mt,[t("section",ft,[s[0]||(s[0]=t("h3",null,"AI Agent 系统提示词",-1)),s[1]||(s[1]=t("p",null,"复制以下提示词给你的 AI Agent, 让它能够接入 UniHive MCP:",-1)),l(u,{code:Pt})]),t("section",gt,[s[2]||(s[2]=t("h3",null,"配置 MCP Server",-1)),s[3]||(s[3]=t("p",null,"在你的 Agent 配置文件中添加:",-1)),l(u,{code:yt})]),t("section",ht,[s[5]||(s[5]=t("h3",null,"工具清单",-1)),s[6]||(s[6]=t("p",null,[e("完整工具列表 (含参数说明) 请查看 "),t("strong",null,'"MCP APIs"'),e(" 标签页。")],-1)),l(f,{type:"primary",onClick:p},{default:m(()=>[...s[4]||(s[4]=[e("→ MCP APIs",-1)])]),_:1})])])}}}),bt=I(Tt,[["__scopeId","data-v-729a6702"]]),Ct={class:"cfg-subtabs",role:"tablist","aria-label":"指南子标签"},It=["aria-selected","onClick"],St={class:"guides-panel-stack"},wt={class:"cfg-panel","aria-label":"人工接入指南"},At={class:"cfg-panel","aria-label":"AI 接入指南"},Mt=b({__name:"GuidesView",setup(P){const n=V(),p=A(),a=y("human");a.value=H(n.query.tab,w,"human"),L(()=>n.query.tab,r=>{a.value=H(r,w,"human")});function s(r){a.value!==r&&(a.value=r,p.replace({query:{...n.query,tab:r}}))}const f=U(()=>w.map(r=>({key:r,title:j[r]})));return(r,d)=>{const S=C("el-card");return _(),Y(S,{shadow:"never",class:"panel-card"},{header:m(()=>[...d[0]||(d[0]=[t("div",{class:"card-toolbar"},[t("h2",{class:"card-title"},"接入指南")],-1)])]),default:m(()=>[t("nav",Ct,[(_(!0),h($,null,q(f.value,i=>(_(),h("button",{key:i.key,type:"button",class:x(["cfg-subtab",{active:a.value===i.key}]),role:"tab","aria-selected":a.value===i.key,onClick:o=>s(i.key)},v(i.title),11,It))),128))]),t("div",St,[k(t("section",wt,[l(_t)],512),[[O,a.value==="human"]]),k(t("section",At,[l(bt)],512),[[O,a.value==="ai"]])])]),_:1})}}}),$t=I(Mt,[["__scopeId","data-v-35b4455f"]]);export{$t as default};
