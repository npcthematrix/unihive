# 控制台 Vue3 迁移 — 阶段 6 GuidesView + 旧 console 拆除 handoff

**日期**: 2026-09-14
**状态**: 阶段 1-5 已提交, 阶段 6 调研中段因上下文耗尽暂停
**下一步**: 直接从阶段 6 GuidesView 实现开始, 然后旧 console 拆除, 最后 pytest 改写 + code-reviewer (累积阶段 4-6)

## 总体进度

| 阶段 | 提交 | 内容 |
|---|---|---|
| 1 | `3a6e989` + `e4e7b48` | 后端 JSON 鉴权端点 + web/ 脚手架 + 登录闭环 |
| 2 | `6e20c1b` | StatusView 状态页 (755 行, code-reviewer APPROVE) |
| 3 | `8dbd725` | InterfacesView 接口页 (8 文件, code-reviewer WARNING 全修) |
| 4 | `92a7ada` | ConfigView 配置页 (8 文件, 含 proxy PUT) |
| 5 | `189b8d8` | OmniView 板块管理 (6 文件, AbortController 中止同步) |
| 5.5 | `514a03b` | **fix(omni): query_sectors 缩进回归修复** — 后端函数体移出 if keyword: + 前端 `?.` 兜底 |
| 6 | — | **本 handoff 范围**, 尚未开始 |

## 阶段 6 调研发现

### 旧 console 指南结构 (2 tab 已合并为单路由)
- **人工接入指南** (`#onboarding`, console.html:267-441, ~175 行): 账号准备、配置文件、启动验证、客户端示例 (curl / Python)、故障排查
- **AI接入指南** (`#ai-onboarding`, console.html:443-?, ~120 行): MCP 端点说明、JSON-RPC 例子、Claude Desktop / Cursor 配置、stdio 模式
- 旧路由表 2 个独立 panel, 新 router (`web/src/router/index.ts:42`) 已合并为单 `guides` 路由 + 内部 2 subtab
- 旧 console.js: `wireOnboarding()` (line 1267+) — **唯一动态部分**: 「跑代理测试」按钮 (POST /api/auth/test-mcp-conn), 其它都是静态 HTML

### 当前 GuidesView.vue 现状
- stub (5 行), 阶段 1 占位, 标「迁移阶段 6」

### finalize-build.mjs 流程 (阶段 1 验证)
- `cd web && npm run build` → vue-tsc + vite build → 输出 `dist/`
- `finalize-build.mjs`: 把 dist 嵌入 `console.html` 的 SPA shell, 部署到 `static/console/` 后由网关同源托管
- 阶段 6 切换 = 跑一次 `npm run build` + 部署 finalize-build 输出

