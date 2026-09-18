# 控制台第四轮审视修复 + 上游热开关 — 未提交交接 (2026-09-18)

> 用户请求: "请审视管理控制台的功能逻辑、排版布局, 并赋予一个缺省的应用图标"。
> 并行双审查 (功能逻辑 / 排版布局) 的 H/M 项 + 廉价 L 项落盘后, 同日把全部 deferred LOW (前端 L4-L11 + 后端 3 项) 也清完, **三轮验证通过**, 尚未 commit (用户未明确要求提交)。
> 验证止步于 vitest + `npm run build` + pytest, **未开浏览器** (遵既定约束)。

## 验证结果 (全绿)

| 关卡 | 命令 | 结果 |
|---|---|---|
| 前端单测 | `cd web && npx vitest run` | **11 files / 222 tests passed** (上轮基线 209; +client 取消/重试用例、+fuyao_endpoint 5、row-key 用例重写) |
| 前端构建 | `cd web && npm run build` | vue-tsc + vite + finalize 全过; console.html / static/console/assets / favicon.svg 已重新生成 |
| 后端单测 | `.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_get_daily_l1_observability.py --ignore=tests/test_mootdx2_p0_perf.py --ignore=tests/test_fuyao_cache_config.py -q` | **606 passed, 1 skipped**; `test_omni_sync_lock.py` (3) + `test_upstream_enabled_toggle.py` (27, 含 3 个自愈/校验用例) |

> 当日续做: 第五节原列的 deferred LOW **已全部清掉** (前端 L4-L11 + 后端 3 项), 见文末「六、LOW 收尾 (同日)」。

3 个 ignore 文件为既有损坏 (与本专题无关)。

## 一、功能逻辑修复

### F-H1 (HIGH) 取消的请求被误判网络错误并重试, 三处取消守卫全部失效
- 根因: axios 1.20 的 `CanceledError` 带 xhr `request` 字段 → 落入"网络错误"重试分支; 用已 abort 的 signal 重试瞬时再败, 最终 reject 普通 `Error('retry exhausted')`, 调用方 `name === 'CanceledError'` 守卫全部错过 → 幻影"网关连不上" banner / 同步假失败 / 弹窗错误复活。旧 `__isCanceled` 标志全局无任何赋值点。
- `web/src/api/client.ts`: 新增导出 `isCanceledError(error)` (`axios.isCancel` 优先, 结构兜底 `code==='ERR_CANCELED'`/`name==='CanceledError'`, 不依赖 `instanceof` 以兼容测试 mock); `shouldRetry` 前置两道: ① 取消不重试 ② **仅 GET/HEAD 幂等方法可重试** (POST/PUT/PATCH 重试会导致同步重跑、yaml 重复写、乐观锁端点必 409)。
- 调用点统一改用 `isCanceledError`: `OmniView.vue`、`omni/QueryPanel.vue`、`omni/SyncPanel.vue`。
- 陈旧请求归属守卫: OmniView `finally` 只在 `queryAbort === ac` 时清 loading; QueryPanel 弹窗请求用 `reqHandle` 身份比较, success/catch/finally 三处均守卫。
- 测试: `web/tests/api/client.spec.ts` 重写扩充 (ERR_CANCELED 带 request、isCancel sentinel、4 个非幂等方法不重试、HEAD 可重试、method 缺省按 GET)。

### F-H2 (HIGH) fuyaoEndpointOf 是假实现
- `web/src/api/config.ts`: 按 `upstream_tool_mapping[tool]` 的 backer 真实解析端点, 固定优先级 `fuyao_ashare → fuyao_index → fuyao_meta → fuyao_fund` (多 backer 链确定性取值)。
- 新测试 `web/tests/config/fuyao_endpoint.spec.ts` (5 例)。

### F-M2 FuyaoCachePanel 深度 watch 静默拍回用户草稿
- 改为只 watch 服务端 `fuyaoCache.version` 变化才 hydrate; 父面板整表替换引用 / 切 tab 重挂只影响派生分组, 不动 working 草稿。

### F-M3 InterfacesView 筛选/刷新后展开行丢失
- 移除 el-table 上的 `:key="activeGroup"` (强制 remount 丢展开态); 展开裁剪改为对**全量 tools 宇宙**判存在而非当前可见行; `load()` 后清空 `expandedNames` (行对象全新, 顺手修"收起"按钮文案陈旧)。依赖 filter 保留行对象引用、el-table 按身份跟踪展开。

