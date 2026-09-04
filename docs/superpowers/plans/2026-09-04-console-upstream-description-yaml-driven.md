# Console 上游描述迁 YAML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 console.html onboarding 标签的上游描述从硬编码 GROUP_META 字典迁移到 `config/upstreams.yaml` 的 `description` 字段，GROUP_META 保留作为兜底。

**Architecture:** YAML 是单一来源；console_server 透传 `description` 到 `/api/upstreams` 响应；console.html onboarding 渲染时优先读 upstream.description，兜底到 GROUP_META。

**Tech Stack:** Python (pytest + yaml) + console.html (vanilla JS + fetch)

---

## File Structure

| File | 改动 |
|---|---|
| `config/upstreams.yaml` | 8 个 upstream 块各加 1 个 `description` 字段 |
| `src/console_server.py` | `_probe_upstream` result dict 加 1 行 |
| `console.html` | `loadOnboarding` 函数改写：并行 fetch + descByName 优先级查找 |
| `tests/test_console_interfaces.py` | 加 2 个新测试 |

---

### Task 1: Add description field to 8 upstream blocks

**Files:**
- Modify: `config/upstreams.yaml:5-133`

- [ ] **Step 1: Add `description` field to `tdx_local` block (after `enabled: true` on line 6)**

在 `tdx_local:` 块里 `enabled: true` 之后插入：

```yaml
  description: "通达信-行情: 行情/K线/分时/ETF/指数, 含 1 个高风险工具 (tdx_call), 39 个 gateway 工具"
```

最终结构：

```yaml
tdx_local:
  enabled: true
  description: "通达信-行情: 行情/K线/分时/ETF/指数, 含 1 个高风险工具 (tdx_call), 39 个 gateway 工具"
  type: "npx"
  ...
```

- [ ] **Step 2: Add `description` field to `tdx_tq_local` block (after `enabled: true` on line 24)**

在 `tdx_tq_local:` 块里 `enabled: true` 之后插入：

```yaml
  description: "通达信-交易终端: 行情/自选/交易/公式系统, 含 17 个高风险工具 (write ops), 58 个 gateway 工具 (TQ-Local codegen)"
```

- [ ] **Step 3: Add `description` field to `tokenwave_tdx` block (after `enabled: true` on line 118)**

在 `tokenwave_tdx:` 块里 `enabled: true` 之后插入：

```yaml
  description: "通达信-MooTDX: 基于 mootdx 的行情/K线/财务/板块/日历, 9 个 gateway 接口 (含 2 个 tokenwave_ 前缀命名避开 TQ-Local 冲突), local 优先 + network 兜底"
```

- [ ] **Step 4: Add `description` field to `fuyao_ashare` block (after `enabled: true` on line 36)**

在 `fuyao_ashare:` 块里 `enabled: true` 之后插入：

```yaml
  description: "同花顺-a-share-mcp: A 股行情/财报/估值/特殊数据, 28 个 gateway 工具, 端点: /mcp/a-share"
```

- [ ] **Step 5: Add `description` field to `fuyao_index` block (after `enabled: true` on line 57)**

在 `fuyao_index:` 块里 `enabled: true` 之后插入：

```yaml
  description: "同花顺-a-share-index-mcp: 指数/板块目录/成分股/历史K线, 7 个 gateway 工具, 端点: /mcp/a-share-index"
```

- [ ] **Step 6: Add `description` field to `fuyao_meta` block (after `enabled: true` on line 72)**

在 `fuyao_meta:` 块里 `enabled: true` 之后插入：

```yaml
  description: "同花顺-meta-mcp: 跨市场 ticker 搜索/列表, 2 个 gateway 工具, 端点: /mcp/meta"
```

- [ ] **Step 7: Add `description` field to `fuyao_fund` block (after `enabled: true` on line 85)**

在 `fuyao_fund:` 块里 `enabled: true` 之后插入：

```yaml
  description: "同花顺-fund-mcp: 公募基金持仓/业绩/经理/财务/历史, 30 个 gateway 工具, 端点: /mcp/fund"
```

- [ ] **Step 8: Add `description` field to `tushare` block (after `enabled: true` on line 103)**

在 `tushare:` 块里 `enabled: true` 之后插入：

```yaml
  description: "Tushare: 通用 API 透传 (sdk_call / sdk_schema / sdk_search), 3 个 gateway 工具, 含财务/公告/通用"
```

