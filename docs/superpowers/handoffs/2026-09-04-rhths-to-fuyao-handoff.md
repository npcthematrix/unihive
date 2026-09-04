# RHTHS → Fuyao 重命名 — 实施 handoff

**会话上下文临界 (92%)，从 Task 2 中途移交。Plan 与 spec 完整，按下面命令照做即可。**

## 当前 git 状态 (执行 handoff 前的 baseline)

```
R  src/rhths_client.py -> src/fuyao_client.py        # 已 git mv, 未 commit
?? config/upstreams - 副本.yaml                       # 待删除
```

最近 commits:
- `f08daa1` docs(plan): rhths→fuyao rename implementation plan
- `4036d75` feat(scripts): one-shot .env migration script for rhths→fuyao rename
- `17449d5` docs(spec): self-review fix
- `741abe7` docs(spec): rhths→fuyao upstream rename design

## 引用文件

- Spec: `docs/superpowers/specs/2026-09-04-rhths-to-fuyao-rename-design.md`
- Plan: `docs/superpowers/plans/2026-09-04-rhths-to-fuyao-rename.md`
- 验证 baseline: `pytest -q` 当前 125 passed (118 既有 + 7 migrate)

---

## Task 2 剩余: Python 模块/类重命名

`git mv` 已执行, src/fuyao_client.py 已存在 (内仍是旧名).

### Step 2.1 — 改 `src/fuyao_client.py` 内部类名

用 Edit 工具, `replace_all=true`:

- `RhthsClient` → `FuyaoClient`
- `RhthsConfig` → `FuyaoConfig`

类 docstring 改一行 (模块 docstring 末行):
- `RHTHS (同花顺) HTTP MCP Client` → `FUYAO (同花顺) HTTP MCP Client`

验证:

```bash
grep -n "Rhths\|rhths" src/fuyao_client.py
```

期望: 0 hits.

### Step 2.2 — `src/gateway_server.py` 6 处替换

| 行 | old | new |
|----|-----|-----|
| 23 | `from .rhths_client import RhthsClient, RhthsConfig` | `from .fuyao_client import FuyaoClient, FuyaoConfig` |
| 39 | `dict[str, UpstreamClient \| RhthsClient \| HttpJsonRpcClient]` | `dict[str, UpstreamClient \| FuyaoClient \| HttpJsonRpcClient]` |
| 64 | `rhths_cfg = RhthsConfig(` | `fuyao_cfg = FuyaoConfig(` |
| 71 | `client = RhthsClient(rhths_cfg)` | `client = FuyaoClient(fuyao_cfg)` |
| 191 | `if "rhths_meta" in self.upstreams:` | `if "fuyao_meta" in self.upstreams:` |
| 202 | `source="rhths_meta",` | `source="fuyao_meta",` |

验证: `grep -n "Rhths\|rhths" src/gateway_server.py` → 0 hits.

### Step 2.3 — `src/router.py` 2 处

| 行 | old | new |
|----|-----|-----|
| 10 | `from .rhths_client import RhthsClient` | `from .fuyao_client import FuyaoClient` |
| 45 | `dict[str, UpstreamClient \| RhthsClient \| HttpJsonRpcClient]` | `dict[str, UpstreamClient \| FuyaoClient \| HttpJsonRpcClient]` |

### Step 2.4 — `src/http_jsonrpc_client.py` 1 处

行 31 注释: `与 RhthsClient 不同的关键点` → `与 FuyaoClient 不同的关键点`.

### Step 2.5 — 跑全套测试

```bash
pytest -q
```

期望: `125 passed`. 若失败, 全项目 grep `RhthsClient|RhthsConfig|rhths_client` 排查.

### Step 2.6 — Commit

```bash
git add src/rhths_client.py src/fuyao_client.py src/gateway_server.py src/router.py src/http_jsonrpc_client.py
git commit -m "refactor(python): rename RhthsClient/RhthsConfig to FuyaoClient/FuyaoConfig

- src/rhths_client.py → src/fuyao_client.py (git mv 保留历史)
- RhthsClient → FuyaoClient, RhthsConfig → FuyaoConfig
- gateway_server.py: import, type hint, 局部变量 rhths_cfg→fuyao_cfg, 'rhths_meta'→'fuyao_meta'
- router.py / http_jsonrpc_client.py: import + 注释
- 125 tests passed"
```

---

## Task 3: config/upstreams.yaml 重命名 + 删除副本

### Step 3.1 — 4 个 upstream key 改名 (replace_all=true)

- `rhths_ashare` → `fuyao_ashare`
- `rhths_index` → `fuyao_index`
- `rhths_meta` → `fuyao_meta`
- `rhths_fund` → `fuyao_fund`

### Step 3.2 — env var 占位符

- `${RHTHS_API_KEY}` → `${FUYAO_API_KEY}` (replace_all)

### Step 3.3 — 章节注释

- `# RHTHS a-share` → `# Fuyao a-share`
- `# RHTHS a-share-index` → `# Fuyao a-share-index`
- `# RHTHS meta` → `# Fuyao meta`
- `# RHTHS fund` → `# Fuyao fund`

### Step 3.4 — 验证

```bash
grep -in "rhths" config/upstreams.yaml   # 期望 0
grep -n "RHTHS_API_KEY" config/upstreams.yaml  # 期望 0
```

### Step 3.5 — 删除副本文件

