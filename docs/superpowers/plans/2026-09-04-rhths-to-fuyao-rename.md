# RHTHS → Fuyao 重命名 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 4 个 RHTHS 上游 (`rhths_ashare` / `rhths_index` / `rhths_meta` / `rhths_fund`) 与关联代码 (`RhthsClient` / `RhthsConfig` / `rhths_client.py`) 硬切重命名为 `fuyao_*` / `FuyaoClient` / `FuyaoConfig` / `fuyao_client.py`，并提供一次性 .env 迁移脚本。

**Architecture:** 6 个原子任务，按 (新逻辑) → (Python) → (config) → (env+frontend) → (回归测试) → (验收) 顺序推进。每个任务一个 commit。TDD 仅应用于迁移脚本（新逻辑），其余任务为纯 rename 重构。

**Tech Stack:** Python 3.10+, pytest, FastMCP, FastAPI

---

## 文件结构 (与 spec §3.1 对齐)

| 文件 | 操作 |
|------|------|
| `scripts/migrate_rhths_env.py` | **create** |
| `src/rhths_client.py` | rename → `src/fuyao_client.py` |
| `src/gateway_server.py` | modify |
| `src/router.py` | modify |
| `src/http_jsonrpc_client.py` | modify (注释) |
| `config/upstreams.yaml` | modify |
| `config/upstreams - 副本.yaml` | **delete** |
| `.env.example` | modify |
| `tests/test_load_all_tools.py` | modify (fixture) |
| `console.html` | modify (provider keys 数组, 保留 "同花顺" 品牌) |
| `tests/test_migrate_rhths_env.py` | **create** |
| `tests/test_no_rhths_stragglers.py` | **create** |

---

## Task 1: 一次性 .env 迁移脚本 (TDD)

**Files:**
- Create: `scripts/migrate_rhths_env.py`
- Create: `tests/test_migrate_rhths_env.py`

- [ ] **Step 1.1: 写 7 个失败测试**

`tests/test_migrate_rhths_env.py`：

```python
"""Tests for scripts/migrate_rhths_env.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_rhths_env.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd or ROOT,
    )


def _write_env(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 行为契约 ----------

def test_migrate_renames_var(tmp_path: Path):
    """含 RHTHS_API_KEY=v 的 .env 运行后变成 FUYAO_API_KEY=v, 且 .env.bak = 原内容."""
    env = tmp_path / ".env"
    _write_env(env, ["# header", "RHTHS_API_KEY=secret123", "OTHER=keep"])
    result = _run(["--path", str(env)])
    assert result.returncode == 0, result.stderr
    text = env.read_text(encoding="utf-8")
    assert "FUYAO_API_KEY=secret123" in text
    assert "RHTHS_API_KEY=" not in text
    assert "OTHER=keep" in text
    bak = tmp_path / ".env.bak"
    assert bak.exists()
    assert "RHTHS_API_KEY=secret123" in bak.read_text(encoding="utf-8")


def test_migrate_idempotent(tmp_path: Path):
    """第二次运行 exit 0 且 mtime 不变."""
    env = tmp_path / ".env"
    _write_env(env, ["FUYAO_API_KEY=already"])
    mtime_before = env.stat().st_mtime
    result = _run(["--path", str(env)])
    assert result.returncode == 0, result.stderr
    assert "already present" in result.stderr
    assert env.stat().st_mtime == mtime_before


def test_migrate_dry_run_no_write(tmp_path: Path):
    """--dry-run 模式下不写盘, 不生成 .env.bak."""
    env = tmp_path / ".env"
    _write_env(env, ["RHTHS_API_KEY=v"])
    mtime_before = env.stat().st_mtime
    result = _run(["--path", str(env), "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "would rename" in result.stdout
    assert env.stat().st_mtime == mtime_before
    assert not (tmp_path / ".env.bak").exists()


def test_migrate_both_vars_error(tmp_path: Path):
    """两 var 同存 → exit 1, 不写盘."""
    env = tmp_path / ".env"
    _write_env(env, ["RHTHS_API_KEY=old", "FUYAO_API_KEY=new"])
    result = _run(["--path", str(env)])
    assert result.returncode == 1, result.stderr
    assert "both vars present" in result.stderr
    assert env.read_text(encoding="utf-8").count("API_KEY=") == 2  # 未改


def test_migrate_no_env_error(tmp_path: Path):
    """.env 不存在 → exit 2."""
    env = tmp_path / ".env"
    result = _run(["--path", str(env)])
    assert result.returncode == 2, result.stderr
    assert "not found" in result.stderr


def test_migrate_never_prints_value(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """脚本 stdout/stderr 永不打真实 key value."""
    secret = "x" * 64  # 不会自然出现的字符串
    env = tmp_path / ".env"
    _write_env(env, [f"RHTHS_API_KEY={secret}"])
    _run(["--path", str(env)])  # 真实运行, 触发写盘
    out = capsys.readouterr()
    assert secret not in out.out
    assert secret not in out.err


def test_migrate_already_new_skips(tmp_path: Path):
    """仅 FUYAO_API_KEY → exit 0, stderr 含 'already present', 不写盘."""
    env = tmp_path / ".env"
    _write_env(env, ["FUYAO_API_KEY=here"])
    mtime_before = env.stat().st_mtime
    result = _run(["--path", str(env)])
    assert result.returncode == 0
    assert "already present" in result.stderr
    assert env.stat().st_mtime == mtime_before
```

