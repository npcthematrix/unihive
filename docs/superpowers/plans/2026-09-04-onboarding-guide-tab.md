# 接入指南标签页实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 UniHive Console 新增"接入指南"标签页，面向开发者与量化研究者提供 UniHive MCP 的接入文档。

**Architecture:** 单文件改动（`console.html`），新增一个标签面板，沿用现有 CSS 变量与卡片样式。静态章节内容写在 HTML 中，接口列表动态从 `/api/interfaces` 拉取，代码块带复制按钮。

**Tech Stack:** 原生 HTML / CSS / JavaScript（无新依赖）

---

## 文件改动清单

```
console.html (修改)
  ├─ <style> 块末尾新增：
  │    - .onboarding-nav（粘性锚点导航条）
  │    - .code-block / .copy-btn（代码块 + 复制按钮）
  │    - .interface-card / details/summary（折叠卡片）
  │    - 章节标题 h3/h4/段落样式
  │
  ├─ <body> 中：
  │    - .tabs 容器内新增 <button data-panel="onboarding">
  │    - .container 内新增 <div id="onboarding" class="panel">
  │
  └─ <script> 末尾新增：
       - loadOnboarding() 函数
       - 锚点导航高亮的 IntersectionObserver
       - 复制按钮的事件委托
       - 章节切换时按需懒加载
```

---

## Task 1: 新增标签按钮与面板骨架

**Files:**
- Modify: `console.html` (`.tabs` 内与 `.container` 末尾)

- [ ] **Step 1: 在 `.tabs` 内新增"接入指南"按钮**

定位到第 78-80 行的 `<div class="tabs">` 块：

```html
        <div class="tabs">
            <button class="tab active" data-panel="status">上游状态</button>
            <button class="tab" data-panel="interfaces">接口列表</button>
            <button class="tab" data-panel="config">配置信息</button>
        </div>
```

在第 80 行 `</div>` 之前新增第 4 个按钮：

```html
        <div class="tabs">
            <button class="tab active" data-panel="status">上游状态</button>
            <button class="tab" data-panel="interfaces">接口列表</button>
            <button class="tab" data-panel="config">配置信息</button>
            <button class="tab" data-panel="onboarding">接入指南</button>
        </div>
```

- [ ] **Step 2: 在 `.container` 内、最后一个 `</div>` 之前新增面板容器**

定位到第 126 行 `<div id="config" class="panel">` 结束后的 `</div>` 之前（在第 127 行的 `</div>` 之前），新增：

```html
        <div id="onboarding" class="panel">
            <div class="card">
                <h2>接入指南</h2>
                <p style="color: var(--text-secondary);">UniHive MCP 统一网关，5 分钟接入金融数据。</p>
                <p style="margin-top: 12px;"><em>章节内容正在加载...</em></p>
            </div>
        </div>
```

- [ ] **Step 3: 验证标签切换已生效**

在浏览器中打开 `console.html`，点击新加的"接入指南"按钮。验证：
- 标签按钮显示为第 4 个
- 点击后切换到新面板
- 看到"接入指南"卡片标题

**预期:** 通过；新标签可点击，面板可切换。

- [ ] **Step 4: 提交**

```bash
git add console.html
git commit -m "feat(console): add onboarding tab placeholder"
```

---

## Task 2: 新增 CSS 样式（锚点导航、代码块、折叠卡片）

**Files:**
- Modify: `console.html` (`<style>` 块末尾，第 64 行后)

- [ ] **Step 1: 在 `</style>` 之前（第 65 行前）新增样式**

定位到第 64-65 行：

```css
        .latency.bad { color: var(--danger); }
    </style>
```

在 `.latency.bad` 之后、`</style>` 之前插入：

