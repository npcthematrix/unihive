# UniHive Console 接入指南标签页设计

**日期:** 2026-09-03
**状态:** 待审阅
**目标文件:** `console.html`

## 1. 背景与目标

UniHive Console 当前有 3 个标签页：上游状态、接口列表、配置信息。缺少一个面向首次接入用户的引导页。

**目标:** 新增"接入指南"标签页，帮助开发者与量化研究者在 5 分钟内完成与 UniHive MCP 的集成。

**核心原则:**
- 沿用现有 console 的极简风格，零突兀感
- 数据保持与实际配置同步（动态接口列表）
- 静态示例代码 + 动态接口元信息混合

## 2. 用户与场景

| 角色 | 使用场景 | 主要诉求 |
|------|---------|---------|
| 开发者 | 把 UniHive MCP 集成到 Claude/Cursor/自研 Agent | 配置文件怎么写、stdio/HTTP 怎么选 |
| 量化研究者 | 用 Python/Node.js 调数据 | 调哪个接口、参数怎么传、返回啥结构 |

## 3. 设计方案（方案 A：极简一致风）

### 3.1 标签页入口

在现有 `.tabs` 容器内新增一个按钮，紧跟"配置信息"之后：

```html
<button class="tab" data-panel="onboarding">接入指南</button>
```

### 3.2 面板结构

```
┌─────────────────────────────────────────────┐
│  [锚点导航条 - sticky top]                   │
│  快速开始 · 接口列表 · 常用示例 · 故障排查    │
├─────────────────────────────────────────────┤
│  章节 1: 快速开始                            │
│    1.1 什么是 UniHive MCP                    │
│    1.2 启动网关                              │
│    1.3 配置文件位置                          │
├─────────────────────────────────────────────┤
│  章节 2: 接入方式                            │
│    2.1 Stdio 模式（Claude Desktop / Cursor） │
│    2.2 HTTP 模式（自定义客户端）             │
├─────────────────────────────────────────────┤
│  章节 3: 接口列表（动态渲染）                │
│    - 从 /api/interfaces 拉取                 │
│    - 每个接口是一个 <details> 折叠卡片        │
│    - 展开后展示：描述、数据源、Python 示例    │
├─────────────────────────────────────────────┤
│  章节 4: 常用示例                            │
│    - 获取实时行情                             │
│    - 获取日 K 线                             │
│    - 搜索股票                                │
├─────────────────────────────────────────────┤
│  章节 5: 故障排查                            │
│    - 常见错误码                              │
│    - 调试技巧                                │
│    - 反馈渠道                                │
└─────────────────────────────────────────────┘
```

### 3.3 锚点导航条

页面滚动到顶部时固定在 viewport 顶部，使用现有 `.tabs` 的视觉风格但层级更高：

```css
.onboarding-nav {
    position: sticky;
    top: 0;
    z-index: 10;
    background: var(--card-bg);
    padding: 12px 20px;
    margin: -20px -20px 20px -20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 16px;
    font-size: 0.875rem;
}
.onboarding-nav a {
    color: var(--text-secondary);
    text-decoration: none;
    padding: 4px 8px;
    border-radius: 4px;
    transition: all 0.2s;
}
.onboarding-nav a:hover { background: var(--bg); color: var(--text); }
.onboarding-nav a.active { color: var(--primary); background: rgba(13,110,253,0.08); }
```

**激活态逻辑:** 滚动时根据 `IntersectionObserver` 自动高亮当前章节。

### 3.4 代码块样式

沿用现有 `.json-block` 的浅灰底，但加上复制按钮：

```css
.code-block {
    position: relative;
    background: var(--bg);
    padding: 16px;
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
}
.code-block .copy-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    padding: 4px 10px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 0.75rem;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.2s;
}
.code-block:hover .copy-btn { opacity: 1; }
.code-block .copy-btn:hover { background: var(--primary); color: white; border-color: var(--primary); }
.code-block .copy-btn.copied { background: var(--success); color: white; border-color: var(--success); }
```

**复制行为:**
- 点击 → 写入剪贴板 → 按钮变绿显示"已复制" → 2 秒后恢复
- 使用 `navigator.clipboard.writeText()`（HTTPS 或 localhost 才可用，符合本项目场景）

### 3.5 折叠接口卡片

```html
<details class="interface-card">
    <summary>
        <span class="tool-name">get_realtime_quote</span>
        <span class="chain">TDX, RHTHS</span>
    </summary>
    <div class="interface-body">
        <p class="description">获取股票实时行情</p>
        <h4>参数</h4>
        <div class="code-block">
            <button class="copy-btn">复制</button>
            <pre>{ "symbol": "600000" }</pre>
        </div>
        <h4>Python 示例</h4>
        <div class="code-block">
            <button class="copy-btn">复制</button>
            <pre>import asyncio
from mcp import ClientSession
...</pre>
        </div>
    </div>
</details>
```

折叠卡片样式：

