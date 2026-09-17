# 控制台第三轮审视 — 专题 handoff (6 HIGH + 9 MED + 4 LOW)

**日期**: 2026-09-17
**专题**: 控制台第三轮全量审视（前两轮 9+21 = 30 项 ship 后）
**结论**: 3 并行审查 agent（功能逻辑 / 排版布局 / 代码质量）原始发现 39 项，去重 + 排除已 ship + 排除前两轮已识别项 → 19 项待 ship（H/M/L 三档）

## 背景

`docs/handoffs/2026-09-14-console-overall-optimization.md` 9 项 + `docs/handoffs/2026-09-15-console-second-pass-audit.md` 21 项均已 ship。本专题是 ship 后的第三轮审视，重点：

- **ThsLocalView 新增未提交**（web/src/views/ThsLocalView.vue + web/src/api/ths-local.ts + 后端 src/unihive/api/ths_local_gateway_client.py + src/unihive/api/index_registry.py + 构建产物 static/console/assets/ThsLocalView-*），3 个 agent 都把它当成审计对象
- App.vue `<router-view />` 未被 ErrorBoundary 包裹 — **整体优化 M3 ship claim 落空**（ErrorBoundary.vue 存在但未 wire）
- 二轮 ship 的 21 项之外，剩余 LOW（L4/L5/L6/L8）仍 deferred
- 二次审视审计的 same-file 类问题在 L1CacheView/OmniView 上有 sibling 漏掉

**Why**: 主题明确"功能逻辑 + 排版布局"全量扫描；前两轮分别聚焦测试基建 + CSS 响应式，本轮覆盖功能/视觉/代码质量三轴全维度。

**How to apply**: 3 批提交，与前两轮节奏一致
- 批次 1 (HIGH): 6 项；含 1 项 ship 落空修复（H3）+ 5 项新发现
- 批次 2 (MED): 9 项；含 1 项测试盲点（M9）+ 8 项代码质量/韧性提升
- 批次 3 (LOW): 4 项；可合并 PR

## 已知 ship 落空（务必前置修复）

### M3 · App.vue 缺少 ErrorBoundary 包裹
- **文件**: `web/src/App.vue:1-6`
- **问题**: 模板只有 `<router-view />`，无 ErrorBoundary 包裹 → 任何视图 render 抛错导致白屏，只能刷页
- **整体优化专题** 文档标注 M3 "App.vue 全局 ErrorBoundary" 已 ship，但实际 ErrorBoundary.vue 文件存在却未被 App.vue 引用
- **关联**: `web/src/components/ErrorBoundary.vue` 存在但 `import` 和 `<ErrorBoundary>` 标签均未使用
- **修复**: App.vue template 加 `<ErrorBoundary><router-view /></ErrorBoundary>`，script setup 加 `import ErrorBoundary from '@/components/ErrorBoundary.vue'`

> **审计元发现**: 这是审计执行流程问题。后续 ship 应在每批 commit 前跑 `vue-tsc --noEmit + npm run build + 手测受影响路径 + 验证 import 是否落地`。本专题第 1 批必须先修这个。

## 与前次 handoff 的状态

| 前次 30 项 | 状态 | 备注 |
|---|---|---|
| 整体优化 M1-M4 + L1-L4 | 7/9 ✅，M3 落空 | 详见上 |
| 二次审视 H1-H3 + M1-M10 + L1-L3+L7 | 13/13 ✅ | 全 ship |
| 二次审视 L4/L5/L6/L8 | 4 deferred | L4 CodeBlock 480 / L5 空状态统一 / L6 LoginView 380 / L8 ref 同名 |

### 本轮 agent 与二次审视 ship 的去重说明

| 本轮 agent 报 | 二次审视已 ship | 处理 |
|---|---|---|
| 排版 M1 QueryPanel 640-768 | 二次 M2 (已 ship) | 排除 |
| 排版 L4 CodeBlock 480 | 二次 L4 (deferred) | 排除 (defer 重复) |
| 质量 L4 Element Plus 全量 | 整体 L4 (已 ship) | 排除 |

## 19 项发现

### HIGH (6)

