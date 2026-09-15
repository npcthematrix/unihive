# 控制台二次审视 — 专题 handoff (3 HIGH + 10 MED + 8 LOW)

**日期**: 2026-09-15
**专题**: 控制台整体优化专题（9 项全部 ship 后）的第二轮审视
**结论**: 3 并行审查 agent（功能逻辑 / 排版布局 / 代码质量）识别 21 项改进，按 3 批提交

## 背景

`docs/handoffs/2026-09-14-console-overall-optimization.md` 9 项（H1 + M1-M4 + L1-L4）已全部 ship。本专题是 ship 后的二次审视，重点是：
- 之前未覆盖的细节（响应式 CSS / a11y 细节 / 错误反馈重复）
- FUYAO 缓存面板审计留 5 项 LOW + 本次审计新发现的同类 LOW

**Why**: 第一轮集中在测试基建 + 全局重构，第二轮聚焦局部 CSS / a11y / 交互闭环。

**How to apply**: 3 批提交
- 批次 1 (HIGH): 3 项布局 CSS，零业务风险
- 批次 2 (MED): 6 项（含 3 项 a11y + 1 项版本冲突写闭环 + 1 项 util 抽取 + 1 项重复错误反馈）
- 批次 3 (LOW + 散点): 8 项散点，含空状态统一、a11y polish、性能小优化

## 与前次 handoff 的状态

| 前次 9 项 | 状态 |
|---|---|
| H1 vitest | ✅ done |
| M1 侧栏响应式折叠 | ✅ done |
| M2 SyncPanel boardType 4 种 | ✅ done |
| M3 App.vue ErrorBoundary | ✅ done |
| M4 axios 5xx/timeout 重试 | ✅ done |
| L1 StatusView hidden 跳过轮询 | ⚠️ 未明确确认（clockTimer 仍 1s 跑是独立 LOW） |
| L2 logout 二次确认 | ⚠️ 未在二次审计范围 |
| L3 a11y skip-link + 状态色图标 | 🔶 skip-link done; 状态图标剩 3 项 MED |
| L4 Element Plus 按需 | ⚠️ 未在二次审计范围 |

## 21 项发现

### HIGH (3)

#### H1 · StatusView 顶部 6 列网格窄屏溢出
- **文件**: `web/src/views/StatusView.vue:562`
- **问题**: `.summary-grid { grid-template-columns: repeat(6, 1fr) }` 只有 1100px 降到 3 列，< 640px 仍 6 列
- **修复**: 加 `@media (max-width: 900px) { 2 列 }` + `@media (max-width: 560px) { 1 列 }`

#### H2 · UpstreamsPanel 表格窄屏溢出
- **文件**: `web/src/views/config/UpstreamsPanel.vue:61-83`
- **问题**: 列宽累加 ~770px (Endpoint 200 + API Key 170 + 其他 + padding), 无响应式
- **修复**: 外包 `<div class="ups-table-scroll">` + `overflow-x: auto`

#### H3 · InterfacesView 侧栏+表格在平板宽度撑爆
- **文件**: `web/src/views/InterfacesView.vue:563-564`
- **问题**: `.ifs-sidebar { flex: 0 0 264px }` + 表格列 ~660px, 共需 ~924px, 900-924px 区间布局破裂
- **修复**: 900-1100px 侧栏收为 220px; < 900px 已切 column (OK); < 768px 表格转卡片式

### MED (10)

#### M1 · FuyaoCachePanel 版本冲突后 UI 不刷新
- **文件**: `web/src/views/config/FuyaoCachePanel.vue:425-435`
- **问题**: 用户 A 保存 → 用户 B 改 → 用户 A 再保存 → "版本冲突" 弹窗 → 弹窗关后 UI 仍显示 A 的旧配置
- **修复**: 弹窗 `ElMessageBox.alert` 关闭后 `emit('saved', ...)` 触发父级 reload 或 reload 本地 props.config

#### M2 · QueryPanel 查询栏 640-768px 尴尬换行
- **文件**: `web/src/views/omni/QueryPanel.vue:310-311`
- **问题**: `.query-keyword { width: 320px }` + 两个 160px selects 在 640-768px 区间 wrap 难看
- **修复**: 窄屏 `min-width: 200px` 或 stack

#### M3 · StatusView cache-hitrate-card `grid-column: span 2` 窄屏溢出
- **文件**: `web/src/views/StatusView.vue:736`
- **问题**: `.cache-hitrate-card { grid-column: span 2 }`, 窄屏 grid 1 列时仍 span 2 → 溢出
- **修复**: 窄屏 `@media (max-width: 700px) { grid-column: span 1 }`

#### M4 · FuyaoCachePanel 缺 640px 中间断点
- **文件**: `web/src/views/config/FuyaoCachePanel.vue:589`
- **问题**: 只在 900px 切单列, 640-900px 区间 preset 网格挤压
- **修复**: 加 `@media (max-width: 640px)` 中间梯度

