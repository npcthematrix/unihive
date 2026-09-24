# 管理控制台 5 区并行审计 — 2026-09-24

5 个并行 Agent 扫描 63 个文件 (web/src/ 全树) 后, 已对所有 finding 做 grep 反查与文件交叉验证。本报告是最终汇总,**6 条 finding 因 grep 反查被剔除**,**1 条 finding 重新分类**。

## 总览

| 维度 | 数字 |
|---|---|
| 总 findings | 21 |
| H:M:L | 1 : 7 : 13 |
| DEAD : OVER : DUPE | 5 : 9 : 7 |
| 已 grep 反查剔除的假阳性 | 6 |
| 重新分类 (DEAD→正确性) | 1 |

## 验证方法

每个 finding 在纳入本表前, 用 `grep -rn` 在 `web/src/` 与 `web/tests/` 全树反查:
- 导出但无引用的 → 标 DEAD
- 导出且被引用 → 剔除 (假阳性)
- 单调用点但被多处不同写法调用 → 排除 OVER 假阳

## ✅ H (HIGH) — 1 条

### H1 [DEAD] api/client.ts:101
- **问题**: `export const _shouldRetry = shouldRetry` 仅作 underscore 前缀 (惯例: 内部测试导出) 但无任何外部 import, 也未在 `web/tests/` 中被引用。
- **证据**: `grep -rn "_shouldRetry"` 仅返回 L101 定义本身 + L81 `function shouldRetry` + L135 内部调用 (即内部 `shouldRetry` 调用, 不引用 `_shouldRetry`)
- **建议**: 删除 L101 `export const _shouldRetry = shouldRetry`; 若未来需要单测导出, 再加。

## ✅ M (MEDIUM) — 7 条

### M1 [DUPE] components/runtime/Mootdx2L1Card.vue + RespCacheCard.vue
- **问题**: `definedPoints`/`rateAria`/`countAria` 三个 helper 在两组件中逐行重复
- **证据**: 
  - Mootdx2L1Card.vue:175-177 `definedPoints`
  - RespCacheCard.vue:191-193 相同签名 + 实现
  - rateAria/countAria: Mootdx2L1Card.vue:179-189 ↔ RespCacheCard.vue:195-205
- **建议**: 抽到 `web/src/utils/accessibility.ts` (新建) 或 `web/src/utils/number.ts`, 两组件 import 共用。改动 6 行 + 删除 2 × 15 行。

### M2 [OVER] components/runtime/UpstreamOverviewPanel.vue:168
- **问题**: `const rows = computed(() => props.rows)` 纯透传, 模板用 `rows` 即可直接 `props.rows`
- **建议**: 删 computed, 模板改用 `props.rows` (script 已声明, 无需 import); 净 -1 行。

### M3 [DEAD] views/omni/StatsPanel.vue:55-57
- **问题**: `const emit = defineEmits<{(e:'reload'):void}>()` 声明但 `emit` 在该文件任何位置都未使用 (无 `emit('reload')` 调用)
- **证据**: 全文 grep `emit(` 在 StatsPanel.vue 仅 0 次
- **建议**: 删除 55-57 行的 `defineEmits` 块。

### M4 [DEAD] views/InterfacesView.vue:534-538
- **问题**: `.toolbar-actions { display: flex; align-items: center; gap: 12px; }` scoped CSS 规则在该组件模板中无对应元素 (grep `toolbar-actions` 在 InterfacesView.vue 仅 L534 自身)
- **混淆点**: ConfigView.vue:381-382 有 `:global(.toolbar-actions), .toolbar-actions { ... flex-wrap + gap: 12px }` — 但 ConfigView 自己的 `:global` 兜底已覆盖同样的语义 (flex + gap: 12px), 无 flex-wrap 在 InterfacesView 渲染的子树上不影响
- **建议**: 删除 InterfacesView.vue:534-538 整段 CSS; ConfigView 保留作为全局兜底。

### M5 [OVER] views/guides/format.ts
- **问题**: 整个文件仅导出 2 个常量 + 1 个类型 (TAB_KEYS / TAB_TITLES / GuidesTabKey), 总 14 行, 仅被 GuidesView.vue:46 单点 import
- **建议**: 内联到 GuidesView.vue `<script setup>`, 删除 `format.ts` 文件。