#### H1 · ThsLocalView 表格列宽固定 + max-height 固定
- **文件**: `web/src/views/ThsLocalView.vue:29-49` (table attrs)
- **问题**: 列 `width="280" / "150" / "300"` 累加 ~730px，无横向滚动容器；`max-height="600"` 固定，小屏压缩内容不可见
- **修复**:
  ```vue
  <div class="ths-table-scroll">
    <el-table :max-height="tableMaxHeight">...</el-table>
  </div>
  ```
  ```ts
  const tableMaxHeight = computed(() => window.innerWidth < 768 ? 400 : 600)
  ```
  ```css
  .ths-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  ```

#### H2 · ThsLocalView 加载失败无用户反馈（合并功能 H1+M1+L1 + 质量 H1）
- **文件**: `web/src/views/ThsLocalView.vue:64-77`
- **问题**:
  - `load()` catch 只 `console.error` + `connected = false`，无 UI 错误消息（用户只看到「未连接」无法区分网络/网关问题）
  - 加载成功时 `loadError` 未清空（如果有重新加载逻辑）
  - 与 StatusView/L1CacheView 的 `loadError` ref + `el-alert` 模式不一致
- **修复**:
  ```ts
  const loadError = ref('')
  async function load() {
    loadError.value = ''
    try { ... }
    catch (e: unknown) {
      loadError.value = e instanceof Error ? e.message : '加载失败'
      connected.value = false
    }
  }
  ```
  ```vue
  <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" />
  ```
  同步删 `console.error` (LOW 子项 L1)

#### H3 · App.vue 未包裹 ErrorBoundary（M3 ship 落空，见上）

#### H4 · L1CacheView 命中率卡片 640px 区间溢出
- **文件**: `web/src/views/L1CacheView.vue:331-332`
- **问题**: `.hitrate-card { grid-column: span 2 }` 与 StatusView M3 同模式问题，但 L1CacheView 未 ship；窄屏 1 列时仍 span 2 → 溢出
- **修复**:
  ```css
  @media (max-width: 700px) {
    .hitrate-card { grid-column: span 1; }
  }
  ```

#### H5 · OmniView SyncPanel 硬编码浅色背景破坏暗色主题
- **文件**: `web/src/views/omni/SyncPanel.vue:229-241`
- **问题**: `#f0fdf4`/`#bbf7d0`/`#fffbeb` 等 last-sync 卡片背景，暗色主题下文字融入背景
- **修复**: 替换为 Element Plus 语义变量 `var(--el-color-success-light-9)` / `var(--el-color-warning-light-9)` / `var(--el-color-danger-light-9)` + `var(--el-color-*-light-7)` 边框

#### H6 · StatusView/L1CacheView 重复 load/clock/poll 逻辑应抽 composable
- **文件**: `web/src/views/StatusView.vue:435-505` vs `web/src/views/L1CacheView.vue:198-258`
- **问题**: `load()` / `clockTimer` / `pollTimer` / `autoRefresh` / `visibilitychange` 逻辑 90% 重复，违反 DRY
- **修复**: 抽 `web/src/composables/useAutoRefresh.ts` (新文件)，接受 `loadFn` + `intervalMs` + 选项，返回 `{ data, loading, error, refresh, start, stop }`
- **注意**: composable 必须正确处理 `onBeforeUnmount` 清理 + AbortController 透传

### MED (9)

#### M1 · StatusView/L1CacheView 魔法数字分散
- **文件**: `web/src/views/StatusView.vue:226` `STALE_THRESHOLD_SEC = 30` / `web/src/views/L1CacheView.vue:132` 同样 = 30
- **文件**: `web/src/views/StatusView.vue:477` `10_000` / `web/src/views/L1CacheView.vue:233` 同样 `10_000`
- **修复**: 抽 `web/src/constants/time.ts`：
  ```ts
  export const STALE_THRESHOLD_SEC = 30
  export const POLL_INTERVAL_MS = 10_000
  ```
  两处 view 改 import