```css
        /* 接入指南 */
        .onboarding-nav {
            position: sticky;
            top: 0;
            z-index: 10;
            background: var(--card-bg);
            padding: 12px 20px;
            margin: -20px -20px 20px -20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            gap: 8px;
            font-size: 0.875rem;
            overflow-x: auto;
        }
        .onboarding-nav a {
            color: var(--text-secondary);
            text-decoration: none;
            padding: 4px 10px;
            border-radius: 4px;
            white-space: nowrap;
            transition: all 0.2s;
        }
        .onboarding-nav a:hover { background: var(--bg); color: var(--text); }
        .onboarding-nav a.active { color: var(--primary); background: rgba(13,110,253,0.08); }

        .onboarding-content h3 {
            font-size: 1.125rem;
            font-weight: 600;
            margin: 24px 0 12px 0;
            padding-bottom: 8px;
            border-bottom: 2px solid var(--primary);
        }
        .onboarding-content h4 {
            font-size: 0.9rem;
            font-weight: 600;
            margin: 16px 0 8px 0;
            color: var(--text);
        }
        .onboarding-content p {
            margin: 8px 0;
            color: var(--text);
        }
        .onboarding-content ul {
            margin: 8px 0 8px 24px;
            color: var(--text);
        }
        .onboarding-content li { margin: 4px 0; }

        .code-block {
            position: relative;
            background: var(--bg);
            padding: 16px;
            padding-top: 36px;
            border-radius: 6px;
            margin: 12px 0;
            border: 1px solid var(--border);
        }
        .code-block pre {
            margin: 0;
            overflow-x: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.8rem;
            line-height: 1.5;
            white-space: pre;
        }
        .code-block .copy-btn {
            position: absolute;
            top: 8px;
            right: 8px;
            padding: 3px 10px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 4px;
            font-size: 0.75rem;
            cursor: pointer;
            opacity: 0;
            transition: opacity 0.2s, background 0.2s, color 0.2s;
            color: var(--text-secondary);
        }
        .code-block:hover .copy-btn { opacity: 1; }
        .code-block .copy-btn:hover {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }
        .code-block .copy-btn.copied {
            background: var(--success);
            color: white;
            border-color: var(--success);
            opacity: 1;
        }
        .code-block .copy-btn:focus { opacity: 1; outline: 2px solid var(--primary); outline-offset: 1px; }

        .interface-card {
            border: 1px solid var(--border);
            border-radius: 6px;
            margin-bottom: 8px;
            background: var(--card-bg);
            overflow: hidden;
        }
        .interface-card > summary {
            padding: 12px 16px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            list-style: none;
            user-select: none;
        }
        .interface-card > summary::-webkit-details-marker { display: none; }
        .interface-card > summary::before {
            content: '▶';
            color: var(--text-secondary);
            font-size: 0.65rem;
            transition: transform 0.2s;
            flex-shrink: 0;
        }
        .interface-card[open] > summary::before { transform: rotate(90deg); }
        .interface-card > summary:hover { background: var(--bg); }
        .interface-card .interface-body {
            padding: 0 16px 16px;
            border-top: 1px solid var(--border);
        }
        .interface-card .interface-body .description {
            color: var(--text-secondary);
            font-size: 0.875rem;
            margin: 12px 0;
        }

        .loading-state {
            text-align: center;
            padding: 24px;
            color: var(--text-secondary);
            font-size: 0.875rem;
        }
        .retry-link {
            color: var(--primary);
            cursor: pointer;
            text-decoration: underline;
            margin-left: 8px;
        }
    </style>
```

- [ ] **Step 2: 验证 CSS 加载无报错**

在浏览器中打开 `console.html` → F12 → Console。验证：
- 无 CSS 解析错误
- 切换到"接入指南"标签页
- 由于还没有内容，页面看起来应该跟之前一样（空骨架）

**预期:** 通过；Console 无报错。

- [ ] **Step 3: 提交**

```bash
git add console.html
git commit -m "feat(console): add onboarding tab CSS styles"
```

---

## Task 3: 填充静态章节内容（HTML）

**Files:**
- Modify: `console.html` (`<div id="onboarding">` 内部)

- [ ] **Step 1: 用完整章节内容替换 Task 1 中新增的占位面板**

定位到 Task 1 中新增的 `<div id="onboarding" class="panel">` 块（包含 `<p><em>章节内容正在加载...</em></p>`），用以下内容整体替换：