```css
.interface-card {
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 8px;
    background: var(--card-bg);
}
.interface-card summary {
    padding: 12px 16px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    list-style: none;
}
.interface-card summary::-webkit-details-marker { display: none; }
.interface-card summary::before {
    content: '▶';
    margin-right: 8px;
    color: var(--text-secondary);
    transition: transform 0.2s;
    font-size: 0.7rem;
}
.interface-card[open] summary::before { transform: rotate(90deg); }
.interface-card .interface-body { padding: 0 16px 16px; }
```

### 3.6 静态章节内容

只列出每节要展示的内容要点，**具体文案在实现时填充**（避免设计文档膨胀）：

| 章节 | 内容 |
|------|------|
| 1. 快速开始 | UniHive MCP 简介（一句话）、启动命令、配置文件路径 |
| 2. 接入方式 | Stdio 配置 JSON 片段、HTTP 配置示例（指向控制台地址） |
| 3. 接口列表 | 动态渲染，初始为空时显示"等待加载"提示 |
| 4. 常用示例 | 3 个典型场景的完整 Python 脚本（行情/K线/搜索） |
| 5. 故障排查 | 常见错误含义（连接失败、接口超时、参数错误）、建议操作 |

## 4. 数据流

```
页面加载
  ↓
loadOnboarding()
  ├─ 静态章节内容直接渲染到 HTML
  └─ fetch /api/interfaces
       ↓
     渲染 <details> 列表（每张卡片一个接口）
       ↓
     为所有 .copy-btn 绑定 click 事件（事件委托）
```

**事件委托（避免 N 次绑定）:**

```javascript
document.getElementById('onboarding').addEventListener('click', (e) => {
    if (e.target.classList.contains('copy-btn')) {
        const code = e.target.nextElementSibling.textContent;
        navigator.clipboard.writeText(code).then(() => {
            e.target.textContent = '已复制';
            e.target.classList.add('copied');
            setTimeout(() => {
                e.target.textContent = '复制';
                e.target.classList.remove('copied');
            }, 2000);
        });
    }
});
```

**与现有自动刷新的关系:** 接入指南是静态文档，不需要每 10 秒刷新。只有在用户切换到该标签页时调用一次 `loadOnboarding()`。

## 5. 与现有风格的协调

| 元素 | 处理方式 |
|------|---------|
| 字体、配色、圆角、间距 | 全部沿用现有 CSS 变量 |
| 卡片样式 | 复用 `.card` 类 |
| 表格（如果有） | 复用现有 `<table>` 样式 |
| 状态点（如果有） | 复用 `.status-dot` 系列 |
| 不引入任何新字体或外部依赖 | ✓ |

## 6. 错误处理

| 场景 | 表现 |
|------|------|
| `/api/interfaces` 加载失败 | 折叠区显示"加载失败，点击重试" |
| `navigator.clipboard` 不可用 | 复制按钮隐藏，不报错 |
| 用户浏览器过老不支持 `<details>` | 不处理（所有现代浏览器都支持） |

## 7. 测试策略

由于这是一个纯前端静态文件改动，测试方式为：

1. **手动浏览器验证（必做）:**
   - 打开控制台，点击"接入指南"标签页
   - 验证 5 个章节全部渲染
   - 验证接口列表从 API 拉取（接口数量与"接口列表"标签页一致）
   - 验证点击代码块复制按钮 → 剪贴板有内容 → 按钮变绿
   - 验证滚动时锚点导航高亮跟随
   - 验证 hover 代码块时复制按钮浮现
   - 验证折叠接口可展开/收起

2. **响应式抽查:**
   - 1280px 桌面：正常
   - 768px 平板：横向不溢出
   - 375px 手机：代码块横向滚动可读

3. **不做自动化测试:** console.html 是单文件原型，引入测试框架成本太高

## 8. 范围与不做的事

**不做（避免范围蔓延）:**
- 不做深色模式（现有 console 也未做）
- 不做国际化（仅中文）
- 不做搜索功能（章节数 ≤ 5）
- 不做接口参数表单生成器
- 不接入后端 API 提供"在线试用"功能
- 不修改现有 3 个标签页的任何逻辑

## 9. 文件改动清单

仅修改一个文件：

```
console.html
  ├─ <style> 新增：
  │    - .onboarding-nav
  │    - .code-block / .copy-btn
  │    - .interface-card
  │    - 章节标题样式（h3, h4）
  │
  ├─ <body> 新增：
  │    - 标签按钮 <button data-panel="onboarding">
  │    - 面板 <div id="onboarding"> 含全部 5 个章节
  │
  └─ <script> 新增：
       - loadOnboarding() 函数
       - 锚点导航高亮的 IntersectionObserver
       - 复制按钮的事件委托
```

## 10. 验收清单

- [ ] 标签按钮"接入指南"在第 4 个位置显示
- [ ] 点击切换正常（与其他标签页行为一致）
- [ ] 锚点导航条点击可平滑滚动到对应章节
- [ ] 滚动时锚点导航自动高亮当前章节
- [ ] 接口列表与 `/api/interfaces` 实时数据一致
- [ ] 代码块 hover 显示复制按钮，点击后变绿显示"已复制"
- [ ] 接口折叠卡片可正常展开/收起
- [ ] 视觉风格与现有 3 个标签页保持一致（无突兀感）
- [ ] 在 1280/768/375 三种宽度下不破版