#### M2 · StatusView/L1CacheView catch 块 console.error 冗余
- **文件**: `web/src/views/StatusView.vue:454` / `web/src/views/L1CacheView.vue:213` / `web/src/views/ThsLocalView.vue:72`
- **问题**: catch 块同时设置 `loadError` ref + `console.error`，用户已通过 el-alert 看到错误，控制台冗余
- **修复**: 删 `console.error`（生产环境不再泄露）+ 保留 `loadError` UI 反馈；M2 子项 L1 (ThsLocalView) 已在 H2 修复

#### M3 · ConfigView/OmniView/GuidesView selectTab 缺显式返回类型 + watch 参数隐式 any（合并 M1+M2 质量）
- **文件**:
  - `web/src/views/ConfigView.vue:167-173` `selectTab(tab: TabKey)` + `watch((q) => ...)`
  - `web/src/views/OmniView.vue:248-262` 同模式
  - `web/src/views/GuidesView.vue:57-64` 同模式
- **问题**: 函数无 `: void` 返回类型；watch 回调参数 `q` 依赖推断，手抖易错
- **修复**:
  ```ts
  function selectTab(tab: TabKey): void { ... }
  watch(() => route.query.tab, (q: string | string[] | undefined) => { ... })
  ```
  3 处同步改

#### M4 · ConfigView loadProxy 未 await
- **文件**: `web/src/views/ConfigView.vue:230`
- **问题**: `void loadProxy()` 不等待，proxy 加载失败时用户切到 proxy tab 才看到错误
- **修复**: 改为 `await loadProxy()`；如不想阻塞初始 load，加 `if (activeTab.value === 'proxy') await loadProxy()` 守卫

#### M5 · InterfacesView 搜索结果跨分组切换时展开态重置
- **文件**: `web/src/views/InterfacesView.vue:359-363`
- **问题**: `watch(activeGroup)` 时 `expandedNames.value = new Set()` 清空展开态。用户展开工具详情后切换分组，状态丢失
- **修复**: 改为保留跨分组的 Set（不重置），或加提示"切换分组将关闭展开项"

#### M6 · OnboardingPanel sticky nav z-index 冲突风险
- **文件**: `web/src/views/guides/OnboardingPanel.vue:259-262`
- **问题**: `z-index: 5` 偏低，未来若加 modal/tooltip 可能遮挡
- **修复**: 改 `z-index: 10`

#### M7 · OmniView SyncPanel log item min-width 640 风险
- **文件**: `web/src/views/omni/SyncPanel.vue:332-342`
- **问题**: `.log-time { min-width: 140px }` + `.log-source { min-width: 80px }` + `.log-type { min-width: 90px }` 累加 ~310px，窄屏溢出
- **修复**:
  ```css
  @media (max-width: 640px) {
    .log-item { flex-wrap: wrap; }
    .log-time { min-width: 100px; }
    .log-source, .log-type { min-width: auto; }
  }
  ```

#### M8 · ProxyPanel coverage table 无滚动容器
- **文件**: `web/src/views/config/ProxyPanel.vue:53-69`
- **问题**: 列 `min-width="200" + min-width="200"` 累加 ~400px，无 `overflow-x: auto`
- **修复**: 表格外加 `<div class="proxy-coverage-table">` + CSS `overflow-x: auto`

#### M9 · ThsLocalView 零单测覆盖
- **文件**: `web/tests/views/` 缺 ths-local.spec.ts
- **问题**: 新增 view 无任何测试，而其他 view 已有工具函数/组件测试
- **修复**: 补 `web/tests/views/ths-local.spec.ts`，覆盖 load 成功/失败、连接状态切换、loadError 渲染
- **依赖**: happy-dom + vitest 已就绪

### LOW (4)

#### L1 · LoginView 登录失败密码不清空
- **文件**: `web/src/views/LoginView.vue:87-102`
- **问题**: 登录失败后 password 字段保留，用户需手动清除再重试
- **修复**: catch 块加 `form.password = ''`

#### L2 · ErrorBoundary console.error 泄露错误对象
- **文件**: `web/src/components/ErrorBoundary.vue:52`
- **问题**: `console.error('[ErrorBoundary] caught:', err)` 在生产环境泄露完整错误对象
- **修复**: 删 console.error 或改为 `logger.error('[ErrorBoundary]', err.message)`（项目如无 logger 则直接删）

