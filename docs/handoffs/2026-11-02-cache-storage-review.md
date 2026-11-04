# 网关运行监控 ·「缓存与存储」标签页 审视

> 范围：`web/src/views/GatewayMonitorView.vue` 的 `cache` 标签及其子组件 / composable / API / 全局样式 / 后端契约
> 审视日期：2026-11-02
> 结论先行：**功能逻辑整体稳健、防御充分；排版栅格与响应式处理到位。仅有少量清晰度与文案/交互细节可优化，无功能性阻断 bug。**

---

## 1. 架构与数据流（确认无误）

```
GatewayMonitorView (cache 标签)
├─ CacheSummaryCard      顶层"两层缓存·总览"（始终可见，复用同一批 props 派生，无额外请求）
└─ cache-subtabs（分段控件：响应缓存 / MooTDX2 L1）
   ├─ RespCacheCard      响应缓存（SQLite / logs/cache.db）
   │   ├─ SessionBar + 特例说明
   │   ├─ HitRateCard + 5×MiniStat + 2×TrendRow
   │   └─ CacheBreakdownTable（按工具 Top20 占比）
   └─ Mootdx2L1Card      进程内 L1（日K / 板块 / 异动）
       ├─ HitRateCard + 5×MiniStat + 2×TrendRow
       ├─ 2×L1CacheBlock + <details> 配置常量
```

- 数据统一由 `useGatewayRuntime()` 编排：五源 `allSettled` 独立结算、趋势采样环（60 点/10min）、breakdown 降频（每 6 轮 ≈60s）、四个管理动作（cleanup/vacuum/clear/resetL1）。
- 子标签 ↔ URL `?tab=cache&cacheSub=resp` 双向同步，键盘 roving tabindex 无障碍齐全。
- 命中率阈值（80/50）在 `useHitRate`、`CacheSummaryCard`、`Mootdx2L1Card`、`HitRateCard` 处统一，且有文字 tier 徽标（良好/一般/较低）作为色盲 fallback。

**核对后端 `console_api.py` 后确认一个关键设计是对的**：`get_cache_stats()` 中
`hits/misses/hit_rate/stats_pid` 读自 `cache_stats.json`（上一进程残留时 `stats_pid` 与当前 pid 不一致 → "异进程"），
而 `entries/live_entries/freelist/db_size_mb` 是**直接对 cache.db 实时 SQL/PRAGMA 查询**，始终是当前值。
因此 composable 里 `foreignStats` 只抑制 `hit_rate`（趋势置 null、`—`），而条目数/库大小等仍正常展示——**这是正确且必要的隔离，不是遗漏**。

---

## 2. 功能逻辑审视

### ✅ 已确认正确的防御点（值得保留）
- 管理动作互斥：`anyBusy` 三按钮 `:disabled` 联动，避免 `DELETE`/`VACUUM` 锁库并发（`database is locked`）。
- 破坏性操作 (`clear`/`vacuum`/`resetL1`) 均有 `ElMessageBox.confirm` 二次确认；`clear` 还需后端 `confirm:true` 强确认。
- `cleanup`/`vacuum`/`clear` 收尾调用 `load(false, true)` 强制刷新 breakdown，且用 `pendingBreakdownRefresh` 排队避免被 `inFlight` 丢弃。
- `load()` 中 `inFlight` + `pendingManual` 排队、AbortController 取消在途请求、文档隐藏时停轮询——稳健。
- `null`/`undefined` 一律 `numOrDash → "—"`，避免 0 与未知的歧义。
- breakdown 占比分母用后端 `total_tools`/`entries`，不靠 TopN 行反推，截断提示正确。

### ⚠️ 建议优化（非阻断，按优先级）

**P2 — 总条目 ≠ 活跃 + 过期 的清晰度缺口**
- 位置：`CacheStats`（`entries`/`live_entries`/`expired_entries`）与 `RespCacheCard` 三张 MiniStat。
- 后端有 `null_expiry_entries`（Round-12 后禁止新写的"历史遗留冻结行"，`expires_at IS NULL`），它们既不计入"活跃"也不计入"过期"。因此**活跃+过期 < 总条目**是常态，但卡片上三者并列展示，用户易误以为应相加等于总数。
- 建议：在"过期条目" hint 或总条目 hint 补一句"（含 N 条历史冻结行）"，或在 summary 卡片说明该口径。