- [ ] **Step 9: Verify YAML still parses**

Run: `cd D:/fintech_workspace/unihive && python -c "import yaml; yaml.safe_load(open('config/upstreams.yaml', encoding='utf-8'))"`
Expected: 静默退出 (YAML 解析成功)

- [ ] **Step 10: Commit**

```bash
cd D:/fintech_workspace/unihive
git add config/upstreams.yaml
git commit -m "chore(config): add description field to 8 upstream blocks"
```

---

### Task 2: Pass description through /api/upstreams response

**Files:**
- Modify: `src/console_server.py:118-124` (`_probe_upstream` result dict)
- Test: `tests/test_console_interfaces.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_console_interfaces.py`:

```python
def test_get_upstreams_includes_description():
    """Contract: /api/upstreams response includes description field per upstream.

    Each upstream in config/upstreams.yaml must declare a non-empty description,
    which /api/upstreams proxies to the console for rendering.
    """
    import yaml
    from src.console_server import get_upstream_status

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for name, cfg in config.get("upstreams", {}).items():
        assert "description" in cfg, f"upstream {name} missing description field"
        assert cfg["description"].strip(), f"upstream {name} description is empty"

    result = get_upstream_status()
    for name, info in result.get("upstreams", {}).items():
        assert "description" in info, f"{name} response missing description"
        assert info["description"].strip(), f"{name} description is empty in response"
```

- [ ] **Step 2: Run test to verify it FAILS**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/test_console_interfaces.py::test_get_upstreams_includes_description -v`
Expected: FAIL with `"upstream tdx_local response missing description"` (或类似 — 因为 `_probe_upstream` 还没透传字段)

- [ ] **Step 3: Add `description` to `_probe_upstream` result**

修改 `src/console_server.py:118-124`：

```python
    upstream_type = cfg.get("type", "")
    base_url = cfg.get("base_url", "")
    result = {
        "enabled": cfg.get("enabled", False),
        "type": upstream_type,
        "description": cfg.get("description", ""),
        "status": "unknown",
        "latency_ms": None,
        "last_error": None,
    }
```

（仅新增一行 `"description": cfg.get("description", ""),` 在 `"type": upstream_type,` 之后）

- [ ] **Step 4: Run test to verify it PASSES**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/test_console_interfaces.py::test_get_upstreams_includes_description -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify no regression**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/ -q`
Expected: 143 passed (142 baseline + 1 new)

- [ ] **Step 6: Commit**

```bash
cd D:/fintech_workspace/unihive
git add src/console_server.py tests/test_console_interfaces.py
git commit -m "feat(server): pass upstream description through /api/upstreams + add regression test"
```

---

### Task 3: Render description in console.html onboarding tab

**Files:**
- Modify: `console.html:1690-1717` (`loadOnboarding` 函数)
- Test: `tests/test_console_interfaces.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_console_interfaces.py`:

```python
def test_console_html_uses_upstream_description():
    """Regression: console.html onboarding must fetch /api/upstreams and prefer description.

    Without this, onboarding cards fall back to GROUP_META forever — re-introducing
    the rot problem this spec solved.
    """
    html_path = "console.html"
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    assert "/api/upstreams" in html, "console.html must fetch /api/upstreams"
    assert "descByName" in html, "console.html must build descByName map from upstream description"
```