- [ ] **Step 1.2: 运行测试, 确认全部 RED (失败)**

```bash
pytest tests/test_migrate_rhths_env.py -v
```

Expected: 7 个 `FAILED`, 因 `migrate` 函数不存在 (`ModuleNotFoundError: No module named 'scripts.migrate_rhths_env'`).

- [ ] **Step 1.3: 实现 `scripts/migrate_rhths_env.py`**

```python
"""Migrate .env: RHTHS_API_KEY=xxx → FUYAO_API_KEY=xxx (preserve value)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_OLD_KEY = "RHTHS_API_KEY="
_NEW_KEY = "FUYAO_API_KEY="


def migrate(path: Path, dry_run: bool = False) -> int:
    if not path.exists():
        print(f"{path} not found; copy from .env.example first", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    has_old = any(line.startswith(_OLD_KEY) for line in lines)
    has_new = any(line.startswith(_NEW_KEY) for line in lines)

    if has_old and has_new:
        print("both vars present; resolve manually", file=sys.stderr)
        return 1
    if not has_old:
        if has_new:
            print("FUYAO_API_KEY already present, skipping", file=sys.stderr)
        else:
            print("nothing to migrate (no RHTHS_API_KEY or FUYAO_API_KEY present)", file=sys.stderr)
        return 0

    new_lines = [
        _NEW_KEY + line[len(_OLD_KEY):] if line.startswith(_OLD_KEY) else line
        for line in lines
    ]
    new_text = "".join(new_lines)

    if dry_run:
        print("would rename RHTHS_API_KEY → FUYAO_API_KEY (value redacted)")
        return 0

    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        print(f"warning: {bak} already exists, overwriting", file=sys.stderr)
    bak.write_text(text, encoding="utf-8")
    path.write_text(new_text, encoding="utf-8")
    print(f"migrated {path} (backup: {bak})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--path", default=".env", help="path to .env file (default: ./.env)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return migrate(Path(args.path), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 1.4: 运行测试, 确认全部 GREEN**

```bash
pytest tests/test_migrate_rhths_env.py -v
```

Expected: `7 passed`.

- [ ] **Step 1.5: 跑全套测试, 确认无 regress (118 passed)**

```bash
pytest -q
```

Expected: `118 passed` (没有动到现有代码, 新增 7 个 migrate 测试已包含 → **125 passed**).

- [ ] **Step 1.6: Commit**

```bash
git add scripts/migrate_rhths_env.py tests/test_migrate_rhths_env.py
git commit -m "feat(scripts): one-shot .env migration script for rhths→fuyao rename