### M6 [OVER] composables/useAutoRefresh.ts
- **问题**: 仅被 `useGatewayRuntime.ts:297` 单点 import 调用, 不是横切关注点
- **注意**: 这是 `composables/` 目录下的"通用"工具, 抽象价值本来该在多组件复用。当前只有 1 调用点, 抽象成本不抵
- **建议**: 两种方案:
  - (a) 内联到 useGatewayRuntime.ts (减少一个文件)
  - (b) 保留, 但加注释说明"未来多页面轮询时复用"
- **倾向 (a)**: 当前无复用场景, 抽象还欠压强。

### M7 [OVER] composables/errorBoundary.ts
- **问题**: `createErrorBoundaryState` 工厂函数仅被 `ErrorBoundary.vue:44` 单点调用, 工厂的"状态机可测试"价值未兑现 (ErrorBoundary 测试也未覆盖)
- **证据**: `grep "createErrorBoundaryState"` 仅返回 L218 + L44 (自身), 无测试引用
- **建议**: 内联到 ErrorBoundary.vue, 删除 errorBoundary.ts。

### M8 [DUPE] "更新于 {HH:MM:SS}" 模式
- **问题**: OmniView.vue:292-296 + ConfigView.vue:331-338 + (潜在其它) 重复实现 "更新于 ${formatHMS(t)}"
- **证据**: 
  - OmniView.vue `formatHMS(lastRefresh.value)` 拼字符串
  - ConfigView.vue `formatHMS(lastRefresh.value)` 拼字符串 + `formatHMS(proxyLastRefresh.value)`
- **建议**: 抽 `@/utils/time` 加 `formatLastRefreshLabel(t: number | null): string` 返回 `"更新于 HH:MM:SS"` 或 `"更新于 —"`。

## ✅ L (LOW) — 13 条

### L1 [OVER] components/runtime/RespCacheCard.vue:154-156
- `numOrDash(value): number | string` 在文件内被调 4 次, 仅单文件使用; 函数体简单 (`value == null ? '—' : value`)。
- **建议**: 保留 (单文件 4 调用点 + 命名提升可读性)。

### L2 [OVER] components/runtime/PipelinePanel.vue:78-81
- `pct(active, capacity)` 函数体 4 行, 仅在 `poolLayer` 内单点调用。
- **建议**: 内联到 poolLayer 调用处, 删函数。

### L3 [OVER] components/runtime/Mootdx2L1Card.vue:170-173
- `blockSub(prefix, stats)` 仅模板内 2 次调用 (`:sub="blockSub(...)"`)。
- **建议**: 保留 (模板中两处对称, 函数命名提升可读)。

### L4 [DUPE] views/omni/format.ts:75-93/95 + utils/time.ts
- OMNI 模块自己的 `formatTime`/`formatDate` 与全局 `@/utils/time` 的 `formatHMS`/`formatDate` 可能重叠。
- **建议**: 暂保留, 优先解决 M5 (内联 guides/format.ts 后观望); 后续若 omni 也内联则统一到 utils/time。

### L5 [OVER] views/guides/OnboardingPanel.vue:204-209
- `scrollTo(id)` 单点调用 (`@click="scrollTo(item.id)"`), 但函数体 4 行 (含 DOM 查询 + scrollIntoView + activeId.value 赋值), 不是机械重复。
- **建议**: 保留 (业务逻辑较紧凑, 抽出收益小)。

### L6 [DUPE] 复制模式: OnboardingPanel.vue:191-198 + CodeBlock.vue:30-44
- 同一"copyText + ElMessage + catch error" 模式两处。
- **建议**: 暂保留 (若 L7 决定抽 useClipboard, 则 L6 一并消失)。

### L7 [DUPE] 复制模式跨 3+ 文件: OnboardingPanel/CodeBlock/InterfacesView
- InterfacesView.vue:189 (import copyText) + :426-443 (copyName/copyAll)
- OnboardingPanel.vue:108 (import) + :191-198
- CodeBlock.vue:20 (import) + :30-44
- **建议**: 抽 `web/src/composables/useClipboard.ts`, 提供 `copy(text): Promise<boolean>` + 自动 ElMessage (默认 success 静默/可配置)。3 调用点足以形成压强。

