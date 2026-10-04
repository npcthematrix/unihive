# 2026-10-01 配置信息页审视 + 缓存与存储重构 — 未提交

## 背景

两轮连续审视：

1. **配置信息页（ConfigView）** —— 用户要求审视"功能逻辑、排版布局"，按建议应用了 4 处修复。
2. **网关运行监控页 → 缓存与存储标签** —— 用户反馈"页面内容太多，需要往下长拉"，经头脑风暴选定方案 C（顶部 summary + sub-tab deep-dive，sub-tab 视觉复用 `.cfg-subtabs` 文字下划线）。

所有改动均未提交、未 push（按既定"commit/push 等用户明确指示"约定）。

---

## 改动 1 —— 配置信息页（ConfigView）修复

### M1 — `copyJson` 改用 displayConfig（MEDIUM → 已修）

`web/src/views/ConfigView.vue`：

- `copyJson()` 改读 `displayConfig.value` 而非 `config.value`；与"工具"tab 展示同源（displayConfig 在原始快照缺 tools 时用 `/api/interfaces` 合成清单补齐）。
- 注释说明目的。

### L1 — 池参数网格窄屏响应式（LOW → 已修）

`web/src/views/config/UpstreamsPanel.vue` scoped CSS：

- 新增 `@media (max-width: 768px) { .cfg-pool-grid { grid-template-columns: max-content 1fr; … } }`，四列折两行（来源+键 / 值+徽标），小屏不再溢出。

### M2 — `UpdateCenterPanel` 懒加载（MEDIUM → 已修）

`web/src/views/config/UpdateCenterPanel.vue`：

- 新增 prop `active`；删 `onMounted(loadStatus)`；改为 `watch(() => props.active, …, { immediate: true })`。
- 仅当 `active && isDb && !statusLoadedOnce && !status && !statusLoading` 时首次拉取模板状态。
- 移除 `onMounted` import（已无用），保留 `watch` import。
- `statusLoadedOnce` ref 防重复拉。

`web/src/views/ConfigView.vue`：

- `<UpdateCenterPanel :entities="entities" :active="activeTab === 'updateCenter'" @applied="onFactoryUpgradeApplied" />`

旁路确认（已排除，无须改）：

- `DatabasePanel` / `ProxyPanel` 在挂载时无自发请求；`UpstreamsPanel.getPoolConfig` 在 onMounted 拉取（合理，默认 tab 即上游）。

### L5 — 搜索框组件抽取（LOW → 已修）

新增 `web/src/views/config/CfgSearchInput.vue`：

- Props: `modelValue: string`、`placeholder: string`、`ariaLabel?: string`
- Emits: `update:modelValue`
- 视觉全部走全局 `.cfg-search-wrap` / `.cfg-search` / `.cfg-search-clear`（main.css 已有），零新 CSS。
- 清空按钮补 `aria-label="清空搜索"`。

替换三处：

- `web/src/views/config/UpstreamsPanel.vue` —— 在 `.ups-toolbar` 内仍与 OpenAPI 按钮同行
- `web/src/views/config/ToolsPanel.vue`
- `web/src/views/config/MappingPanel.vue`

### 未做的 LOW（L2/L3/L4）

- L2 `isDbMode` 依赖 entities 加载完成致编辑列延迟闪现（极微小）
- L3 subtab 导航无 roving tabindex —— 全局 `.cfg-subtabs` 已有 `flex-wrap: wrap` + `overflow-x: auto`，无实际换行问题
- L4 ToolsPanel 300+ 行无虚拟滚动（当前量级可接受）

---

## 改动 2 —— 缓存与存储重构（方案 C）

### 新组件 `web/src/components/runtime/CacheSummaryCard.vue`

- 两层 tile 并排：响应缓存（活跃条目 / 命中率 / 工具覆盖）+ MooTDX2 L1（当前 size / 命中率 / 扫描 L1）。
- 全部派生自 `useGatewayRuntime` 同批 prop，**零新接口**。
- 命中率三档着色：≥80% `rate-good`（绿）/ ≥50% `rate-mid`（橙）/ 否则 `rate-low`（红）；无数据时为空类。
- 异进程 stats 抑制（`respForeign=true`）—— 与 RespCacheCard 同语义。
- 状态点语义同 RespCacheCard / Mootdx2L1Card：实心绿 = 启用、实心灰 = 停用、虚框 = 未知。
- 窄屏 `@media (max-width: 860px)` tile 折单列、tile-sub margin-left 0；560px tile-stats 折单列。

### `web/src/views/GatewayMonitorView.vue`

**Script：

- 新增 `CacheSummaryCard` import。
- 新增 `CACHE_SUBTAB_KEYS = ['resp', 'l1'] as const` + `CacheSubTab` 类型。
- 新增 `activeCacheSub` ref，初值 `parseHashTab(route.query.cacheSub, CACHE_SUBTAB_KEYS, 'resp')`。
- 新增 `watch(() => route.query.cacheSub, …)` 同步。
- 新增 `selectCacheSub(tab)`，写回 URL 时用 `{ ...route.query, cacheSub: tab }`（保留外层 tab）。

