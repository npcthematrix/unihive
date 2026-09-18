# Console 第三轮审视 · 第二批 (MED+LOW) 未提交收尾

- 日期: 2026-09-17
- 审计专题: `docs/handoffs/2026-09-17-console-third-pass-audit.md` (6 H + 9 M + 4 L = 19 项)
- 第一批 HIGH 已 ship 并 commit
- 第二批 MED + LOW 全部 edit 已落盘, **未 commit**
- 原因: 上下文临界, 用户要求"写 handoff, 不 commit"

---

## 待提交 13 项 (第二份 commit 应包含)

| 编号 | 等级 | 文件 | 改动概述 |
|---|---|---|---|
| M3 | MED | `web/src/views/ConfigView.vue` | `selectTab` 返回 `: void` + watch `q: LocationQueryValue \| LocationQueryValue[]` |
| M3 | MED | `web/src/views/OmniView.vue` | `selectTab` 返回 `: Promise<void>` + watch type + 移除重复 init |
| M3 | MED | `web/src/views/GuidesView.vue` | 同上 |
| M4 | MED | `web/src/views/ConfigView.vue` | `loadProxy` 在 proxy tab 同步等待, 否则 fire-and-forget |
| M5 | MED | `web/src/views/InterfacesView.vue` | 移除 `watch(activeGroup)` 重置 `expandedNames`, 保留组切换展开态 |
| M6 | MED | `web/src/views/guides/OnboardingPanel.vue` | `.onboarding-nav` z-index `5` → `10` |
| M7 | MED | `web/src/views/omni/SyncPanel.vue` | log-item `@media (max-width: 640px)` 折行兜底 |
| M8 | MED | `web/src/views/config/ProxyPanel.vue` | coverage 表包 `<div class="proxy-coverage-table-scroll">` + `overflow-x: auto` |
| L1 | LOW | `web/src/views/LoginView.vue` | 登录失败后 `form.password = ''` 清空 |
| L2 | LOW | `web/src/components/ErrorBoundary.vue` | 删除 `console.error('[ErrorBoundary] caught:', err)` 防泄露 |
| L3 | LOW | `web/src/views/OmniView.vue` | (合并在 M3 里) |
| L3 | LOW | `web/src/views/GuidesView.vue` | (合并在 M3 里) |
| M9 | LOW | (skipped) | `ThsLocalView.vue` 测试 — 用户明确跳过, 留待新专题 |

> L3 与 M3 合并实施, 不再单列。实际独立文件 10 处改动, M9 已跳过。

---

## 验证步骤 (新 session 必须执行)

1. **类型** — `cd web && npx vue-tsc --noEmit`
   - 关键回归: `GuidesView.vue(57)` + `OmniView.vue(248)` + `ConfigView.vue(167)` watch type 须 `LocationQueryValue | LocationQueryValue[]` (vue-router 4+ 的 `string | null` 单值, 不是 `string`)
   - OmniView.vue 上一版只改了 type 注解没改 import — **本 handoff 已补 `import { useRoute, useRouter, type LocationQueryValue }`**
2. **构建** — `npm run build` (含 vue-tsc 复用 + vite + finalize-build)
3. **单测** — `npm run test` (vitest, 与 H1 94 cases 同套)
4. **覆盖率** — `npm run test:coverage`, 关注改动文件 client/clipboard/utils/time, 目标保持 ≥ 80%

## 提交建议

```bash
# 在 web/ 已就绪且未 push 的工作树基础上:
git status  # 应见 10 文件 M, 无 ?? (本批次内)
git diff --stat src/

# 建议单 commit (第二批全部 MED+LOW, 主题清晰):
git add web/src/views/ConfigView.vue \
        web/src/views/OmniView.vue \
        web/src/views/GuidesView.vue \
        web/src/views/InterfacesView.vue \
        web/src/views/guides/OnboardingPanel.vue \
        web/src/views/omni/SyncPanel.vue \
        web/src/views/config/ProxyPanel.vue \
        web/src/views/LoginView.vue \
        web/src/components/ErrorBoundary.vue
git commit -m "refactor(console): 第三轮审视第二批 (MED+LOW)
- M3 watch/selectTab 返回类型 + tab 路由初始化去重 (3 view)
- M4 ConfigView proxy tab 同步等待 loadProxy
- M5 InterfacesView 组切换保留展开态
- M6 OnboardingPanel 顶位 z-index 提升
- M7 SyncPanel 移动端 log-item 折行
- M8 ProxyPanel 覆盖率表横向滚动兜底
- L1 LoginView 失败清空密码
- L2 ErrorBoundary 删 console.error 防错误对象泄露
M9 ThsLocalView 测试跳过, 留新专题"
```

---

## 第一批已 ship (HIGH, 已 commit)

详情见上一份 handoff / git log。包含: H1 App.vue 包 ErrorBoundary + H2/H5 + H3 ErrorBoundary 装载 + H6 useAutoRefresh composable + ThsLocalView H1+H2 (loadError + table scroll)。本批次与 H1/H2/H5/H3/H6 无依赖冲突, 可独立 ship。

## 风险与回滚

- M3 watch type 改动: 验证 3 个视图 (Config / Omni / Guides) 顶栏 hash tab 同步正常; 若 type 改错, vue-tsc 已能拦截, 不会进运行时
- M5 InterfacesView: 仅 watch 移除, 不会触发 group 上浮
- M4 同步等待: 仅 `activeTab === 'proxy'` 时 await, 不影响 onMounted 首屏
- M8/L1/L2/M6/M7 全为单行/几行, 回滚 `git revert` 即可

---

## 上下文备注

- 上下文已临界 (98%), 第一份 commit 后才进入第二批
- 用户答复: "写 handoff, 不 commit" — 此 handoff 是新 session 接手的最短路径
- 后续若想保留 THS LOCAL 相关 ?? 文件 (web/src/views/ThsLocalView.vue 等), 用户尚未表态, 不要自动 `git add` 它们