```html
        <div id="onboarding" class="panel">
            <nav class="onboarding-nav" id="onboarding-nav">
                <a href="#sec-quickstart" data-target="sec-quickstart">快速开始</a>
                <a href="#sec-integration" data-target="sec-integration">接入方式</a>
                <a href="#sec-interfaces" data-target="sec-interfaces">接口列表</a>
                <a href="#sec-examples" data-target="sec-examples">常用示例</a>
                <a href="#sec-troubleshoot" data-target="sec-troubleshoot">故障排查</a>
            </nav>

            <div class="card">
                <div class="onboarding-content">

                    <h3 id="sec-quickstart">1. 快速开始</h3>
                    <p>UniHive MCP 是一个统一的金融数据网关，把通达信、同花顺、Tushare 等多个数据源聚合在同一个 MCP 接口后面。</p>

                    <h4>1.1 启动网关</h4>
                    <p>在项目根目录下运行：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>.\scripts\start_all.ps1</pre>
                    </div>
                    <p>启动后，控制台默认监听 <code>http://127.0.0.1:18080</code>，网关以 stdio 方式对外提供 MCP 接口。</p>

                    <h4>1.2 配置文件</h4>
                    <p>上游数据源在 <code>config/upstreams.yaml</code> 中配置。修改后需要重启网关生效。</p>

                    <h3 id="sec-integration">2. 接入方式</h3>

                    <h4>2.1 Stdio 模式（Claude Desktop / Cursor）</h4>
                    <p>在客户端的 MCP 配置中添加：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "mcp_data_gateway.gateway_server"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}</pre>
                    </div>

                    <h4>2.2 HTTP 模式（自定义客户端）</h4>
                    <p>控制台 HTTP 服务暴露了接口元信息查询端点，可用于发现可用工具：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>curl http://127.0.0.1:18080/api/interfaces</pre>
                    </div>

                    <h3 id="sec-interfaces">3. 接口列表</h3>
                    <p>以下接口由当前网关动态发现，<strong>与你实际启用的上游保持一致</strong>。</p>
                    <div id="interfaces-list">
                        <div class="loading-state">正在加载接口列表...</div>
                    </div>

                    <h3 id="sec-examples">4. 常用示例</h3>

                    <h4>4.1 获取实时行情</h4>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_data_gateway.gateway_server"],
        cwd="D:/fintech_workspace/unihive"
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_realtime_quote",
                {"symbol": "600000"}
            )
            print(result.content[0].text)

asyncio.run(main())</pre>
                    </div>

                    <h4>4.2 获取日 K 线</h4>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>result = await session.call_tool(
    "get_daily_bar",
    {"symbol": "600000", "start_date": "20260101", "end_date": "20260301"}
)</pre>
                    </div>

                    <h4>4.3 搜索股票</h4>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>result = await session.call_tool(
    "search_stock",
    {"keyword": "茅台"}
)</pre>
                    </div>

                    <h3 id="sec-troubleshoot">5. 故障排查</h3>

                    <h4>5.1 连接失败</h4>
                    <ul>
                        <li>检查网关是否已启动：访问 <code>http://127.0.0.1:18080</code></li>
                        <li>检查客户端配置中的 <code>cwd</code> 路径是否正确</li>
                        <li>Windows 下确认 Python 已加入 PATH</li>
                    </ul>

                    <h4>5.2 接口返回错误</h4>
                    <ul>
                        <li>查看控制台"上游状态"标签页，确认对应上游是否在线</li>
                        <li>查看返回的 <code>last_error</code> 字段</li>
                        <li>对应上游若被禁用，需修改 <code>config/upstreams.yaml</code> 并重启</li>
                    </ul>

                    <h4>5.3 参数错误</h4>
                    <ul>
                        <li>股票代码需要标准化（参见 <code>src/normalizer.py</code>）</li>
                        <li>日期格式统一为 <code>YYYYMMDD</code></li>
                    </ul>

                    <h4>5.4 反馈渠道</h4>
                    <p>遇到问题请提交 Issue 到项目仓库，并附上控制台截图与接口返回内容。</p>

                </div>
            </div>
        </div>
```

- [ ] **Step 2: 验证静态内容渲染**

在浏览器中打开 `console.html`，切换到"接入指南"标签页。验证：
- 顶部出现 5 个锚点链接
- 5 个章节（h3）依次展示
- 至少看到 5 个代码块
- 代码块顶部预留了 36px 空间（为复制按钮预留位置）

**预期:** 通过；静态内容完整渲染。

- [ ] **Step 3: 提交**

```bash
git add console.html
git commit -m "feat(console): populate onboarding tab static content"
```

---

## Task 4: 实现接口列表动态加载

**Files:**
- Modify: `console.html` (`<script>` 块末尾，第 230 行 `</script>` 之前)

- [ ] **Step 1: 在 `</script>` 之前新增 `loadOnboarding` 函数**