#### M5 · OnboardingPanel sticky 顶距用未定义变量
- **文件**: `web/src/views/guides/OnboardingPanel.vue:258-261`
- **问题**: `top: var(--app-header-height, 0)` 默认 0, 与 52px header 不对齐, 实际行为正确 (因 fallback 是 0 + 真实 sticky 也对得上) 但语义错
- **修复**: `web/src/styles/main.css` 加 `--app-header-height: 52px`

#### M6 · LoginView 输入框缺 `for`/`id` 关联
- **文件**: `web/src/views/LoginView.vue:27-35`
- **问题**: el-form-item prop 关联, 但 input 缺 `id` + label 无 `for`, 屏读器无法朗读字段名
- **修复**: 加 `id` 到 input, `for` 到 label (或 aria-label)

#### M7 · ProxyPanel 状态值缺 a11y
- **文件**: `web/src/views/config/ProxyPanel.vue:22-27`
- **问题**: `.proxy-status-value` 是颜色+文本, 屏读器只读文本不知语义
- **修复**: 加 `role="status"` + `aria-label="代理状态: ${statusHint}"`

#### M8 · SyncPanel 状态 tag 缺 a11y
- **文件**: `web/src/views/omni/SyncPanel.vue:17`
- **问题**: el-tag 仅颜色 + 文本, 色盲用户难区分
- **修复**: 加 `aria-label="同步状态: ${lastLog.status}"` + 前置 unicode 符号 (✓/✗/⚠)

#### M9 · ConfigView + OmniView 重复 formatTime
- **文件**: `web/src/views/ConfigView.vue:260-264` + `web/src/views/OmniView.vue:280-284`
- **问题**: 完全相同的 5 行函数
- **修复**: 抽到 `web/src/utils/time.ts` `formatHMS(ts: number): string`, 两处导入

#### M10 · ProxyPanel 错误反馈双重显示
- **文件**: `web/src/views/config/ProxyPanel.vue:157-158`
- **问题**: `saveStatusText` + `ElMessage.error` 同时显示
- **修复**: 保留 saveStatusText (持久化) + 删 ElMessage, 或反之

### LOW (8)

| # | 文件:行 | 问题 |
|---|---|---|
| L1 | StatusView.vue:487-493 | clockTimer 即使 autoRefresh=false 也每秒跑 (cpu 浪费) |
| L2 | ProxyPanel.vue:144 | onToggle 无 `if (saving) return` 入口守卫 |
| L3 | ConfigView.vue:163-170 | activeTab 初始化 + watcher 同 query 跑两次 |
| L4 | CodeBlock.vue:77 | 480px 孤儿 media query (外层无对应断点) |
| L5 | 全局 | 空状态 8 处实现不一致 (`:empty-text` / `.ifs-empty` / `.feedback`) |
| L6 | LoginView.vue:114 | 380px 登录卡在 320px 屏 padding 局促 |
| L7 | QueryPanel.vue:166 | 本地 `./format` 与 `omni/format.ts` 重复 |
| L8 | QueryPanel.vue:186-188 | keyword/source/boardType ref 与 prop 同名 (intentional) |

## 执行顺序

```text
第一批 (HIGH): H1 + H2 + H3
  └ 纯 CSS 响应式修复, 独立 PR, 零业务风险
第二批 (MED): M1 + M5 + M6 + M7 + M8 + M9 + M10
  └ 写路径 + a11y + util 抽取 + 错误反馈去重
  └ M2/M3/M4 是纯 CSS 排版, 可合并到第一批
第三批 (LOW + 散点): L1-L8
  └ 性能小优化 + 命名 + 空状态统一 (L5 单独子 PR)
```

## 验收标准 (每批)

每批 commit 必须满足:
```text
✅ vue-tsc --noEmit  0 error
✅ npm run build  clean (build 时间 ±10% 内)
✅ pytest tests/  不引入回归
✅ console.html production assets 引用正确 (finalize-build.mjs)
✅ /api/* + /mcp/ 端点契约不变
```

## 风险与回退

- **H1-H3 风险**: CSS 仅影响视觉, 不影响数据; 出问题可 revert 单文件
- **M1 风险**: emit 触发 reload 若父级有未保存改动 → 用明确 reload 而非 silent emit
- **M9 风险**: util 抽取跨文件, 同步更新两处 import
- **L5 风险**: 空状态统一涉及 8 处, 改动面大, 拆子 PR

## 关联记忆

- [[project-console-overall-optimization]] — 前次 9 项专题
- [[project-console-vue-migration]] — 阶段 1-6 迁移上下文
- [[project-fuyao-cache-panel-audit]] — FUYAO 缓存面板审计
- [[feedback-no-playwright-default]] — 简单 CSS 改不动浏览器
- 文档路径: `docs/handoffs/2026-09-15-console-second-pass-audit.md`

## 续接

- 当前会话执行第一批 (H1+H2+H3) → 提交
- 新会话恢复时, 先看 git log 哪批已提交, 接着跑对应批次
- 所有项完成后, 出最后一个 PR summary, 把"控制台二次审视"专题关闭
