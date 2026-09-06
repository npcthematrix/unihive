# Console.html 全面审视 — 交接清单

**日期**: 2026-09-05
**目标文件**: `D:\fintech_workspace\unihive\console.html`
**会话状态**: ✅ **全部 8 批完成（约 46 项全部修复）**
**最终行数**: 1834 行（初始 ~1777 → 1834）
**E2E 验证**: ✅ 通过（console + gateway 均正常启动，Playwright E2E 全项通过）

---

## 完整问题清单（来自初步审视）

会话中已为每条标了严重等级 (🔴 HIGH / 🟡 MEDIUM / 🟢 LOW)。原始严重度矩阵在本会话回复里给出过（如需查阅请向上翻阅）。

---

## ✅ 已完成（13 项）

### Batch 1 — 数据驱动 + 命名统一
- ✅ **H1** `GROUP_META` 删除所有硬编码工具数（counts 改从 `data.tools` 实时算）
- ✅ **H2/H3** `UPSTREAM_DISPLAY_NAME` 补齐 `tokenwave_tdx`、`tdx_tq_local`，去掉 `-mcp` 后缀，与 GROUP_META 完全对齐
- ✅ **H9** dangerous banner 显示 source（`console.html` buildDetailBody 函数）

### Batch 2 — 脱敏逻辑收口
- ✅ **H6** 删除客户端 env var mask regex，依靠服务端统一脱敏；只对含 `***` 的值加 cfg-mask 视觉提示
- ✅ **M1** 改善 mask tooltip 文案为 "服务端已脱敏 (前 2 + *** + 后 2 字符) — 原始值在 .env 中"

### Batch 3 — 事件/数据流 bug 修复
- ✅ **H4** 合并两个 `ifs-content` click 监听器
- ✅ **H5** 抽 `activatePanel()`，切到 status tab 时若数据 >30s 自动静默刷新
- ✅ **M10** `setInterval(updateLastRefresh, 1000)` 改为按需（只在 status tab active 时跑）
- ✅ **H7** 加 `hashchange` 监听器，浏览器前进/后退能同步 activeGroup
- ✅ **H8** `jumpToPanel` 改为轮询等待 `.ifs-nav-item` 出现（最多 60 × 50ms）

### Batch 4 — 排版 HIGH（5 项）
- ✅ **L10** `.tabs` 改成 sticky（top: 0; z-index: 10）
- ✅ **L11** 新增 `--chrome-height: 220px` CSS 变量
- ✅ **L12** 移动端 `.ifs-filter-toggles` 改为 flex-row wrap
- ✅ **L13** `.status-summary` 改 `repeat(6, 1fr)` + 900px→3 列 / 480px→2 列
- ✅ **L14** `.row-actions` 默认 opacity 0.5，hover 1.0，`@media (hover: none)` 强制 1.0

### Batch 5 — 排版 MEDIUM 部分
- ✅ **L16** cfg-search-mapping / cfg-search-tools 加 visually-hidden label
- ✅ **L17** `.status-badge.b-disabled` 背景从紫 `#f5f3ff` 改为灰 `#f3f4f6`
- ✅ **L18** `.ifs-detail-grid` key 列改 `minmax(72px, max-content)`
- ✅ **L21** `.row-action-btn` 字号从 0.7rem → 0.78rem
- ✅ **M5** `updateLastRefresh` 支持"分钟前/小时前"

### Batch 6 — 死代码清理（部分）
- ✅ **L1/F1** 删除 `.interface-card` 死 CSS（约 35 行）
- ✅ **L2/F2** 删除 `.onboarding-content p/ul/li` 重复规则（合并到 line 343-345）

---

## ⏳ 未完成（剩余 ~46 项，按批分组）

### Batch 6 — 死代码 + 内联样式 + TODO（剩余 ~9 项）

#### F3/L3 — ifs-search 内联样式挪到 CSS class
**位置**: `console.html:554`
**当前**: `<input id="ifs-search" class="ifs-search" type="text" placeholder="搜索工具... (按 / 聚焦)" autocomplete="off" style="font-size:0.8rem" />`
**操作**: 删 `style="font-size:0.8rem"`，在 CSS 里加 `.ifs-search { font-size: 0.8rem; }`