定位到第 229-230 行：

```javascript
        refreshAll();
        startAutoRefresh();
    </script>
```

在 `startAutoRefresh();` 之后、`</script>` 之前插入：

```javascript
        async function loadOnboarding() {
            const container = document.getElementById('interfaces-list');
            try {
                const data = await fetchJSON('/api/interfaces');
                const tools = data.tools || [];
                if (tools.length === 0) {
                    container.innerHTML = '<div class="loading-state">暂无接口配置</div>';
                    return;
                }
                container.innerHTML = tools.map(tool => `
                    <details class="interface-card">
                        <summary>
                            <span class="tool-name">${escapeHtml(tool.name)}</span>
                            <span class="chain">${escapeHtml(tool.source || '-')}</span>
                        </summary>
                        <div class="interface-body">
                            <p class="description">${escapeHtml(tool.description || '暂无描述')}</p>
                            <h4>调用示例</h4>
                            <div class="code-block">
                                <button class="copy-btn">复制</button>
                                <pre>result = await session.call_tool(
    "${escapeHtml(tool.name)}",
    { /* 参数见上游文档 */ }
)</pre>
                            </div>
                        </div>
                    </details>
                `).join('');
            } catch (e) {
                console.error('Load interfaces error:', e);
                container.innerHTML = '<div class="loading-state">加载失败<span class="retry-link" onclick="loadOnboarding()">点击重试</span></div>';
            }
        }

        function escapeHtml(str) {
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }
```

- [ ] **Step 2: 在 `refreshAll()` 中加入 `loadOnboarding()`**

定位到 `refreshAll()` 函数（第 215-221 行），在末尾添加调用：

```javascript
        function refreshAll() {
            countdown = 10;
            document.getElementById('countdown').textContent = countdown;
            loadStatus();
            loadInterfaces();
            loadConfig();
        }
```

修改为：

```javascript
        function refreshAll() {
            countdown = 10;
            document.getElementById('countdown').textContent = countdown;
            loadStatus();
            loadInterfaces();
            loadConfig();
            loadOnboarding();
        }
```

- [ ] **Step 3: 验证接口列表动态加载**

在浏览器中打开 `console.html`，切换到"接入指南"标签页。验证：
- 滚动到"3. 接口列表"章节
- 接口卡片数量与"接口列表"标签页的表格行数一致
- 点击接口卡片可展开/收起
- 展开后看到描述与示例代码
- 故意停掉控制台后端服务，刷新页面，应看到"加载失败，点击重试"

**预期:** 通过；接口动态渲染、折叠交互、错误降级都生效。

- [ ] **Step 4: 提交**

```bash
git add console.html
git commit -m "feat(console): dynamic interface list with collapsible cards"
```

---

## Task 5: 实现代码复制按钮（事件委托）

**Files:**
- Modify: `console.html` (`<script>` 块末尾)

- [ ] **Step 1: 在 `loadOnboarding()` 函数之后新增复制按钮的事件绑定**

定位到 `escapeHtml` 函数之后（Task 4 末尾），在 `</script>` 之前插入：

```javascript
        document.getElementById('onboarding').addEventListener('click', (e) => {
            if (!e.target.classList.contains('copy-btn')) return;
            const btn = e.target;
            const pre = btn.nextElementSibling;
            if (!pre || pre.tagName !== 'PRE') return;
            const text = pre.textContent;
            if (!navigator.clipboard) {
                btn.textContent = '浏览器不支持';
                setTimeout(() => { btn.textContent = '复制'; }, 2000);
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                btn.textContent = '已复制';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = '复制';
                    btn.classList.remove('copied');
                }, 2000);
            }).catch(() => {
                btn.textContent = '复制失败';
                setTimeout(() => { btn.textContent = '复制'; }, 2000);
            });
        });
```

- [ ] **Step 2: 验证复制功能**

在浏览器中打开 `console.html` → 切换到"接入指南"。验证：
- 鼠标 hover 到任意代码块，右上角出现"复制"按钮
- 点击"复制"按钮 → 按钮变绿显示"已复制" → 2 秒后恢复
- 打开记事本粘贴，验证内容是代码块里的纯文本（无 HTML 标签）

**预期:** 通过；复制成功，文案切换正确。

- [ ] **Step 3: 提交**

```bash
git add console.html
git commit -m "feat(console): copy-to-clipboard for code blocks"
```

