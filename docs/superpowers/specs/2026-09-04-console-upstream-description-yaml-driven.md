# Console 上游描述迁 YAML — Design

## Problem

`console.html` 第 1005-1014 行的 `GROUP_META` 是手工写死的 8 行字典，包含每个 upstream 的 `title` + `desc` 字符串。这是 onboarding 标签侧栏展示上游卡片时的唯一描述来源。

**问题**：
1. 描述**不跟随 YAML 变更** — 上一轮 tokenwave_tdx 暴露 9 个工具 + rename 2 个 tokenwave_ 前缀，描述里 "8 个接口" 第一次写错（commit `7addfb8`），第二次靠 commit `d147390` 才补上 "9 个接口"
2. 描述和实际 capabilities 漂移 — 例如 `fuyao_meta` 描述说 "跨宇宙全量 ticker 列表"，"跨宇宙" 是 slang，不准确
3. gateway 工具数字硬编码 — 描述里 "X 个 gateway 工具" 这种数字需要维护者手算（如 tokenwave_tdx 实际 11 个 mapping+chain 条目，但 tokenwave 真正路由的是 9 个，要算清楚不容易）

## Solution

把描述**单一来源**迁到 `config/upstreams.yaml`，每个 upstream 块加 `description` 字段。console.html 渲染时优先读 YAML，HTML 的 GROUP_META 作为兜底（方案 B）。

## Scope

**In scope:**
- `config/upstreams.yaml` 8 个 upstream 块加 `description` 字段
- `src/console_server.py._probe_upstream` 透传 `description` 到 `/api/upstreams` 响应
- `console.html` onboarding 渲染逻辑：优先读 `/api/upstreams` 响应里的 `description`，兜底到 `GROUP_META[key].desc`
- `tests/test_console_interfaces.py` 加 `test_get_upstreams_includes_description`

**Out of scope:**
- 删 `GROUP_META`（保留兜底，后续按需逐个迁移）
- `console.html` 其它标签页（status / interfaces / config 的数据已自动从 `/api/*` 加载，不涉及静态描述）
- 上游 YAML 字段以外的配置（如 capabilities 列表展示）

## Implementation

### A. `config/upstreams.yaml`

每个 upstream 块加 `description` 字段（按现状 capabilities + GROUP_META 字符串的合并修订）：

```yaml
tdx_local:
  enabled: true
  description: "通达信-行情: 行情/K线/分时/ETF/指数, 含 1 个高风险工具 (tdx_call), 39 个 gateway 工具"
  ...

tdx_tq_local:
  description: "通达信-交易终端: 行情/自选/交易/公式系统, 含 17 个高风险工具 (write ops), 58 个 gateway 工具 (TQ-Local codegen)"
  ...

tokenwave_tdx:
  description: "通达信-MooTDX: 基于 mootdx 的行情/K线/财务/板块/日历, 9 个 gateway 接口 (含 2 个 tokenwave_ 前缀命名避开 TQ-Local 冲突), local 优先 + network 兜底"
  ...

fuyao_ashare:
  description: "同花顺-a-share-mcp: A 股行情/财报/估值/特殊数据, 28 个 gateway 工具, 端点: /mcp/a-share"
  ...

fuyao_index:
  description: "同花顺-a-share-index-mcp: 指数/板块目录/成分股/历史K线, 7 个 gateway 工具, 端点: /mcp/a-share-index"
  ...

fuyao_meta:
  description: "同花顺-meta-mcp: 跨市场 ticker 搜索/列表, 2 个 gateway 工具, 端点: /mcp/meta"
  # 注: 原 "跨宇宙全量 ticker 列表" 改为 "跨市场 ticker 搜索/列表", "跨宇宙" 是 slang 不准确

fuyao_fund:
  description: "同花顺-fund-mcp: 公募基金持仓/业绩/经理/财务/历史, 30 个 gateway 工具, 端点: /mcp/fund"
  # 注: 原 "公募基金持仓/业绩/经理/财务" 补 "历史" (capabilities 含 fund_history)

tushare:
  description: "Tushare: 通用 API 透传 (sdk_call / sdk_schema / sdk_search), 3 个 gateway 工具, 含财务/公告/通用"
```

**字段顺序**：放在 `enabled` 之后（enabled 是 schema 必填且常读，description 是展示用）。

### B. `src/console_server.py`

`_probe_upstream` (line 108-124) result dict 加一行：

```python
result = {
    "enabled": cfg.get("enabled", False),
    "type": upstream_type,
    "description": cfg.get("description", ""),  # NEW
    "status": "unknown",
    "latency_ms": None,
    "last_error": None,
}
```

如果某 upstream 没 `description` 字段（旧配置兼容），返回空字符串 `""`，console 端兜底到 GROUP_META。

### C. `console.html`

修改 `loadOnboarding` (line 1690-1717)：

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
            const meta = key === ALL_KEY
                ? { title: '全部接口', desc: '一次性查看所有上游的 tool' }
                : (GROUP_META[key] || { title: key, desc: '' });
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

关键变化：
1. `Promise.all([/api/interfaces, /api/upstreams])` — 并行 fetch
2. 构造 `descByName` map
3. 渲染 desc 时 `descByName[key] || meta.desc || ''` 优先级

`GROUP_META` 字典保留（line 1005-1014），作为兜底。

### D. `tests/test_console_interfaces.py`

加 1 个测试 `test_get_upstreams_includes_description`：

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

    # 单元验证：8 个 upstream 都定义了 description
    for name, cfg in config.get("upstreams", {}).items():
        assert "description" in cfg, f"upstream {name} missing description"
        assert cfg["description"].strip(), f"upstream {name} description is empty"

    # 集成验证：响应包含 description
    result = get_upstream_status()
    for name, info in result.get("upstreams", {}).items():
        assert "description" in info, f"{name} response missing description"
        assert info["description"].strip(), f"{name} description is empty in response"
```

## Acceptance

- pytest 142 + 1 = **143 passed**
- 8 个 upstream 都有非空 description 字段
- `/api/upstreams` 响应每个 upstream 含非空 description
- 手动验证：浏览器开 console，onboarding 标签每个上游卡片显示新描述（来自 YAML）
- GROUP_META 仍存在（兜底用，后续可逐个迁移删除）

## Risks

- **YAML 缺 description**：当前 spec 强制要求填；旧部署忘填 → console 显示 GROUP_META 兜底
- **空 description 字符串**：测试 `cfg["description"].strip()` 拦截，避免静默退化
- **YAML 描述漂移**：仍可能发生，但比 HTML 里漂移好查（YAML 是单一变更点）

## Out of scope / Future

- 删 GROUP_META — 等所有 upstream 都迁完再说
- 给 `tools:` 列表里每个 tool 也加 `description` 字段并 console 展示（已有 `description`，但 console 现在已经渲染了）
- 上游 capabilities 列表在 console 单独 tab 展示
- 自动从 tools 数动态算 "X 个 gateway 工具" 而不是硬编码数字