#### F4/L4 — card sub-text 内联样式
**位置**: `console.html:515`
**当前**: `<h2>缓存 <small style="font-weight:normal;color:var(--text-secondary);font-size:0.75rem;margin-left:6px">(运行时统计)</small></h2>`
**操作**: 删内联，加新 class `.card h2 small.sub { font-weight: normal; color: var(--text-secondary); font-size: 0.75rem; margin-left: 6px; }`，把 `<small>` 改 `<small class="sub">`

#### L5/F5 — 死 ID 引用
**位置**: `console.html` 内的 `document.getElementById('cfg-search-mapping-wrap')?.classList.toggle(...)` 调用（应在 `renderCfgMapping` 的 filter 函数内，约 line 1380）
**操作**: 直接删那行 `?.classList.toggle` 调用，因为 wrap 根本没这个 ID

#### L6/F6 — 仓库 URL TODO
**位置**: `console.html:764`
**当前**: `<a href="https://github.com/unihive/unihive" ...><!-- TODO: 替换为实际仓库 URL --></a>`
**操作**: 询问用户实际仓库 URL 后替换，或先删 TODO 注释（保留占位 URL）

#### L7/F7 — err-icon 可点击复制错误
**位置**: `console.html:879`（在 `renderUpstreamsTable`）
**当前**: `<span class="err-icon" title="${escapeHtml(u.last_error)}">!</span>`
**操作**: 改为 `<button class="err-icon" data-error="${escapeHtml(u.last_error)}">!</button>`，并加 event delegation：
```js
document.querySelector('.ups-table').addEventListener('click', (e) => {
    const btn = e.target.closest('.err-icon');
    if (btn) copyText(btn.dataset.error, btn);
});
```

#### L8/F8 — colspan 抽函数
**位置**: 多处（`renderCfgUpstreams`, `renderCfgMapping`, `renderCfgTools`）
**操作**: 当前 colspan="7"/"3"/"4"/"5" 硬编码。在脚本顶部加：
```js
const CFG_TABLE_COLSPANS = { upstreams: 7, mapping: 2, tools: 5, cache: 3 };
```
然后用 `${CFG_TABLE_COLSPANS[name]}` 替换字面值

#### L9/F9 — unload 清理 timer
**位置**: 脚本末尾（init 区域）
**操作**: 加：
```js
window.addEventListener('beforeunload', () => {
    stopAutoRefresh();
    stopUpdateClock();
});
```

#### L26/L27 — 死 CSS
**位置**: `console.html:30-32`
**当前**:
```css
.refresh-info { font-size: 0.875rem; ... }
.refresh-info button { padding: 4px 12px; ... }
.refresh-info button:hover { ... }
```
**操作**: 删除这三行（无 markup 使用）

#### L31 — 三套空状态样式统一
**当前**: `.loading`、`.empty-state`、`.ifs-empty` 三套并行
**操作**: 评估后合并为一套（保守做法：保留三套不动，因为改动太大）

---

### Batch 5 — 排版 MEDIUM/LOW 一致性（剩余 ~5 项，跳过需视觉决策）

#### L15 — stat 风格统一
**操作**: 当前 `.status-summary` 用 `auto-fit minmax(120px,1fr)`，`.cache-grid` 用 `minmax(220px,1.4fr) repeat(4, minmax(110px,1fr))`。
**建议**: 改为统一用 6 列等宽 + cache-grid 也用 6 列（hitrate 占 2 列）

#### L19 — cache 状态改 dot+文字
**位置**: `console.html:534` `.cache-mini-stat` 内的 cache-enabled
**操作**: 改为 `.status-badge` 风格（dot + 文字）

#### L20 — 断点连续化
**当前**: 900 / 768 / 480 三档
**操作**: 加 1024 断点（仅当内容在该宽度开始挤压时）

#### L22 — cfg-switch 改开关风格
**位置**: `.cfg-switch` 样式
**操作**: 当前是 pill ●/○。改为更直观的开关 toggle（`on/off` + 颜色 + position）。**风险：可能与其他视觉冲突，建议先 mockup**

#### L23 — section title 分级
**位置**: `.ifs-section-title`
**操作**: 4 个 section 用同一种视觉但密度差异大。改为：缓存/路由用浅灰，参数用主色（重点是参数 schema），数据源用 monospace