scripts/migrate_rhths_env.py:
- rename RHTHS_API_KEY=xxx → FUYAO_API_KEY=xxx (preserve value)
- atomic write via os.replace + .env.bak backup
- never prints key value to stdout/stderr (value redacted in --dry-run)
- idempotent: 第二次运行检测到 FUYAO_API_KEY 已存在则 skip
- 7 个测试覆盖: rename, idempotent, dry-run, both-vars error, no-env, never-prints-value, already-new-skips"
```

---

## Task 2: Python 模块与类重命名

**Files:**
- Rename: `src/rhths_client.py` → `src/fuyao_client.py`
- Modify: `src/fuyao_client.py` (内部类名 + docstring)
- Modify: `src/gateway_server.py` (import + type hint + var name + 字面量)
- Modify: `src/router.py` (import + type hint)
- Modify: `src/http_jsonrpc_client.py` (注释)

- [ ] **Step 2.1: Git mv 模块文件**

```bash
git mv src/rhths_client.py src/fuyao_client.py
```

- [ ] **Step 2.2: 在 `src/fuyao_client.py` 内重命名类与 docstring**

使用 Edit 工具的 `replace_all=True`, 三处替换:

- `RhthsClient` → `FuyaoClient` (类名, 出现 2 次: class 定义 + __init__ 引用)
- `RhthsConfig` → `FuyaoConfig` (类名, 出现 2 次: class 定义 + 函数参数 type hint)
- module docstring 末行 `RHTHS (同花顺) HTTP MCP Client` → `FUYAO (同花顺) HTTP MCP Client`

执行后 cat 检查:

```bash
head -5 src/fuyao_client.py
grep -n "Rhths\|rhths" src/fuyao_client.py
```

Expected: `head` 显示 docstring 已含 "FUYAO", `grep` 0 hits.

- [ ] **Step 2.3: 更新 `src/gateway_server.py`**

具体改动 (用 Edit 工具逐处替换):

| 行 | old_string | new_string |
|----|-----------|------------|
| 23 | `from .rhths_client import RhthsClient, RhthsConfig` | `from .fuyao_client import FuyaoClient, FuyaoConfig` |
| 39 | `dict[str, UpstreamClient \| RhthsClient \| HttpJsonRpcClient]` | `dict[str, UpstreamClient \| FuyaoClient \| HttpJsonRpcClient]` |
| 64 | `rhths_cfg = RhthsConfig(` | `fuyao_cfg = FuyaoConfig(` |
| 71 | `client = RhthsClient(rhths_cfg)` | `client = FuyaoClient(fuyao_cfg)` |
| 191 | `if "rhths_meta" in self.upstreams:` | `if "fuyao_meta" in self.upstreams:` |
| 202 | `source="rhths_meta",` | `source="fuyao_meta",` |

执行后验证:

```bash
grep -n "Rhths\|rhths" src/gateway_server.py
```

Expected: 0 hits.

- [ ] **Step 2.4: 更新 `src/router.py`**

| 行 | old_string | new_string |
|----|-----------|------------|
| 10 | `from .rhths_client import RhthsClient` | `from .fuyao_client import FuyaoClient` |
| 45 | `dict[str, UpstreamClient \| RhthsClient \| HttpJsonRpcClient]` | `dict[str, UpstreamClient \| FuyaoClient \| HttpJsonRpcClient]` |

验证:

```bash
grep -n "Rhths\|rhths" src/router.py
```

Expected: 0 hits.

- [ ] **Step 2.5: 更新 `src/http_jsonrpc_client.py` 注释**

行 31: `与 RhthsClient 不同的关键点` → `与 FuyaoClient 不同的关键点`.

验证:

```bash
grep -n "Rhths\|rhths" src/http_jsonrpc_client.py
```

Expected: 0 hits.

- [ ] **Step 2.6: 跑全套测试, 确认无 regress**

```bash
pytest -q
```

Expected: `118 passed` (这个任务没改 yaml/upstream key, 仅改 Python 名字, 现有 fixture 应该都过).

**注意**: 如果失败, **不要**继续. 失败的常见原因: 还有别的 import 路径漏改. 用 `grep -rn "RhthsClient\|RhthsConfig\|rhths_client" src/` 全扫一遍.

- [ ] **Step 2.7: Commit**

```bash
git add src/rhths_client.py src/fuyao_client.py src/gateway_server.py src/router.py src/http_jsonrpc_client.py
# 上面 git add 实际只需要 src/fuyao_client.py + 3 个 modify; src/rhths_client.py 已被 git mv 处理, 但 git add src/ 会同时跟踪删除/添加
git commit -m "refactor(python): rename RhthsClient/RhthsConfig to FuyaoClient/FuyaoConfig

- src/rhths_client.py → src/fuyao_client.py (git mv 保留历史)
- RhthsClient → FuyaoClient, RhthsConfig → FuyaoConfig
- module docstring 同步
- gateway_server.py: import, type hint, 局部变量 rhths_cfg→fuyao_cfg, 'rhths_meta' 字面量→'fuyao_meta'
- router.py: import, type hint
- http_jsonrpc_client.py: 注释里的 RhthsClient 引用→FuyaoClient
- 零行为变更, 118 tests passed"
```

---

## Task 3: config/upstreams.yaml 重命名 + 删除副本

**Files:**
- Modify: `config/upstreams.yaml`
- Delete: `config/upstreams - 副本.yaml`

- [ ] **Step 3.1: 重命名 4 个 upstream key**

用 Edit 工具, `replace_all=True`, 在 `config/upstreams.yaml` 中:

- `rhths_ashare` → `fuyao_ashare`
- `rhths_index` → `fuyao_index`
- `rhths_meta` → `fuyao_meta`
- `rhths_fund` → `fuyao_fund`

执行后验证:

```bash
grep -n "rhths" config/upstreams.yaml
```

Expected: 0 hits (env var 占位符 `${RHTHS_API_KEY}` 仍在, 下一步处理).

- [ ] **Step 3.2: 替换 env var 占位符**

`replace_all=True`:

- `${RHTHS_API_KEY}` → `${FUYAO_API_KEY}`

验证:

```bash
grep -n "RHTHS_API_KEY" config/upstreams.yaml
```

Expected: 0 hits.

- [ ] **Step 3.3: 替换章节注释**

`replace_all=True`:

- `# RHTHS a-share` → `# Fuyao a-share`
- `# RHTHS a-share-index` → `# Fuyao a-share-index`
- `# RHTHS meta` → `# Fuyao meta`
- `# RHTHS fund` → `# Fuyao fund`

(章节注释数量由 grep `^# RHTHS` 决定; 若还有其他 `RHTHS` 字样未替换, 单独替换.)

最终验证:

```bash
grep -in "rhths" config/upstreams.yaml
```

Expected: 0 hits (大小写均无).

- [ ] **Step 3.4: 删除副本文件**

```bash
git rm "config/upstreams - 副本.yaml"
```

- [ ] **Step 3.5: 跑全套测试, 确认无 regress**

```bash
pytest -q
```

Expected: `118 passed`. (env var 名没改, 仅 upstream key 改名; config/upstreams.yaml 通过 `${FUYAO_API_KEY}` 占位符仍读 os.environ; 但因为 os.environ 没设 `FUYAO_API_KEY`, 实际会读到空字符串 → gateway 启动时 api_key 为 "" → 运行时不报错, 但请求会失败 — 这个测试不验证运行时行为, 所以仍 pass.)

- [ ] **Step 3.6: Commit**

```bash
git add config/upstreams.yaml
git commit -m "refactor(config): rename rhths_* upstream keys to fuyao_*

config/upstreams.yaml:
- 4 个 upstream key: rhths_ashare/index/meta/fund → fuyao_*
- env var 占位符 \${RHTHS_API_KEY} → \${FUYAO_API_KEY}
- 章节注释 # RHTHS * → # Fuyao *
- 删除字节相同的备份文件 config/upstreams - 副本.yaml

注: 用户本地 .env 仍含 RHTHS_API_KEY=xxx, 需手动跑
scripts/migrate_rhths_env.py (Task 1 已交付) 完成迁移.
或者直接编辑: sed -i 's/^RHTHS_API_KEY=/FUYAO_API_KEY=/' .env"
```

---

## Task 4: .env.example + 测试 fixture + console.html

**Files:**
- Modify: `.env.example`
- Modify: `tests/test_load_all_tools.py`
- Modify: `console.html` (provider keys 数组, 保留 "同花顺" UI 文案)

- [ ] **Step 4.1: 更新 `.env.example`**

用 Edit, 替换:

- `# 同花顺金融数据 API Key (用于 rhths_ashare / rhths_index / rhths_meta / rhths_fund 四个上游)` → `# 同花顺金融数据 API Key (用于 fuyao_ashare / fuyao_index / fuyao_meta / fuyao_fund 四个上游)`
- `RHTHS_API_KEY=` → `FUYAO_API_KEY=`

验证:

```bash
grep -in "rhths" .env.example
```

Expected: 0 hits.

- [ ] **Step 4.2: 更新 `tests/test_load_all_tools.py` fixture**

查找 fixture 文件里出现的 `rhths_meta:` (test_load_all_tools.py:19 与 :28), 替换为 `fuyao_meta:`.

`replace_all=True` 一次完成.

验证:

```bash
grep -n "rhths" tests/test_load_all_tools.py
```

Expected: 0 hits.

- [ ] **Step 4.3: 更新 `console.html` provider keys 数组**

打开 `console.html`, 找到第 1004 行附近的 `GROUP_ORDER` 数组 (含 `'rhths_ashare'` 等 4 项) 与第 1008-1011 行的 provider keys 映射. **仅**替换以下字面量:

| old | new |
|-----|-----|
| `'rhths_ashare'` | `'fuyao_ashare'` |
| `'rhths_index'` | `'fuyao_index'` |
| `'rhths_meta'` | `'fuyao_meta'` |
| `'rhths_fund'` | `'fuyao_fund'` |

**保留不动**: "同花顺" UI 品牌文案, "rhths_a-share" 等带后缀的描述性字符串若不含 `rhths_` 也保留 (检查后再决定).

验证:

```bash
grep -n "rhths" console.html
```

Expected: 0 hits (除可能保留的 UI 文案, 但 UI 文案通常用 "同花顺" 而非 "rhths"). 如果出现 "rhths" 字样, 单独检查是否需要替换.

- [ ] **Step 4.4: 跑全套测试**

```bash
pytest -q
```

Expected: `118 passed`.

- [ ] **Step 4.5: Commit**

```bash
git add .env.example tests/test_load_all_tools.py console.html
git commit -m "refactor(env+tests+console): complete rhths→fuyao rename in remaining files

- .env.example: env var 名 + 注释里的 4 个 upstream key 同步
- tests/test_load_all_tools.py: fixture 里 rhths_meta: → fuyao_meta:
- console.html: provider keys 数组 4 项 ('rhths_*') → ('fuyao_*')
  注: UI 文案 \"同花顺\" 品牌保留, 仅改代码层的 provider key 字符串"
```

---

## Task 5: grep 回归保护测试

**Files:**
- Create: `tests/test_no_rhths_stragglers.py`

- [ ] **Step 5.1: 写 grep 回归测试**

`tests/test_no_rhths_stragglers.py`:

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

- [ ] **Step 5.2: 运行测试, 期望 GREEN (因为 Task 2-4 已全部改名)**

```bash
pytest tests/test_no_rhths_stragglers.py -v
```

Expected: `1 passed`.

**如果失败**: 返回 Task 2-4 排查. 失败信息会列出所有剩余的 `rhths` 引用与文件:行号.

- [ ] **Step 5.3: 跑全套测试, 确认 126 passed**

```bash
pytest -q
```

Expected: `126 passed` (= 既有 118 + Task 1 新增 7 个 migrate + Task 5 新增 1 个 grep).

- [ ] **Step 5.4: Commit**

```bash
git add tests/test_no_rhths_stragglers.py
git commit -m "test(regression): grep guard - no rhths in active code/config/console/env

硬切重命名配套防护: 任何后续代码意外引入 'rhths' 字符串 (含大小写)
会立即被此测试发现. docs/ 历史文件显式排除 (审计追踪)."
```

---

## Task 6: 最终验收

**Files:** 无新增, 仅运行验证命令

- [ ] **Step 6.1: 项目级 grep 确认**

```bash
grep -rin "rhths" src/ config/upstreams.yaml tests/ console.html .env.example scripts/
```

Expected: 0 hits.

- [ ] **Step 6.2: env var 名 grep 确认**

```bash
grep -rn "RHTHS_API_KEY" config/ src/ tests/ scripts/ .env.example
```

Expected: 0 hits.

- [ ] **Step 6.3: 新名字 grep 确认 (≥ 4 hits)**

```bash
grep -rn "FuyaoClient\|FuyaoConfig" src/ tests/ | wc -l
```

Expected: `>= 4` (至少: class 定义 + 1 个 import in gateway_server + 1 个 type hint in router + 1 个 type hint in gateway_server).

- [ ] **Step 6.4: 跑迁移脚本 dry-run**

(仅当用户 .env 含真实 key 时; 否则跳过)

```bash
python scripts/migrate_rhths_env.py --dry-run
```

Expected: 输出 `would rename RHTHS_API_KEY → FUYAO_API_KEY (value redacted)`, exit 0.

- [ ] **Step 6.5: 全套 pytest**

```bash
pytest -q
```

Expected: `126 passed, 1 warning` (与现有 baseline 一致).

- [ ] **Step 6.6: 跑 codegen --check 确认 TQ-Local 链路无影响**

```bash
python scripts/gen_tdx_tq_local_tools.py --check
```

Expected: `58 methods (N dangerous)`, exit 0. (TQ-Local 路径与 RHTHS 独立, 此步仅做 sanity check.)

- [ ] **Step 6.7: 在用户本地 .env 上跑迁移脚本 (可选, 用户执行)**

(此步仅作 reminder, 不在 PR 内 commit)

```bash
python scripts/migrate_rhths_env.py
ls -la .env .env.bak
grep "API_KEY=" .env .env.bak
```

Expected: `.env` 含 `FUYAO_API_KEY=...`, `.env.bak` 含原 `RHTHS_API_KEY=...`. 若用户 .env 已无 `RHTHS_API_KEY` (之前手动改过), 脚本应 exit 0 并打印 skip 提示.

---

## Self-Review Checklist

- [x] **Spec coverage**: 每条 spec 验收项都有 task 实现
  - spec §3.1 改动清单 → Task 2-4 + 副本文件删除
  - spec §4 migrate 脚本 → Task 1
  - spec §5.2 migrate 测试 → Task 1
  - spec §5.3 grep 回归测试 → Task 5
  - spec §9 验收标准 → Task 6
- [x] **No placeholders**: 无 TBD/TODO/"implement later"
- [x] **Type consistency**: 全文统一使用 `FuyaoClient` / `FuyaoConfig` / `fuyao_ashare` / `FUYAO_API_KEY`
- [x] **DRY**: 6 个任务, 每个有清晰 boundary
- [x] **YAGNI**: 不实现 deprecation alias / 自动化 alias 检测 (用户决定硬切)

## 预计总耗时

- Task 1 (migrate + tests): ~20 min
- Task 2 (Python rename): ~10 min
- Task 3 (config rename): ~5 min
- Task 4 (env + tests + console): ~5 min
- Task 5 (grep test): ~3 min
- Task 6 (验收): ~3 min

合计 ~45 分钟.

## 风险与回滚

- **风险**: 硬切意味着用户本地 `.env` 改名前, 网关会报 missing `FUYAO_API_KEY`. Task 1 已交付迁移脚本缓解.
- **回滚**: `git revert` 此 PR 即可恢复全部命名, 但用户的 `.env` 已被迁移脚本改名, 需手动 sed 改回.