- [ ] **Step 2: Run test to verify it FAILS**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/test_console_interfaces.py::test_console_html_uses_upstream_description -v`
Expected: FAIL with `"console.html must fetch /api/upstreams"` (因为 `loadOnboarding` 当前只 fetch `/api/interfaces`)

- [ ] **Step 3: Modify `loadOnboarding` to fetch `/api/upstreams` and use description**

替换 `console.html` 第 1690-1717 行的整个 `loadOnboarding` 函数：

```javascript
        async function loadOnboarding() {
            const container = document.getElementById('interfaces-summary');
            const totalEl = document.getElementById('ifs-total-count');
            try {
                const [ifsData, upsData] = await Promise.all([
                    fetchJSON('/api/interfaces'),
                    fetchJSON('/api/upstreams'),
                ]);
                const tools = ifsData.tools || [];
                const descByName = {};
                for (const u of (upsData.upstreams || [])) {
                    if (u && u._name) descByName[u._name] = u.description || '';
                }
                if (totalEl) totalEl.textContent = tools.length;
                if (tools.length === 0) {
                    container.innerHTML = '<div class="loading-state" style="grid-column:1/-1">暂无接口配置</div>';
                    return;
                }
                const counts = new Map();
                for (const t of tools) {
                    const k = primarySource(t.source) || '其他';
                    counts.set(k, (counts.get(k) || 0) + 1);
                }
                const groups = [ALL_KEY, ...GROUP_ORDER].filter(k => k === ALL_KEY || counts.has(k));
                container.innerHTML = groups.map(key => {
                    const meta = key === ALL_KEY ? { title: '全部接口', desc: '一次性查看所有上游的 tool' } : (GROUP_META[key] || { title: key, desc: '' });
                    const count = key === ALL_KEY ? tools.length : (counts.get(key) || 0);
                    const desc = (key !== ALL_KEY && descByName[key]) || meta.desc || '';
                    return `<a href="#" data-jump-panel="interfaces" data-jump-group="${escapeHtml(key)}"><span><strong>${escapeHtml(meta.title)}</strong><br><small style="color:var(--text-secondary)">${escapeHtml(desc)}</small></span><span><span class="count">${count}</span> <span class="arrow">→</span></span></a>`;
                }).join('');
            } catch (e) {
                console.error('Load interfaces error:', e);
                if (totalEl) totalEl.textContent = '加载失败';
                container.innerHTML = `<div class="loading-state" style="grid-column:1/-1">加载失败 <span class="retry-link" onclick="loadOnboarding()">点击重试</span></div>`;
            }
        }
```

关键改动（4 处）：
1. `const data = await fetchJSON('/api/interfaces');` → `const [ifsData, upsData] = await Promise.all([fetchJSON('/api/interfaces'), fetchJSON('/api/upstreams')]);`
2. 新增 `const descByName = {}; for (const [name, info] of Object.entries(upsData.upstreams || {})) { descByName[name] = info.description || ''; }` — 用 `Object.entries` 把 `{name: info}` 字典拆成 `[name, info]` 对（与 line 960 现有 `statusUpstreams` 转换模式一致），name 直接做 descByName 的 key，无需 `_name` 中转
3. `const tools = data.tools || [];` → `const tools = ifsData.tools || [];`
4. 在 `groups.map` 渲染里新增 `const desc = (key !== ALL_KEY && descByName[key]) || meta.desc || '';` 并替换 `${escapeHtml(meta.desc)}` → `${escapeHtml(desc)}`

- [ ] **Step 4: Run test to verify it PASSES**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/test_console_interfaces.py::test_console_html_uses_upstream_description -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify no regression**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/ -q`
Expected: 144 passed (143 from Task 2 + 1 new)

- [ ] **Step 6: Commit**

```bash
cd D:/fintech_workspace/unihive
git add console.html tests/test_console_interfaces.py
git commit -m "feat(console): prefer upstream description from /api/upstreams over GROUP_META"
```

---

### Task 4: Manual smoke test in browser

**Files:** (no file changes — manual verification)

- [ ] **Step 1: Start console server**

Run: `cd D:/fintech_workspace/unihive && python -m src.console_server` (后台)
Expected: console server starts on port 18080

- [ ] **Step 2: Open browser and verify**

Open: `http://127.0.0.1:18080`
Click: 「接入指南」tab
Expected: 每个上游卡片的小字描述来自 YAML（如 "通达信-MooTDX: 基于 mootdx 的行情/K线/财务/板块/日历, 9 个 gateway 接口 ..."），而不是旧 GROUP_META 字符串

- [ ] **Step 3: Stop console server**

Run: kill the background python process

- [ ] **Step 4: Confirm acceptance**

Run: `cd D:/fintech_workspace/unihive && python -m pytest tests/ -q`
Expected: 144 passed, 1 warning

- [ ] **Step 5: Final commit (no-op if all committed; else commit any review fix)**

If changes: `cd D:/fintech_workspace/unihive && git commit -am "chore: review fixes"`
Else: skip.

**Note**: 兜底路径（YAML 缺 description → 用 GROUP_META）通过 Task 2 的 `cfg.get("description", "")` 默认值 + Task 3 的 `descByName[key] || meta.desc || ''` 三层兜底保证，已由 unit test 覆盖 (`assert info["description"].strip()` 拦截空字符串)。无需在浏览器手动验证兜底。