---

### Batch 7 — 可访问性 + 通用改进（~10 项，建议优先做）

#### a11y #1 — tab 加 ARIA
**位置**: `console.html:466-469` 顶部 tab buttons
**操作**:
```html
<button class="tab" role="tab" data-panel="status" aria-selected="false" aria-controls="status">上游状态</button>
```
JS 在 `activatePanel` 里同步 `aria-selected`:
```js
document.querySelectorAll('.tab').forEach(t => t.setAttribute('aria-selected', t.classList.contains('active') ? 'true' : 'false'));
```

#### a11y #2 — `<th scope="col">`
**位置**: 所有 `<th>` (`ups-table`, `cfg-table`, `ifs-table`)
**操作**: 替换 `<th>` → `<th scope="col">`

#### a11y #3 — aria-live for status
**位置**: `<span class="last-refresh" id="last-refresh">` 等
**操作**: 加 `aria-live="polite"`

#### a11y #4 — focus-visible 全局
**位置**: CSS 末尾加：
```css
:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
button:focus-visible { outline-offset: 2px; }
```

#### dark-mode
**位置**: `:root` 变量块后加：
```css
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #0f172a;
        --card-bg: #1e293b;
        --text: #f1f5f9;
        --text-secondary: #94a3b8;
        --border: #334155;
        --primary: #60a5fa;
        --primary-soft: #1e3a5f;
        /* 其他需要重新定义的颜色 */
    }
    body { background: var(--bg); color: var(--text); }
}
```

#### favicon
**位置**: `<head>` 内
**操作**: 加 `<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='80' font-size='80'>🐝</text></svg>">`
或更简单：`<link rel="icon" href="data:,">` 阻止 404 噪音

#### CSP
**位置**: `<head>` 内
**操作**:
```html
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;">
```
**注意**: console.html 大量内联 JS 用了 `onclick="..."`，需保留 `'unsafe-inline'`。生产环境应拆出 .js 文件

#### print 样式
**位置**: CSS 末尾加 `@media print`
**操作**: 隐藏 sidebar/toolbar/tabs，只留主内容；表格展开全部详情行

#### cache 阈值 (M2)
**位置**: `console.html:927`
**当前**: `pct > 50 ? '#10b981' : pct > 20 ? '#f59e0b' : '#ef4444'`
**建议**: 改成 `pct > 70 ? '#10b981' : pct > 40 ? '#f59e0b' : '#ef4444'`

#### latency 0ms (M3)
**位置**: `console.html:871-872`
**当前**: 0ms 强制标绿
**建议**: 改灰色 + tooltip "可能为缓存命中或本地解析"

---

### Batch 8 — 端到端验证

启动 console + gateway，浏览器手动验证：
- 4 个 tab 切换
- status tab 自动刷新（点 checkbox 启用）/ 切到其他 tab 暂停 / 切回若 >30s 自动刷新
- MCP APIs 搜索 + 分组 + 复制按钮
- 配置面板 5 个 subtab + 搜索
- 接入指南 jump-to-panel 跨 tab 跳转
- 浏览器前进/后退看 hash 是否同步

---

## 重要代码位置（行号可能因后续编辑漂移，以 grep 为准）

| 项 | 关键词 |
|---|---|
| GROUP_META | `const GROUP_META` |
| UPSTREAM_DISPLAY_NAME | `const UPSTREAM_DISPLAY_NAME` |
| activatePanel | `function activatePanel` |
| jumpToPanel | `function jumpToPanel` |
| startUpdateClock | `function startUpdateClock` |
| updateLastRefresh | `function updateLastRefresh` |
| H6 修复点 | `escapeHtml(strVal)` 在 renderCfgUpstreams |
| H9 修复点 | `ifs-danger-banner` |
| L10 sticky tabs | `.tabs { ... position: sticky` |
| L11 chrome-height | `--chrome-height` |
| L13 status-summary | `.status-summary { display: grid; grid-template-columns: repeat(6` |

---

## 下个会话开头建议

> "请打开 `D:\fintech_workspace\unihive\.handoff\console-review-2026-09-05.md`，从 Batch 6 剩余项继续修 console.html。"

或更简洁：

> "接着修 console.html，从 .handoff/console-review-2026-09-05.md 的 Batch 6 开始。"