#### L3 · GuidesView/OmniView activeTab 初始化与 watch 重复
- **文件**: `web/src/views/GuidesView.vue:55` / `web/src/views/OmniView.vue:246`
- **问题**: 初始化 `activeTab.value = parseHashTab(...)` + `watch(route.query)` 重复执行一次；ConfigView L3 已 ship 同模式修复
- **修复**: 移除初始化赋值，加 `watch(..., { immediate: true })`

#### L4 · 命名/微优化（agent 标注但无需修）
- `toggles.dangerous` / `toggles.cache` 形容词而非布尔前缀
- `web/src/utils/time.ts` 与 `views/omni/format.ts` + `views/config/format.ts` 名称部分重叠（已有结构，暂不强制合并）
- **处理**: 仅记录，不修

## 执行顺序

```text
第一批 (HIGH): H3 + H2 + H1 + H4 + H5 + H6
  └ H3 必须最先（修 ship 落空）+ H2 同 PR（H2 也涉及 App.vue 周边）+ H1+H4+H5+H6 CSS/AST 修复 + H6 composable 抽
  └ 实际可拆 2 PR：H3+H2 一组（App.vue/ThsLocalView 周边）、H1+H4+H5+H6 一组（视图修复 + composable）
  └ 推荐：单 PR（H3 是 1 行改动，混入无副作用）
第二批 (MED): M1 + M2 + M3 + M4 + M5 + M6 + M7 + M8 + M9
  └ M1+M3 M9 同质（提常量 / 加类型 / 加测试），可合并
  └ M2 + M6 + M7 + M8 CSS/JSX 修改，可合并
  └ M4 + M5 行为修改，独立小 PR
  └ 推荐：2-3 PR
第三批 (LOW): L1 + L2 + L3 + L4 跳过
  └ 3 项 30min 内合并 PR
```

## 验收标准 (每批)

```text
✅ vue-tsc --noEmit  0 error
✅ npm run build  clean (build 时间 ±10% 内)
✅ pytest tests/  不引入回归
✅ web vitest 新测试 100% pass (M9 引入 1 个 spec)
✅ console.html production assets 引用正确 (finalize-build.mjs)
✅ /api/* + /mcp/ 端点契约不变
✅ 视觉走查：H3 ErrorBoundary 触发测试（throw new Error 占位 + 切路由验证）
✅ 暗色主题走查：H5 SyncPanel 3 种状态视觉确认
```

## 风险与回退

- **H3 风险**: App.vue 加 ErrorBoundary 后若 ErrorBoundary.vue 自身 render 错误 → 内部 try/catch (ErrorBoundary.vue 已声明永不递归)
- **H2 风险**: loadError ref 与现有 connected 状态联动 → 测试覆盖 3 态（loading/error/connected）
- **H6 风险**: composable 抽取跨文件，StatusView/L1CacheView 行为必须等价 → 跑两页功能冒烟
- **M1 风险**: 常量提取跨 view，import 路径集中 → 加 `web/src/constants/index.ts` barrel
- **M9 风险**: happy-dom 环境测 ref/computed/lifecycle 已验证，其他 view 也用同模式

## 关联记忆

- [[project-console-overall-optimization]] — 前次 9 项专题 (M3 ship 落空发现)
- [[project-console-second-pass-audit]] — 前次 21 项专题
- [[project-console-vue-migration]] — 阶段 1-6 迁移上下文
- [[project-fuyao-cache-panel-audit]] — FUYAO 缓存面板审计
- [[feedback-no-playwright-default]] — 单测路线不引 Playwright
- 文档路径: `docs/handoffs/2026-09-17-console-third-pass-audit.md`

## 续接

- 当前会话执行第一批 (H1-H6) → 提交
- 新会话恢复时, 先看 git log 哪些 task 已提交 (H3 优先), 接着跑对应批次
- 所有项完成后, 出最后一个 PR summary, 把"控制台第三轮审视"专题关闭
- **元建议**: ship claim 必须配"实际代码检查"（vue-tsc + build + smoke），不能只看 handoff doc 标注