### F-M4 / F-L 杂项
- `McpBrowserView.vue` + `mcp-browser/tools.ts`: 删除从未被设置的 `errorKind` 死代码与模板 span/CSS; `rowKeyOf` 匿名行改用 WeakMap 按对象身份发稳定 id (旧实现依赖展开回调第二参 index, 实际只收到 row); 展开回调签名纠正; 选中上游后 refresh 只重拉工具不回上游列表。
- `UpstreamsPanel.vue`: 消费热开关 **500 契约** — yaml 已持久化但运行时切换失败时, 响应体携带新 `version`, 前端吃下 version 并 warning "重启网关后生效", 避免下次点击必撞 409。
- 文案: Onboarding "约 160 → 约 290 个 MCP 接口 (5 套上游, 精确数量以运行时列表为准)"; AI onboarding `~200 → ~290`; 故障排查改为指向配置页上游 tab 热开关。

### 后端: OMNI 全量同步 in-flight 互斥
- `console_api.py`: 模块级 `_omni_sync_lock = asyncio.Lock()`, `run_omni_sync` 用 `.locked()` 快速失败 ("已有同步任务在执行…", 不排队); 全量同步最长约 300s 且写同一个 board.db。source 白名单校验在取锁之前。
- `web/src/api/omni.ts`: 同步 POST 显式 `timeout: 300_000` 覆盖全局 15s; POST 本身不重试。
- 新测试 `tests/test_omni_sync_lock.py` (3 例: 并发第二次快速失败/完成后可再同步/非法 source 不取锁)。

## 二、排版布局修复