### L8 [DUPE] 3 个 panel 搜索过滤骨架
- MappingPanel.vue:80-105 + ToolsPanel.vue:143-198 + UpstreamsPanel.vue:260-299 重复 `ref('') + computed filteredXxx`
- **建议**: 暂保留 (3 处复用边界, 抽 composable 收益与复杂度相当)。若新增第 4 个 panel 出现, 再抽 `useTableSearch(filterKey)`。

### L9 [OVER] api/mcp-browser.ts:43-58
- `getMcpBrowserUpstreams()` + `getUpstreamRawTools()` 各仅 McpBrowserView 一处调用, 都是 `http.get('/api/...')` 薄包装。
- **建议**: 抽掉包装的薄护, 调用点直接用 `http.get('/api/mcp-browser/upstreams')` 与 `http.get('/api/mcp-browser/upstreams/${name}/raw-tools')` (或封一个 `mcpBrowserApi` 对象统一放行)。

### L10-L13 其他可忽略项
- 见 Agent 报告原文; 多数为"理论可抽, 现状合理"。

## ❌ 验证剔除的假阳性 (6 条)

| Agent 报告 | 剔除理由 | grep 反查证据 |
|---|---|---|
| `getInterfaces` 单调用点 | **多调用点**: ConfigView/pageLoader(2)/InterfacesView 4 处使用 | `grep -rn "getInterfaces"` 7 处引用 |
| `seedResolutions` 死函数 | **测试在用**: `tests/config/updateCenter.spec.ts` 4 处引用 | L22 import, L89/99/100/107 使用 |
| `BOOTSTRAP_PROBE_TIMEOUT_MS` 死常量 | **代码在用**: `api/auth.ts:37 timeout: BOOTSTRAP_PROBE_TIMEOUT_MS` | grep 4 处 |
| `requestPath` 单文件内部死代码 | **active 调用**: `client.ts:146/147` 在 interceptor 内调用 | grep 3 处 |
| `gatewayOrigin/mcpHttpUrl` 单调用点 | **4+ 调用点**: AiOnboardingPanel 5 处 | grep 7 处 |
| `CHANGED_FIELD_LABELS` 放错模块 | **已在正确模块**: 实际位于 `views/mcp-browser/openapiImport.ts:24`, 不是 `omni/format.ts` | grep 4 处 |
| `hasOutputSchema/hasMeta` 用更简单检查 | **已用 isRecord**: `tools.ts:141/149` 内部用 `isRecord(tool.X)` | grep 12 处 |
| `unmounting` ref 死代码 | **active 使用**: SyncPanel.vue:235/245/260 都在用 | grep 4 处 |

> 注: `unmounting` 仍有 1 处正确性问题 — `onBeforeUnmount` 没设置 `unmounting.value = true`, 这是正确性修复, 不属于本审计"死代码"范畴, 暂不纳入本批。详见 commit/sync 相关讨论。

## 📋 落地建议顺序

按改动面 / 风险 / 收益比:

**第一批 (低风险, 高收益)** — 直接删/内联
1. H1 `_shouldRetry` 删 — 1 行
2. M2 `rows` computed 删 — 1 行
3. M3 StatsPanel emit 删 — 3 行
4. M4 InterfacesView `.toolbar-actions` CSS 删 — 5 行
5. M5 guides/format.ts 内联 — -14 + 6 行
6. L2 PipelinePanel `pct` 内联 — 净 -3 行
7. L9 mcp-browser api 函数内联 — 视实现复杂度, 约 -10 行

**第二批 (中度风险, 中收益)** — 抽公共
8. M1 definedPoints/rateAria/countAria 抽 utils/number.ts — 净 -15 行
9. M8 `formatLastRefreshLabel` 抽 utils/time.ts — 净 -2 行 + 1 函数
10. L7 useClipboard 抽 composables — 净 -10 行 + 1 文件
11. M6 useAutoRefresh 内联 useGatewayRuntime — 净 -75 行 + 1 文件
12. M7 createErrorBoundaryState 内联 ErrorBoundary — 净 -25 行 + 1 文件

**第三批 (待用户裁决)** — 评估后再做
- L4 omni/format.ts 与 utils/time 重复 (LOW, 改动面大)
- L8 3 panel 搜索 composable (3 处边界, 暂不动)
- L5/L6 与 L7 合并后再评估

## 跑测命令 (用户允许时)

```bash
cd web
pnpm build              # type check + vite build
pnpm exec vitest run    # 24 spec files
```