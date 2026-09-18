# 控制台整体优化 — 专题 handoff (1 HIGH + 4 MED + 4 LOW)

**日期**: 2026-09-14
**专题**: 控制台整体优化 (Vue3 迁移 6 阶段后专题)
**结论**: 控制台 Vue 迁移 6/6 已 ship (3d53d2b), 整体审视识别 9 项改进, 已落 TaskList, 按优先级分批提交
**关联 commit**: 阶段 6 final 提交 `3d53d2b` 之后的所有优化 PR

## 背景

控制台从原生 JS 迁移到 Vue 3.5 + Element Plus 2.9 + Vite 6 已完成 (c5f9d71..3d53d2b 共 8 commit), 19/19 控制台测试 + 全套 552+1 skipped 全绿。 用户 2026-09-14 要求"整体审视管理控制台的功能逻辑、排版布局", 输出了 9 项改进清单。

**Why**: 迁移阶段集中火力在"功能等价 + 视觉对齐", 未触及工程化提升 (测试覆盖 / 响应式 / 韧性 / a11y)。 本专题把这些改进集中记账。

**How to apply**:
- 按 HIGH → MED → LOW 顺序分批提交, 每批独立 PR
- HIGH 第一批单独提交, MED 4 项可合并 PR, LOW 4 项可拆批提交
- 验收标准: vue-tsc clean + vite build clean + pytest 552+1 skipped 不退化 + 新测试 100% pass

## 现状 (已确认)

| 项 | 状态 |
|---|---|
| 路由 | 6 路由全活, hash 深链 `?tab=` 互通 |
| 鉴权 | cookie session, axios withCredentials, 401 拦截跳 /login?redirect= |
| 状态 | StatusView 6 卡片 + 表格 + cache, 无单测 |
| 接口 | InterfacesView 5 模块 + 755 行, tools.ts 纯函数已抽 |
| 配置 | ConfigView 5 panel, 含 proxy PUT, 无单测 |
| OMNI | OmniView 3 panel, AbortController 中止, 无单测 |
| 接入指南 | GuidesView 2 subtab, IntersectionObserver scroll-spy |
| 构建 | vite build ~8.3s, 总产物 ~370 kB gzip (含 element-plus 1088 kB 全量) |
| 测试 | pytest 552 passed + 1 skipped, web 端 0 测试 |

## 9 项改进清单

### HIGH (1 项)

#### H1 · web 端 vitest + 核心纯函数单测
- **文件**: `web/vitest.config.ts` (新增), `web/tests/**` (新增), `web/package.json` (改 scripts + 加 devDeps)
- **理由**: 现有纯函数 (interfaces/tools.ts / utils/clipboard.ts / api/client.ts 401 拦截) 全无单测, 重构时无安全网, 集成测试兜不到逻辑分支
- **起步范围**:
  - `web/tests/views/interfaces/tools.spec.ts` — `applyToolsFilter / bucketByGroup / firstGroupWithMatch / hasCache / hasNoParams / isParamPlaceholder` (估 15-20 case)
  - `web/tests/utils/clipboard.spec.ts` — 成功路径 + `navigator.clipboard` 缺失 fallback + 异常分支 (估 4-6 case)
  - `web/tests/api/client.spec.ts` — mock axios: 401 → 拦截 → 跳 /login; 4xx/5xx 不跳; timeout 重试 (估 6-8 case, 含 H5 重试)
- **目标**: web 端首测覆盖率 ≥ 60% (起步), 后续可追
- **依赖**: `vitest@^2`, `@vitest/coverage-v8`, `happy-dom` (DOM 环境), `msw` 可选
- **估时**: 2-3h (含 npm install + 配置)

### MED (4 项)