```bash
git rm "config/upstreams - 副本.yaml"
```

### Step 3.6 — 跑测试 + commit

```bash
pytest -q    # 期望 125 passed
git add config/upstreams.yaml
git commit -m "refactor(config): rename rhths_* upstream keys to fuyao_*

config/upstreams.yaml: 4 upstream key + env var + 章节注释
删除字节相同的备份文件 config/upstreams - 副本.yaml

用户本地 .env 仍含 RHTHS_API_KEY=xxx, 跑 scripts/migrate_rhths_env.py
或直接: sed -i 's/^RHTHS_API_KEY=/FUYAO_API_KEY=/' .env"
```

---

## Task 4: .env.example + 测试 fixture + console.html

### Step 4.1 — `.env.example` (2 处)

- `# 同花顺金融数据 API Key (用于 rhths_ashare / rhths_index / rhths_meta / rhths_fund 四个上游)` → `# 同花顺金融数据 API Key (用于 fuyao_ashare / fuyao_index / fuyao_meta / fuyao_fund 四个上游)`
- `RHTHS_API_KEY=` → `FUYAO_API_KEY=`

### Step 4.2 — `tests/test_load_all_tools.py`

`replace_all=true`: `rhths_meta:` → `fuyao_meta:` (出现 2 处, 行 19 与 28).

### Step 4.3 — `console.html`

仅替换 provider keys 数组字面量 (4 项), **保留** "同花顺" UI 品牌:

- `'rhths_ashare'` → `'fuyao_ashare'`
- `'rhths_index'` → `'fuyao_index'`
- `'rhths_meta'` → `'fuyao_meta'`
- `'rhths_fund'` → `'fuyao_fund'`

在 `console.html` 第 1004 行附近的 `GROUP_ORDER` 数组, 与第 1008-1011 行的 provider keys 映射.

### Step 4.4 — 验证 + commit

```bash
grep -n "rhths" console.html    # 期望 0 (除 "同花顺" 文案)
grep -n "rhths" .env.example    # 期望 0
pytest -q    # 期望 125 passed
git add .env.example tests/test_load_all_tools.py console.html
git commit -m "refactor(env+tests+console): complete rhths→fuyao rename in remaining files

- .env.example: env var + 4 个 upstream key 同步
- tests/test_load_all_tools.py: fixture rhths_meta: → fuyao_meta:
- console.html: provider keys 数组 4 项
  注: \"同花顺\" UI 品牌保留, 仅改代码层 provider key 字符串"
```

---

## Task 5: grep 回归保护

### Step 5.1 — 创建 `tests/test_no_rhths_stragglers.py`

```python
"""Regression: no 'rhths' (case-insensitive) in active code, config, console, env.

文档类文件 (docs/) 是历史审计追踪, 显式排除.
"""
import re
from pathlib import Path


_SCAN_PATTERNS = (
    "src/**/*.py",
    "config/upstreams.yaml",
    "tests/**/*.py",
    "console.html",
    ".env.example",
    "scripts/**/*.py",
)
_EXCLUDE_PARTS = {"docs", "__pycache__", ".git", ".pytest_cache", "node_modules"}
_RHTHS = re.compile(r"rhths", re.IGNORECASE)


def test_no_rhths_in_active_code():
    root = Path(".")
    hits: list[str] = []
    for pattern in _SCAN_PATTERNS:
        for path in root.glob(pattern):
            if any(part in _EXCLUDE_PARTS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if _RHTHS.search(line):
                    hits.append(f"{path}:{i}: {line.rstrip()}")
    assert hits == [], (
        "rhths references found in active code (rename incomplete):\n"
        + "\n".join(hits)
    )
```

### Step 5.2 — 跑测试 + 全套

```bash
pytest tests/test_no_rhths_stragglers.py -v    # 期望 1 passed
pytest -q    # 期望 126 passed (125 + 1 grep)
```

### Step 5.3 — Commit

```bash
git add tests/test_no_rhths_stragglers.py
git commit -m "test(regression): grep guard - no rhths in active code/config/console/env

硬切重命名配套防护: 后续引入 'rhths' 字符串立即被发现. docs/ 排除."
```

---

## Task 6: 最终验收

```bash
grep -rin "rhths" src/ config/upstreams.yaml tests/ console.html .env.example scripts/
grep -rn "RHTHS_API_KEY" config/ src/ tests/ scripts/ .env.example
grep -rn "FuyaoClient\|FuyaoConfig" src/ tests/
pytest -q    # 期望 126 passed
python scripts/gen_tdx_tq_local_tools.py --check    # sanity: TQ-Local 链路无影响
```

用户本地 `.env` 迁移提醒 (不在 commit 内):

```bash
python scripts/migrate_rhths_env.py --dry-run    # 看提示
python scripts/migrate_rhths_env.py             # 真实迁移
```

---

## 关键提醒

- **每个 Task 一次 commit**, 不批量.
- **每个 Task 完成后跑 `pytest -q`**, 期望数字在每步标明.
- **若测试失败**, 先 `grep -rn "rhths\|Rhths\|RHTHS" src/ config/ tests/ console.html .env.example scripts/` 排查漏改.
- **不要修改 docs/** 与 CLAUDE.md (用户已确认不在范围).
- **不要修改 console.html 中的 "同花顺" UI 品牌文案** (用户决定保留).