- **L-H1 token 泄漏 (一处兜底替代 11 处散修)**: `web/src/styles/main.css` `:root` 增加兼容别名 `--bg/--border/--card-bg/--text-secondary → --cfg-*`。未声明的自定义属性会让整条 CSS 声明作废 (chain-pill 透明无边框、card 透明等)。
- 对比度 (WCAG AA): `--cfg-text-secondary` 由 `#909399` (~2.9:1) 改为 `var(--el-text-color-regular, #606266)` (~5.9:1); mini-hint / cache-off-hint / toggle-count 同步; 硬编码危险色 `#b91c1c`/rgba 底色改为 EP 语义变量 (`danger-dark-2`/`cfg-bg-soft`/`fill-color-light` 等): QueryPanel、ToolsPanel、CachePanel、SyncPanel、L1CacheView、StatusView。
- **L-H2 StatsPanel 移动端**: 补 `@media (max-width:640px)` 与 SyncPanel 一致 (日志行 flex-wrap)。
- **M1** 上游状态列 `width=120 → min-width=140` (loading spinner 裁切); **M2** ProxyPanel 开关去掉 `inline-prompt`, active/inactive 文案移到轨道外不被裁切; **M3** onboarding-nav `top:0` 并删除无根的 `--app-header-height` (topbar 在正常流中会滚走, 旧值留 52px 穿帮缝)。
- **M6/M7** QueryPanel: 弹窗宽 `min(600px, calc(100vw - 32px))`; 分页精简为 `prev,pager,next` + background。
- **M8 登录页统一**: `static/console/login.css` 全量重写对齐 SPA LoginView (深色渐变页 + 380px 白卡 + #409eff + 品牌图标); `console_auth.py` LOGIN_HTML 加 favicon link 与卡片内 40px brand-icon (CSP img-src 已允许 'self' data:)。
- **M10** McpBrowser / Interfaces 侧栏 `max-height: calc(100vh - …); overflow-y:auto; overscroll-behavior:contain`。
- **L3** `.cfg-toggles-row` 此前无任何样式 (开关挤在一起), 已补 flex-wrap。

## 三、缺省应用图标

- `web/public/favicon.svg`: 蓝色渐变方块 + 六边形 + 3 根 K 线 (新建目录)。
- `web/index.html` 替换空白 `data:,`; `web/scripts/finalize-build.mjs` 增加 favicon 拷贝; 构建产物 `static/console/favicon.svg` 已生成; 服务端直出 /login 同步引用。

## 四、同工作树内另一专题: 上游启用/禁用热开关 (本工作流早些时候已落盘+实测)

与第四轮修复**同处未提交工作树**, 提交时注意区分。范围:
- 后端: `gateway_server.py` (`set_upstream_enabled` + client 构造/启动抽方法 + `_upstream_toggle_lock`)、`core/tool_loader.py` (`backing_upstreams`/`enabled_upstream_names`/`filter_specs_by_enabled`)、`core/registry.py` (抽 `register_tool`)、`utils/console_api.py` (`PUT /api/config/upstreams/<name>/enabled`, 乐观锁 + yaml 定向文本改写保对齐)。
- 前端: `config/UpstreamsPanel.vue` (状态标签→按钮+确认弹窗+行级 loading)、`ConfigView.vue` (changed 本地 patch)、`api/config.ts` `setUpstreamEnabled`、纯函数 `config/upstreams.ts`。
- 测试: `tests/test_upstream_enabled_toggle.py` (24 例)、`web/tests/config/upstreams.spec.ts`。
- 计划原文: `C:\Users\admin\.claude\plans\valiant-greeting-wind.md`; 隔离实例 :18099 API 层实测已完成 (87 工具差集即时增删 / connected / 409 / 多源 search_stock 不受影响 / yaml 落盘)。一次性验证脚本本轮已删除 (`logs/_verify_toggle.py`、`logs/_make_verify_config.py`)。

## 五、LOW 收尾 (同日续做, 全部落盘)

**前端排版 (L4-L11)**:
- **L4 触达目标 ≥24px** (WCAG 2.5.8): `.mcp-tool-item` min-height 24 + flex 居中; `.err-btn` padding 4px / min 24px; `.cfg-search-clear` 20→24px, 搜索框右内边距 28→32px 防文字钻入。
- **L5 CodeBlock 复制按钮重叠**: 去掉绝对定位浮层, 改为独立工具条 `.code-block-bar` (短片段/横向滚动都不再压代码)。
- **L6 FuyaoCachePanel**: 删掉死媒体查询 (`.fcp-tool-row` 模板中不存在) + 无绑定的 `.fcp-preset.active`; el-table 去掉 `scrollbar-always-on`; "清空全部" 静止态即 danger 配色 + 专属 danger focus ring。
- **L7** CachePanel TTL 秒列补 `.num` 类 (原 `:deep(.num)` 选择器从未命中), 人类可读列 `.ttl-human` 一并 tabular-nums。
- **L8** StatusView 状态徽章删掉冗余 `.status-dot` (字形已带形状+颜色; 且 dot 不识别 `configured` 态, 蓝徽章配灰点是错的), `dotClass` 死函数一并删; `.status-dot` 样式保留 (缓存卡仍用)。
- **L9** `.cfg-search:focus` 在边框色之外补 2px primary-light-8 外环。
- **L10** DefaultLayout: 断点折叠本身早已 ship (审计项陈旧), 但旧 route watcher 会在窄屏导航后**自动展开**到 220px 把内容挤到 ~150px — 已删 watcher, 窄屏收敛只由挂载/resize 负责, 手动展开不被导航打断。
- **L11** `.page-title` ellipsis: topbar-left `min-width:0`、topbar-right `flex-shrink:0`、collapse-btn 不收缩。

**后端热开关 (3 项)**:
- **部分失败自愈**: `set_upstream_enabled` 拆为编排器 + `_enable/_disable/_reconcile_upstream_locked` + `_toggle_tool_diff_locked` (差集按 backer 交集限定单上游范围) + `_publish/_unpublish_tools_locked` (单工具失败收集错误继续整批, KeyError 幂等) + attach/detach client helper。同态重试 (cfg 已翻转) 不再被 no-op 短路, 走 reconcile 补发/补摘/补连; 禁用时摘不净则保留 client, 残存工具仍可服务。新增 3 个测试 (部分启用失败重试自愈 / 部分禁用失败保活+重试断连 / body 校验)。
- **阻塞 IO 移线程池**: PUT handler 内 `save_upstream_enabled` 经 `asyncio.to_thread` 执行 (yaml 读/解析/写不卡事件循环)。
- **函数体积**: 111 行单方法拆成 ≤40 行的聚焦步骤; HTTP 层抽 `_validate_toggle_enabled_body`, 500 响应文案注明原样重试可自愈。

## 六、提交边界提醒

- `src/unihive/api/mootdx2_client.py` 是**用户自己的未提交改动**, 本专题未触碰, 提交时勿带入。
- `web/coverage/`、`.verify-screenshots/` 为本地产物, 建议加 .gitignore 而非提交。
- `logs/` 下仍有大量历史 `_*` 调试抓包文件 (本专题之前就存在), 未清理。
- 建议拆两个 commit: ① feat(gateway/console) 上游热开关 ② fix(console) 第四轮审视修复 + favicon。