模板（替换原 `cache` `<el-tab-pane>` 5 块堆叠）：

```
┌── 两层缓存 · 总览 (CacheSummaryCard) ────────────────────┐
│ [响应缓存 tile]   [MooTDX2 L1 tile]                    │
└──────────────────────────────────────────────────────┘
<nav class="cfg-subtabs"> 响应缓存 / MooTDX2 L1缓存 </nav>
<section v-show="activeCacheSub === 'resp'">
  SessionBar
  cache-note (集合竞价 + 龙虎榜例外)
  RespCacheCard (含 cleanup / vacuum / clear)
</section>
<section v-show="activeCacheSub === 'l1'">
  Mootdx2L1Card (无 SessionBar, L1 不走交易时段感知)
</section>
```

CSS：

- 删除 `.cache-layers-hint`（被 summary 卡头部替代）
- 保留 `.cache-session` / `.cache-note` / `.tab-card-gap` / `.overview-stale-alert`

### 关键设计决策

- **`v-show` 双面板常驻挂载** —— 与外层 `el-tab-pane` 同款，10s 自动轮询持续喂两卡 props，切 sub-tab 不重 mount 数据不丢。
- **SessionBar / cache-note 落到响应缓存 sub-tab** —— 两者都是 SQLite TTL 档的会话感知规则，L1 是进程内 TTL（次日 9 点过期），不相关。
- **URL `?cacheSub=` 保留** —— 切到其他外层 tab 再回来时，仍定位到上次 sub-tab（`selectTab` 已用 spread 保留 cacheSub）。
- **sub-tab 样式零新 CSS** —— 复用 `main.css` 已有的 `.cfg-subtabs` / `.cfg-subtab`。

---

## 验证状态

| 检查 | 结果 |
|---|---|
| `npm run build` (vue-tsc + vite) | ✅ 全绿，本轮 GatewayMonitorView 包从 49.83 → 55.91 KB / gzip 17.52 → 18.88 KB (+1.4 KB) |
| `npx vitest run` | ✅ 25 文件 / 431 用例全绿（在改动 1 完成后跑过） |

本轮改动 2 完成后未跑 vitest；CacheSummaryCard / GatewayMonitorView 改动限于纯渲染与 URL 同步，预期无回归，但建议下次会话起首跑一遍。

---

## 未提交累计（所有改动）

按提交意图可分 4 个原子 commit（建议）：

1. **commit A** —— 配置信息页 M1 + L1 + M2 + L2 wiring
   - `web/src/views/ConfigView.vue`（copyJson + UpdateCenterPanel active prop 接入）
   - `web/src/views/config/UpstreamsPanel.vue`（池网格窄屏响应式）
   - `web/src/views/config/UpdateCenterPanel.vue`（active prop + 懒加载）

2. **commit B** —— L5 搜索框组件抽取
   - `web/src/views/config/CfgSearchInput.vue`（新增）
   - `web/src/views/config/UpstreamsPanel.vue` / `ToolsPanel.vue` / `MappingPanel.vue`（接入）

3. **commit C** —— 缓存与存储 summary 卡
   - `web/src/components/runtime/CacheSummaryCard.vue`（新增）

4. **commit D** —— 缓存与存储 sub-tab 接线
   - `web/src/views/GatewayMonitorView.vue`（script + 模板 + CSS 清理）

或者按"前端审视批"打 1 个。**commit/push 均待用户明确指示**（记忆里有约定）。

---

## 下一步建议

按优先级：

1. 跑一遍 vitest 确认改动 2 无回归。
2. 提交（用户授权后按 commit A/B/C/D 或合并 commit）。
3. **不要 push**（GitHub 网络不通 + 记忆明确）。
4. 重启网关（PID 文件 `logs/gateway.pid`）让前端 console.html 通过 http://127.0.0.1:18080 验证缓存重构效果。
5. 顺手审视 `CachePanel` 在 ConfigView 的 sub-tab 视图（独立专题，未做）。

---

## 参考

- `web/src/views/ConfigView.vue`
- `web/src/views/config/{Upstreams,Tools,Mapping,UpdateCenter}Panel.vue`
- `web/src/views/config/CfgSearchInput.vue`（新增）
- `web/src/views/GatewayMonitorView.vue`
- `web/src/components/runtime/CacheSummaryCard.vue`（新增）
- `web/src/components/runtime/{RespCacheCard,Mootdx2L1Card}.vue`（未改动，被引用）
- `web/src/composables/useGatewayRuntime.ts`（未改动）
- `web/src/styles/main.css` `.cfg-subtabs` 已有样式