**P2 — 整理完成提示的 db_size_mb 未格式化**
- 位置：`useGatewayRuntime.onVacuum` → `整理完成, 数据库 ${resp.db_size_mb} MB`。
- `db_size_mb` 是浮点（如 `0.123`），消息会显示 `0.123 MB`。建议 `.toFixed(2)` 与"库大小"MiniStat 口径一致。

**P3 — VACUUM 按钮的可用判定偏保守**
- 位置：`hasReclaimablePages = enabled && freelist_count > 0`。
- WAL 在高频写入后即便 freelist=0 也可能膨胀，`freelist=0` 时禁用 VACUUM 会错过 WAL 回收。建议改为"库大小/WAL 超过阈值即允许"，或去掉禁用、仅在确认框里说明。

**P3 — 特例说明段落偏长且常驻**
- 位置：`GatewayMonitorView` 里 `cache-note`（集合竞价/龙虎榜说明）。
- 整段始终占用纵向空间。建议折叠为 `<details>` 或精简为单行 + 悬停提示，把首屏留给数据。

**P3 — `?cacheSub=` 在非 cache 顶层标签下残留在 URL**
- 位置：`selectCacheSub` 只增不改；切到 overview 后 `cacheSub` 仍挂在 query 上。
- 无害，但 URL 略脏。可在切走 cache 标签时清理，或接受（用于"记住上次子标签"的语义）。

---

## 3. 排版布局审视

### ✅ 栅格与响应式（已正确）
- `.stat-grid` = `repeat(4, 1fr)`，断点 1024→2 列、560→1 列；`HitRateCard` `grid-column: span 2` 在两种列数下都刚好对齐成整行（RespCacheCard / Mootdx2L1Card 均 8 列当量 → 2×4）。
- `.trend-grid` 双列，768 以下单列。
- `CacheSummaryCard` tile 768→单列、560→tile-stats 单列；`tile-sub` 用 `margin-left:auto` 右推、窄屏归零并换行。
- 间距统一 12px，卡片圆角/边框/底色走全局 `styles/main.css`（已从 6 个 runtime 组件上提，消除重复漂移）。
- 危险按钮 `margin-left:auto` 与常规操作拉开层级，折行自动靠右。

### ⚠️ 可优化（观感层面）

**P2 — 两套 tab 视觉隐喻堆叠**
- 外层 `cfg-subtabs` 是下划线 tab，内层 `cache-subtabs` 是分段控件（segmented）。代码注释已说明这是"标签内过滤"的有意分级，方向正确；
- 但两者紧邻、样式差异较大，第一眼可能让读者误以为是两层 tab。建议给分段控件再加一个极轻的"缓存子视图"小标签或左间距分组，强化"这是同一标签内的切换"语义（注释已做，视觉上可再补一笔）。

**P3 — 总览卡与子标签的视觉分隔**
- `CacheSummaryCard`（始终可见）与下方的 `cache-subtabs` 之间仅靠 `.cache-subtabs margin-top:12px` 分隔，上方 el-card 没有对应下边距。整体可用，但 summary 与"下方详情"之间缺一条更明确的视觉边界（如细分隔线或卡片组标题）。属锦上添花。

**P3 — 纵向高度差导致切子标签时跳动**
- `resp` 子标签（含 SessionBar + note + 大卡 + 表格）与 `l1` 子标签高度差异大，`v-show` 切换时容器高度跳变。可接受（监控页常态），若想更稳可对两个 panel 设 `min-height` 或保留滚动位置。

---

## 4. 结论与建议优先级

| 项 | 类型 | 优先级 | 动作 |
|---|---|---|---|
| 活跃+过期≠总（null_expiry 口径） | 清晰度 | P2 | 补 hint 说明 |
| vacuum 完成提示 db_size_mb 格式化 | 文案 | P2 | `.toFixed(2)` |
| VACUUM 可用性判定 | 交互 | P3 | 放宽阈值或去禁用 |
| 特例说明段常驻过长 | 排版 | P3 | 折叠/精简 |
| 两套 tab 隐喻分级 | 排版 | P2 | 视觉再强化 |
| 总览/详情视觉分隔 | 排版 | P3 | 轻分隔 |
| `?cacheSub` 残留 URL | 整洁 | P3 | 切走时清理 |

**整体评价**：该标签页在功能正确性、错误隔离、无障碍、响应式上已达到较高完成度；剩余均为清晰度/观感级打磨，无逻辑缺陷。建议优先处理 P2 的两处（口径说明 + 提示格式化），P3 可按迭代节奏排队。

> 注：以上均为审视结论，未改动任何源码。如需，我可以把 P2 项直接落地为最小改动（补 hint + `.toFixed(2)`）。
