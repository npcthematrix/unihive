# 控制台 Vue3 迁移 — 全部 6 阶段完成 handoff

**日期**: 2026-09-14
**状态**: 阶段 1-6 全部提交, 累积 code-reviewer 在跑 (后台 agent aadc5c555b1c09157)
**结论**: 旧 console.html/js/css 已拆, 新 Vue SPA 已上线, pytest 19/19 + 全套 545/1 pass

## 提交清单 (阶段 1-6)

| 阶段 | SHA | 内容 |
|---|---|---|
| 1 | `3a6e989` + `e4e7b48` | 后端 JSON 鉴权端点 + web/ 脚手架 + 登录闭环 |
| 2 | `6e20c1b` | StatusView 状态页 (755 行, code-reviewer APPROVE) |
| 3 | `8dbd725` | InterfacesView 接口页 (8 文件, WARNING 全修) |
| 4 | `92a7ada` | ConfigView 配置页 (8 文件, 含 proxy PUT) |
| 5 | `189b8d8` | OmniView 板块管理 (6 文件, AbortController 中止) |
| 5.5 | `514a03b` | fix(omni) query_sectors 缩进回归 |
| 6 | `3d53d2b` | **GuidesView 接入指南 + 旧 console 拆除 + pytest 改写** |

## 阶段 6 提交细目 (3d53d2b)

**新增**:
- `web/src/views/GuidesView.vue` (重写 stub → 2 subtab 容器 + hash 深链)
- `web/src/views/guides/CodeBlock.vue` (共享 `<pre>` + 复制按钮组件)
- `web/src/views/guides/OnboardingPanel.vue` (5 章节, IntersectionObserver scroll-spy)
- `web/src/views/guides/AiOnboardingPanel.vue` (3 章节纯静态)
- `web/src/views/guides/format.ts` (2 subtab 常量)
- `static/console/assets/*` (22 文件, Vite 产物)
- `docs/handoffs/2026-09-14-console-vue-phase6-guides-and-teardown.md`

**修改**:
- `console.html` (旧 SPA 内联 → 嵌入 Vite 产物, 引用 `/static/console/assets/index-*.{js,css}`)
- `tests/test_console_html_unused.py` (8 项 → 4 类断言, 改 Vue 视图覆盖 + console.html 薄壳形态)
- `tests/test_console_interfaces.py` (2 个旧 HTML 耦合断言改端点契约)

**归档 (git mv)**:
- `static/console/console.js` → `archive/console-pre-vue-2026-09-14/console.js`
- `static/console/console.css` → `archive/console-pre-vue-2026-09-14/console.css`

**未触碰 (login.css 仍在)**:
- `static/console/login.css` (登录页独立依赖, 保留)

## 验证记录

- `vue-tsc --noEmit` clean
- `npm run build` clean, 6.25s, GuidesView chunk 12.56 kB / 5.31 kB gzip
- `pytest tests/test_console_html_unused.py tests/test_console_interfaces.py` — 19/19 pass
- `pytest tests/` — 545 passed + 1 skipped, 50.6s
- gateway :18080/console.html — 200, 引 production assets
- vite :5173 — 200 (dev HMR)

## 已知遗留 (与迁移无关, 下次清理)

未提交改动 (2026-09-14 之前会话的遗留):
- `config/tools_mootdx2.yaml` — M
- `config/tools_tdx_quant.yaml` — M
- `src/unihive/api/mootdx2_client.py` — M
- `tests/test_mootdx2_reader_offline.py` — M
- `src/unihive/api/index_registry.py` — 新文件 (??)

这些与阶段 1-6 迁移无关, 是别的会话的工作。下一会话如继续, 应先 git stash list / git log 看上下文。

## 累积 code-reviewer (后台 agent)

- agent_id: `aadc5c555b1c09157`
- review span: c5f9d71..3d53d2b (92a7ada + 189b8d8 + 514a03b + 3d53d2b)
- 完成后会以 task-notification 形式送达
- 输出按 severity (CRITICAL/HIGH/MED/LOW/INFO) 分类
- HIGH/MED 必修, LOW/INFO 可 deferred

## 阶段 6 关键经验

- **`test-mcp-conn` 端点不存在**: handoff 调研阶段推测的动态部分不成立, GuidesView 全静态即可
- **静态内容复制即可**: 90% 内容直接复用旧 console.html 文本, 不需要重新组织
- **IntersectionObserver scroll-spy**: 在 Vue onMounted 初始化 + onBeforeUnmount disconnect, 跟 router watcher 同生命周期模式
- **CodeBlock 抽组件**: 复制逻辑提到一处, OnboardingPanel + AiOnboardingPanel 共享
- **旧测试改写比删更稳**: 测试名字改但保留测试意图 (descByName/Chain → getStatus/不引 fallback_chain), API 契约测试保护服务器侧, 视图测试保护客户端侧

## 路由完整性

| 路由 | 视图 | 状态 |
|---|---|---|
| `/login` | LoginView | ✅ 阶段 1 |
| `/status` | StatusView | ✅ 阶段 2 |
| `/interfaces` | InterfacesView | ✅ 阶段 3 |
| `/config` | ConfigView | ✅ 阶段 4 |
| `/omni` | OmniView | ✅ 阶段 5 |
| `/guides` | GuidesView | ✅ 阶段 6 (本次) |

所有路由 hash 模式, 深链 `?tab=` 互通, fallback 路由统一回 status。

## 续接

- 累积 reviewer 输出到位后, 按 HIGH/MED 优先级修, 出最终 fix 提交
- 清理 5 个遗留未提交文件 (commit 或 stash, 别混进迁移分支)
- 如果有视觉/交互回归, 安排阶段 7 UI polish (CSS 微调, 动画, 空状态插画等)