### pytest 改写清单 (调研中, 需新会话先 grep 全量)
已识别 5 个相关测试:
| 文件 | 类型 | 阶段 6 行动 |
|---|---|---|
| `tests/test_console_html_unused.py` | 断言 console.html 元素与 console.js 函数对应 | **改写**: 改为断言 web/src/views/* 完整, 旧 console.html 可删 |
| `tests/test_console_auth.py` | 后端 console_auth API 测试 | **保留** (与前端无关) |
| `tests/test_console_auth_env.py` | 后端 auth 环境变量测试 | **保留** |
| `tests/test_console_api_config_fixes.py` | 后端 config API 测试 | **保留** |
| `tests/test_console_interfaces.py` | 接口页相关, 名字暗示有 console.html 耦合 | **查**: 打开文件确认, 若是 UI 测试就改 Vue e2e, 若是 API 测试就保留 |

**风险点**: handoff 提到「约 8 个 HTML 耦合 pytest」, 实际数可能 5-12 个, 需要在续接第一步 grep 全量:
```bash
grep -rln "console.html\|console\.js\|/static/console" tests/ 2>/dev/null
```

## 阶段 6 实施计划 (建议拆 4 子阶段)

### 6-A: GuidesView 实现
1. **views/guides/OnboardingPanel.vue** — 人工接入指南, 大段静态说明 + 1 个「跑代理测试」按钮 + 测试结果展示
2. **views/guides/AiOnboardingPanel.vue** — AI接入指南, 纯静态 (JSON-RPC 例子 + 客户端配置片段), 用 `<pre><code>` 展示
3. **views/GuidesView.vue** — 2 subtab 容器 + ?tab= hash 深链 (复用阶段 3-5 经验: `parseTabFromQuery` + `selectTab` 只替目标 slot, 保留其他 query)
4. **api/guides.ts** — `testMcpConnection()` (POST /api/auth/test-mcp-conn) — 等等, 需先 grep 确认端点是否真存在

### 6-B: npm run build 正式替换 + 旧 console 拆除
1. `cd web && npm run build` 跑通 (5 阶段已确认 build 链 clean)
2. `node scripts/finalize-build.mjs` 生成新 console.html
3. 备份旧 `console.html` + `static/console/console.js` + `static/console/console.css` 到 `archive/console-pre-vue-2026-09-14/`
4. 部署新 console.html
5. 浏览器验收: 5 个路由全部工作 + 旧 URL 重定向兼容 (router fallback 已配)
6. 启动 gateway 跑回归 smoke (登录 → 切 5 tab → proxy PUT → omni 同步 → 关闭)

### 6-C: pytest 改写
- 跑 `grep -rln "console.html\|/static/console\|console\.js" tests/` 全量清单
- 改写或删除 console.html 耦合断言
- 旧 console.js 引用如果还有, 改为对应 Vue 视图或后端 API 测试
- 跑 `pytest tests/ -x` 全绿

### 6-D: 累积 code-reviewer
- 阶段 4 (ConfigView 8 文件) + 5 (OmniView 6 文件) + 6 (GuidesView + 拆除) 三批代码一起 review
- HIGH/MED 必修, LOW 可 deferred
- 修完出最终提交

## 关键业务约束

- **静态内容**占 GuidesView 90%, 大段复制旧 console.html 即可, 不要造新文档
- **动效**: 「跑代理测试」按钮需 `<el-button>` + `<el-alert>` 展示结果 (success/warning/error), loading 态用 `:loading`
- **CSP**: 不用 inline onclick, 全部走 Vue 事件
- **代码块**: 用 `<pre><code>` 配 `<el-tag>` 显示语言, 不要用 markdown 渲染库 (简单胜过引入依赖)
- **深链**: `?tab=human|ai` 两键, 复用 ConfigView/OmniView 已建立的 hash query 模式
- **i18n**: 阶段 6 不引入, 中文为主, 客户端配置示例用英文 (Claude Desktop 等)

## 关键类型/接口 (阶段 3-5 已确认, 阶段 6 复用)

- `escapeRegExp` / `copyText` / `<HighlightText>` (无 highlight 需求可不引入)
- Pinia auth store + axios 401 拦截器已在 `api/client.ts`
- `errorMessage()` 统一后端错误提取
- 阶段 5 已建立的 AbortController + 单飞模式可复用 (如果 test-mcp-conn 有超时风险)

## 当前 dev 环境

- 网关: **新进程** `br6khmqqo` (PID 15728) 在 :18080, 已加载 514a03b 修复后的 omni_client.py
- Vite dev: `npm run dev` 后台任务 `bal0l4d8d` 在 :5173 (HMR 推送中)
- 浏览器: 5 阶段验收后应可正常使用 (用户报「没有数据」问题应在 gateway 重启 + 硬刷新后解决)

## 阶段 5 关键经验 (避免重复踩坑)

- **Python 不热重载**: 后端 .py 改动必须重启 gateway 才生效, HMR 只对前端生效
- **template 属性内中文双引号**必须 `&quot;` 转义 (Vue 模板解析报 U+0022)
- **template 属性值最长错误**: 在 template attr 内含原始 `"` 会让整个组件编译失败
- **query 类函数**至少跑 4 入参组合验证 (全空/单 keyword/单 source/两者都有)
- **omni 旧缩进 bug**: query_sectors 整个函数体原本误缩进在 `if keyword:` 内 → 返 None → 前端崩。已修, 留兜底 `?.`
- **AbortController 中止**: query + sync 两条路径都要在切换/卸载时取消 in-flight, 避免响应回写到已销毁组件
- **el-select `value=""` + `clearable` 同时使用**: 看起来正常但展示和 v-model 行为容易混淆, 阶段 6 可考虑用 `<el-option :value="null">` 替代

## 续接第一步

```bash
# 1. 确认环境
curl -s -o /dev/null -w 'vite=%{http_code}\n' http://localhost:5173/
curl -s -o /dev/null -w 'gateway=%{http_code}\n' http://127.0.0.1:18080/api/auth/me

# 2. 旧 console.html Guides 段落完整读
sed -n '267,620p' console.html

# 3. 旧 console.js wireOnboarding 动态部分
sed -n '1267,1320p' static/console/console.js

# 4. pytest 全量耦合清单
grep -rln "console.html\|/static/console\|console\.js" tests/

# 5. 确认 test-mcp-conn 端点是否存在
grep -rn "test.mcp.conn\|test_mcp_conn" src/unihive/ static/ console.html 2>/dev/null
```

完成阶段 6 后: vue-tsc + npm run build (含 finalize) + pytest + code-reviewer (4+5+6 累积批) + 删旧 console.html/js/css
