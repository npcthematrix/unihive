# 接口列表 — 左侧固定侧栏 + 右侧内容区

**日期**: 2026-09-04
**状态**: 设计已批准，待实现
**范围**: 仅修改 `console.html`

## 背景

当前「接口列表」面板用可折叠卡片展示 87 个 tool（8 个分组）。当用户想看某分组具体工具时需点击展开；筛选需滚动整页查找。本设计改为 IDE 风格的左侧固定侧栏，提升导航效率。

## 目标

1. 单击侧栏项即可查看该分组工具，零折叠点击
2. 全局搜索框 + 实时显示每个分组的匹配计数
3. 自动刷新全部移除，仅手动刷新
4. 单文件 vanilla HTML，零新依赖

## 非目标

- 不改 MCP gateway、API、YAML
- 不做 tool 调用、导出 CSV、用户偏好持久化
- 不引入框架（保持单文件 vanilla）
- 不加暗色模式

## 设计

### 布局架构

**HTML 结构**（替换 `#interfaces` 内部）：
```
<div id="interfaces" class="panel">
  <div class="card ifs-layout">            <!-- grid 240px | 1fr -->
    <aside class="ifs-sidebar">            <!-- sticky top:0, 自带滚动 -->
      <input class="ifs-search" placeholder="全局搜索工具..." />
      <nav class="ifs-nav">
        <button class="ifs-nav-item active" data-group="__all__">
          <span class="dot"></span> 全部 <span class="count">87</span>
        </button>
        <button class="ifs-nav-item" data-group="gateway">
          <span class="dot"></span> 网关内置 <span class="count">0/2</span>
        </button>
        ... 8 个分组
      </nav>
    </aside>
    <main class="ifs-content">             <!-- 独立滚动 -->
      <header class="ifs-content-header">
        <h2>TDX (通达信) <code>tdx_local</code></h2>
        <div class="ifs-content-meta">29 个工具</div>
      </header>
      <table class="ifs-table"> ... </table>
    </main>
  </div>
</div>
```

**CSS Grid**：
- `.ifs-layout`: `display: grid; grid-template-columns: 240px 1fr; gap: 0;`
- `.ifs-sidebar`: `position: sticky; top: 0; max-height: calc(100vh - 40px); overflow-y: auto; border-right: 1px solid var(--border);`
- `.ifs-content`: `min-width: 0; overflow-x: auto;`
- 媒体查询 `<768px`: `grid-template-columns: 1fr` 堆叠，侧栏改横向滚动

**色板新增**：
```css
--primary-soft: #e7f1ff;
--sidebar-bg:   var(--card-bg);
```

### 侧栏项视觉

- 默认：12px padding，dot 灰色 ○
- hover：背景 `#f8f9fa`
- active：背景 `--primary-soft`，左侧 3px primary 边，dot 实心 ●
- 0 匹配（搜索时）：`opacity: 0.4; pointer-events: none`

### count 显示规则

- 搜索为空：`<span class="count">87</span>`
- 搜索 + 有匹配：`<span class="count has-match">12/29</span>`（蓝色高亮分子）
- 搜索 + 0 匹配：`<span class="count zero">0/29</span>`（灰色）

### 交互

**初始加载**：
- `loadInterfaces()` 拉数据存 `allTools`
- 渲染侧栏 9 项（全部 + 8 分组），「全部」默认 active
- 渲染右侧表格（全部 87 行，按分组顺序 + 工具名排序）

**点击侧栏项**：
- 移除所有 `.active`，给点击项加 `.active`
- 重渲染右侧内容区
- 更新 URL hash `#tools=tdx_local`，刷新后保留
- 不重新拉数据

**搜索输入**：
- `input` 事件实时触发（87 行无防抖必要）
- 重算每个分组的 `matched/total` 计数
- 当前 active 项重渲染右侧表格（仅显示该组匹配的工具）
- 若 active 项 matched = 0：右侧空态「此分组无匹配工具」+ [清空搜索] 按钮
- 若 active 项变灰禁：自动切到第一个非零匹配的分组

**键盘**：
- `/`：聚焦搜索框（已在搜索框时跳过）
- `↑`/`↓`：在侧栏项间移动（focus + 自动激活）
- `Esc`：清空搜索框 + 取消 focus

**手动刷新**：
- 全部面板（上游状态、接口列表、配置信息）仅在用户点击「刷新」按钮时拉取
- 删除 `startAutoRefresh()` 与倒计时 UI
- 按钮文案改为「刷新」

### 右侧空态

```html
<div class="ifs-empty">
  <div class="ifs-empty-icon">🔍</div>
  <div class="ifs-empty-text">此分组无匹配工具</div>
  <button class="ifs-empty-action">清空搜索</button>
</div>
```

### 视觉细节

- active 切换 fade-in 100ms
- 侧栏背景色切换 150ms ease
- 不使用 transform 动画（克制风格）
- 表格 td 加 `overflow-wrap: anywhere` 防长 description 溢出

### a11y

- 侧栏项用 `<button>` 元素，`aria-current="true"` 标记 active
- 搜索框配 `<label>`（视觉可隐藏）
- 表格 `<caption>` 包含当前分组名
- 所有动态字符串经 `escapeHtml()` 防 XSS

## 实现要点

**删除**（vs 当前 console.html）：
- `.tool-filters`、`#tool-shown`、`#tool-total` 整段
- `.tool-group`、`.tool-group-header/body/collapsed/caret` 等折叠卡片样式
- `groupTools()`、`renderTools()` 中按折叠卡片生成 HTML 的部分
- `startAutoRefresh()` 整个函数
- header 里的倒计时 span（保留「刷新」按钮）

**新增**：
- CSS：`.ifs-layout`、`.ifs-sidebar`、`.ifs-nav-item`、`.ifs-content`、`.ifs-table`、空态 `.ifs-empty`
- HTML：替换 `#interfaces` 内部结构
- JS 状态：`activeGroup`、`searchQuery`
- JS 函数：`renderSidebar()`、`renderContent()`、`selectGroup(key)`、`applyFilter(tools, q)`、键盘 handler

**视觉保留**：
- tool 名 monospace + primary 色
- `tdx_call` 红色 `DANGEROUS` 徽章
- `cache_ttl_key` 浅蓝徽章
- source 列在「全部」view 中显示，分组 view 也保留（调试直观）

**风险**：
- 窄屏：媒体查询已规划，验证 375/768/1280px 三档
- 表格溢出：`overflow-wrap: anywhere`
- 性能：87 行正则 < 1ms
- hash 冲突：使用 `#tools=` 前缀
- XSS：`escapeHtml()` 应用于所有动态字符串

## 验证

**手动验证**（6 项）：
1. 默认打开「接口列表」→ 显示全部 87 行
2. 点侧栏 TDX → 右侧变 29 行 + 侧栏 active 切换
3. 搜索 "fund" → 全局过滤 + 9 个侧栏项计数实时更新
4. 0 匹配 → 右侧空态 + 侧栏灰禁项
5. 点「刷新」→ 三面板同时重拉
6. URL hash `#tools=tdx_local` → 刷新后恢复 active 项

**Playwright 自动覆盖**：
- 截图 interfaces panel 默认状态
- 触发：搜索 / 切换侧栏项 / 点刷新 / 空态
- 视口：1280×800 桌面 + 375×667 移动

**后端测试**：
- 不加新 pytest（前端改动）
- 现有 60 个 test 不受影响

## 回退

仅修改 console.html，回退 = git revert 一次提交。无 schema、无 API、无数据库迁移。