#### M1 · DefaultLayout 侧栏响应式折叠
- **文件**: `web/src/layouts/DefaultLayout.vue`
- **问题**: `<el-aside width="220px">` 固定, < 768px 挤压主区, 移动端基本不可用
- **方案**:
  - 加 `window.innerWidth` 监听 (onMounted + resize), < 768 默认折叠 (`isCollapsed = true`)
  - el-aside 改 `:width="isCollapsed ? 64 : 220"`, el-menu 加 `:collapse="isCollapsed"`, el-menu-item `<span>` 加 `:title` (tooltip)
  - topbar 加汉堡按钮 (Fold/Expand 图标) 触发折叠切换
  - 路由切换后自动展开 (避免遮挡内容)
- **CSS 调整**: `.brand-name` 在折叠态隐藏
- **估时**: 1.5h

#### M2 · SyncPanel boardType 补 style / region
- **文件**: `web/src/views/omni/SyncPanel.vue`, `web/src/views/omni/format.ts`, `web/src/api/omni.ts`
- **问题**: UI el-select boardType 只暴露 `industry / concept`, 后端 API 接受 4 种 (industry/concept/style/region), 用户无法触发样式/地域板块同步
- **方案**:
  - `format.ts` `BOARD_TYPES` 加 `style: '风格板块'` / `region: '地域板块'` (与 `src.unihive.sync.board_sync` 内部定义对齐)
  - SyncPanel 同步卡片 el-select 直接绑定 `BOARD_TYPES`
  - 同步调用 `syncOmniBoard(boardType: BoardType)` 类型已支持, 无需改 API
- **估时**: 30min

#### M3 · App.vue 全局 ErrorBoundary
- **文件**: `web/src/components/ErrorBoundary.vue` (新增), `web/src/App.vue` (改)
- **问题**: 任何视图 render 抛错导致白屏, 用户只能手动刷页
- **方案**:
  - ErrorBoundary.vue 用 `<script setup>` + `onErrorCaptured((err) => { hasError = true; return false })` (阻止继续向上传播)
  - fallback 模板: 居中卡片 + "页面渲染失败" + 错误类型 (只显示类型, 不泄露堆栈) + "重试" (reload 视图) + "回首页" (router.replace('/status'))
  - App.vue `<router-view />` 包 ErrorBoundary
  - 错误时 onErrorCaptured 上报 console.error (后续可接 Sentry)
- **估时**: 1h

#### M4 · axios 5xx/timeout 指数退避重试
- **文件**: `web/src/api/client.ts`
- **问题**: 网关偶尔 502/503 (上游重连) 或 timeout, 用户需手动刷新
- **方案**:
  - 在 response interceptor 加 `retryCount` 状态 (不引入新库), 仅对 `5xx / network error / ECONNABORTED` 重试
  - 401/4xx 不重试 (避免重复登录或重复提交)
  - 退避: `300ms * 2^retry`, 最大 1 次重试 (再失败就交给 errorMessage)
  - 关键: retryCount 必须挂 axios config 上 (不挂全局), 避免并发请求互相干扰
- **估时**: 1h (含 5 单测)

### LOW (4 项)

#### L1 · StatusView auto-refresh 跳过 hidden 页
- **文件**: `web/src/views/StatusView.vue`
- **问题**: `setInterval(load, 30s)` 不暂停 `document.hidden`, 切到其他 tab 仍轮询
- **方案**: load() 开头加 `if (document.hidden) return`, 监听 `visibilitychange` 事件触发一次
- **估时**: 30min

#### L2 · Logout 二次确认
- **文件**: `web/src/layouts/DefaultLayout.vue`
- **问题**: 退出按钮无确认, 误点丢会话
- **方案**: `@click="onLogout"` → `@click="confirmLogout"`, `confirmLogout` 调 `ElMessageBox.confirm('确认退出?', '提示', { confirmButtonText: '退出', cancelButtonText: '取消', type: 'warning' })`, 确认才走 onLogout
- **估时**: 15min