---

## Task 6: 实现锚点导航高亮（IntersectionObserver）

**Files:**
- Modify: `console.html` (`<script>` 块末尾)

- [ ] **Step 1: 在复制按钮事件绑定之后，新增锚点导航高亮逻辑**

在 `</script>` 之前、Task 5 代码之后插入：

```javascript
        (function setupOnboardingNav() {
            const navLinks = document.querySelectorAll('#onboarding-nav a');
            const sections = Array.from(navLinks).map(a => document.getElementById(a.dataset.target)).filter(Boolean);
            if (sections.length === 0) return;

            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        navLinks.forEach(l => l.classList.remove('active'));
                        const link = document.querySelector(`#onboarding-nav a[data-target="${entry.target.id}"]`);
                        if (link) link.classList.add('active');
                    }
                });
            }, { rootMargin: '-20% 0px -70% 0px' });

            sections.forEach(s => observer.observe(s));
        })();
```

- [ ] **Step 2: 验证锚点高亮跟随滚动**

在浏览器中打开 `console.html` → 切换到"接入指南"。验证：
- 缓慢向下滚动页面
- 顶部锚点导航的"激活态"高亮跟随当前章节切换
- 点击锚点链接，页面平滑滚动到对应章节
- 滚动到第 5 章"故障排查"时，"故障排查"链接应处于高亮状态

**预期:** 通过；锚点高亮跟随滚动，激活态准确。

- [ ] **Step 3: 提交**

```bash
git add console.html
git commit -m "feat(console): scroll-spy active state for onboarding nav"
```

---

## Task 7: 最终验收

**Files:**
- Modify: 无（仅验证）

- [ ] **Step 1: 启动控制台后端**

```bash
.\scripts\start_console.ps1
```

确认 `http://127.0.0.1:18080` 可访问。

- [ ] **Step 2: 浏览器验收清单**

打开 `http://127.0.0.1:18080/console.html`，对照验收清单逐项确认：

- [ ] 标签按钮"接入指南"在第 4 个位置显示
- [ ] 点击切换正常（与其他标签页行为一致）
- [ ] 锚点导航条 sticky 在顶部，点击可平滑滚动
- [ ] 滚动时锚点导航自动高亮当前章节
- [ ] 接口列表与 `/api/interfaces` 实时数据一致
- [ ] 代码块 hover 显示复制按钮，点击变绿显示"已复制"
- [ ] 接口折叠卡片可展开/收起
- [ ] 视觉风格与现有 3 个标签页保持一致
- [ ] 现有 3 个标签页行为未受影响

**预期:** 全部通过。如有任何一项失败，回到对应 Task 排查修复。

- [ ] **Step 3: 响应式抽查**

按 F12 → 设备模拟器，分别切到 1280×800、768×1024、375×667 三种宽度：

- 1280px：所有内容正常展示
- 768px：横向无溢出
- 375px：代码块可横向滚动，锚点导航可横向滚动

**预期:** 通过；不破版。

- [ ] **Step 4: 清理与最终提交**

确认无调试代码残留（无 `console.log` 调试输出，无 TODO 注释）。

```bash
git status
```

**预期:** 工作目录干净，最后一次提交是 Task 6 的 `scroll-spy` 提交。

---

## 自审结果

**1. Spec 覆盖度:**
- 标签按钮 → Task 1 ✓
- 锚点导航 → Task 6 ✓
- 章节静态内容 → Task 3 ✓
- 接口列表动态 → Task 4 ✓
- 代码复制按钮 → Task 5 ✓
- 折叠卡片 → Task 4 ✓
- 事件委托（设计文档 §4） → Task 5 ✓
- IntersectionObserver（设计文档 §3.3） → Task 6 ✓
- 错误处理降级 → Task 4 ✓
- 视觉一致性 → Task 2 ✓
- 验收清单 → Task 7 ✓

**2. 占位符扫描:** 无 TBD / TODO / "implement later"。

**3. 类型/命名一致性:**
- 复制按钮 class `.copy-btn` 一致
- 接口卡片 class `.interface-card` 一致
- 锚点 data-target `sec-*` 在 HTML 和 JS 中一致
- `loadOnboarding()` 函数名在 Task 4、5、6、7 中一致
- `escapeHtml()` 仅在 Task 4 定义并使用