#### L3 · a11y skip-link + 状态色加图标
- **文件**: `web/src/layouts/DefaultLayout.vue`, `web/src/views/StatusView.vue`
- **问题**:
  - 键盘用户 Tab 进站点, 第一下被侧栏卡住, 必须 Tab 多次
  - 状态色 (success/warning/danger) 色盲用户难区分
- **方案**:
  - DefaultLayout `<el-container>` 上方加 `<a href="#main-content" class="skip-link">跳到主内容</a>`, CSS `position: absolute; left: -9999px;` 平常隐藏, `:focus` 回到屏幕内
  - `<el-main>` 加 `id="main-content"`, `<a class="skip-link" :tabindex="-1">` 锚点 (避免 outline)
  - StatusView 表格 status-badge 在 success/danger/warning 前加 ✓ / ✗ / ⚠ unicode 前缀, 同时 `aria-label="正常"` 等
- **估时**: 30min

#### L4 · Element Plus 按需引入 + splitChunks
- **文件**: `web/vite.config.ts` (改), `web/src/main.ts` (改)
- **问题**: `element-plus` 1088 kB 全量注册是首屏最大负担, 实际只用到 ~30 个组件
- **方案**:
  - 用 `unplugin-vue-components` + `unplugin-auto-import` 自动按需 (不需要手动 import, vite 编译期 tree-shake)
  - vite build 加 `build.rollupOptions.output.manualChunks`: 拆 `element-plus` 到独立 chunk, `vendor` chunk 放 axios / vue-router / pinia
  - 目标: element-plus gzip 从 ~340 kB 降到 ~120 kB (-65%)
- **注意**: 单 PR, 不阻塞 M/H 项, 排在最后
- **估时**: 2h (含 build 配置调优 + 验证)

## 验收标准 (每批提交)

每批 commit 必须满足:

```text
✅ vue-tsc --noEmit  0 error
✅ npm run build  clean (build 时间 ±10% 内)
✅ pytest tests/  552 passed + 1 skipped (不引入回归)
✅ 新测试 100% pass (HIGH 批引入 web 端测试)
✅ console.html production assets 引用正确 (finalize-build.mjs)
✅ /api/* + /mcp/ 端点契约不变
```

## 执行顺序 (建议)

```text
第一批 (HIGH):  H1 单测基建
  └ 顺带把 M4 重试测试也写了 (共用 client.spec.ts)
第二批 (MED):  M1 侧栏折叠 + M2 SyncPanel 补全 + M3 ErrorBoundary
  └ MED 4 项合并 PR, M2 仅 30min 顺带做
第三批 (LOW):  L1 hidden 跳过 + L2 退出确认 + L3 a11y (3 项合并 PR)
第四批 (LOW):  L4 Element Plus 按需 (单独 PR, 风险隔离)
```

## 风险与回退

- **H1 风险**: vitest 配置与 vite 配置冲突 (resolve alias 等) → 沿用 vite.config.ts 的 defineConfig, 不独立写
- **M1 风险**: el-menu collapse 切换时动画卡顿 → 用 `transition: width 0.2s ease` 平滑
- **M3 风险**: ErrorBoundary 捕获到自身错误 → 内部 try/catch, 永不递归
- **M4 风险**: 并发请求重试放大后端压力 → 仅 1 次, 且只对 transient 错误
- **L4 风险**: 按需引入漏组件导致运行时未注册 → 跑全功能 smoke (走 6 路由)

## 关联记忆

- [[project-console-vue-migration]] — 阶段 1-6 迁移上下文
- [[feedback-no-playwright-default]] — 单测路线不引 Playwright
- [[feedback-no-sse-no-mcp-auth-console-auth]] — 鉴权 / SSE 边界
- 文档路径: `docs/handoffs/2026-09-14-console-overall-optimization.md`

## 续接

- 当前 TaskList 已建 9 项 (按本清单一一对应)
- 新会话恢复时, 先看 TaskList 哪批已提交 (git log), 接着跑对应批次
- 所有项完成后, 出最后一个 PR summary, 把"控制台整体优化"专题关闭
