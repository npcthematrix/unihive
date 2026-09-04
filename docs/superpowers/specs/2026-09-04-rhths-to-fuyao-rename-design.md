# RHTHS → Fuyao 上游服务硬切重命名 — 设计文档

**项目**: UNIHIVE MCP Gateway
**时间**: 2026-09-04
**状态**: 设计稿 (待实施)
**父文档**: 无 (独立改名 PR)

## 1. 背景

`tdx_tq_local` 等上游在配置里以 `rhths_*` 前缀命名 (`rhths_ashare` / `rhths_index` / `rhths_meta` / `rhths_fund`)，但外部 API base_url 实际已经是 `https://fuyao.aicubes.cn/*`（"fuyao" 已经是上游服务的对外身份）。本次改名将内部命名与外部身份对齐。

**用户决策 (2026-09-04)**：硬切重命名, 不留 `rhths_*` 别名过渡。

**blast radius**（grep `rhths` 全项目扫描结果）:
- `config/upstreams.yaml`: 84 处
- `config/upstreams - 副本.yaml`: 84 处 (字节相同, 过期备份, 删除)
- `.env.example`: 2 处
- `src/rhths_client.py`: 6 处 (整模块 + 2 个类)
- `src/gateway_server.py`: 6 处
- `src/router.py`: 2 处
- `src/http_jsonrpc_client.py`: 1 处 (注释)
- `tests/test_load_all_tools.py`: 2 处
- `console.html`: 9 处 (provider keys 数组, "同花顺" UI 文案保留)
- `docs/superpowers/specs/*.md` / `plans/*.md` / `CLAUDE.md`: 若干处, **不在本次范围**

合计约 **215** 处需改动。

## 2. 用户决策记录

| 问题 | 选择 |
|------|------|
| 改名范围 | upstream key + env var + Python 类/模块 (UI 品牌 + docs 保留) |
| 向后兼容 | 硬切, 不留 rhths_* 别名 |
| 副本文件处理 | 删除 `config/upstreams - 副本.yaml` |
| .env 迁移方式 | PR 带迁移脚本 `scripts/migrate_rhths_env.py` |
| 迁移脚本是否写 `.env.bak` 备份 | 是 |
| grep 回归测试 | 保留 `test_no_rhths_in_active_code` |
| FUYAO_API_KEY 取值 | 等同原 RHTHS_API_KEY 的值 |

## 3. 改动清单

### 3.1 文件操作矩阵

| 文件 | 操作 | 改动摘要 |
|------|------|---------|
| `src/rhths_client.py` | rename → `src/fuyao_client.py` | `RhthsClient` → `FuyaoClient`, `RhthsConfig` → `FuyaoConfig`, docstring 同步 |
| `src/gateway_server.py` | modify | import 路径, type hint, 4 处 upstream key 字面量, env var 名 (`${RHTHS_API_KEY}` → `${FUYAO_API_KEY}`) |
| `src/router.py` | modify | import 路径, type hint |
| `src/http_jsonrpc_client.py` | modify | 1 处注释里的 `RhthsClient` 字面引用 |
| `config/upstreams.yaml` | modify | 4 个 upstream key + 4 处 `${RHTHS_API_KEY}` env var + 章节注释 + 54 条 capability 映射 + 17 条 fallback chain |
| `config/upstreams - 副本.yaml` | **delete** | 字节与 primary 相同 |
| `.env.example` | modify | `RHTHS_API_KEY=` → `FUYAO_API_KEY=`, 注释里的 "rhths_*" 提及同步 |
| `tests/test_load_all_tools.py` | modify | fixture 里 `rhths_meta:` → `fuyao_meta:` |
| `console.html` | modify | provider keys 数组 4 项 (`'rhths_ashare'` 等) 更新; **保留** "同花顺" UI 品牌文案 |
| `scripts/migrate_rhths_env.py` | **create** | 一次性迁移脚本, 见 §4 |
| `tests/test_migrate_rhths_env.py` | **create** | 迁移脚本的测试, 见 §5.2 |
| `tests/test_no_rhths_stragglers.py` | **create** | grep 回归保护, 见 §5.3 |

### 3.2 命名规则

| 层 | 旧 | 新 | 规则 |
|----|----|----|------|
| upstream key | `rhths_ashare` | `fuyao_ashare` | 小写, prefix 替换, suffix 保持 |
| env var | `RHTHS_API_KEY` | `FUYAO_API_KEY` | 全大写, prefix 替换 |
| Python class | `RhthsClient` | `FuyaoClient` | PascalCase, prefix 替换 |
| Python class | `RhthsConfig` | `FuyaoConfig` | PascalCase, prefix 替换 |
| Module file | `src/rhths_client.py` | `src/fuyao_client.py` | snake_case |
| Module docstring | `RHTHS (同花顺) HTTP MCP Client` | `FUYAO (同花顺) HTTP MCP Client` | 同步 |

## 4. 迁移脚本 `scripts/migrate_rhths_env.py`

**职责**: 把本地 `.env` 里的 `RHTHS_API_KEY=xxx` 重命名为 `FUYAO_API_KEY=xxx`, **保留原值**.

### 4.1 CLI

```bash
python scripts/migrate_rhths_env.py             # 默认改 ./.env
python scripts/migrate_rhths_env.py --path .env.local
python scripts/migrate_rhths_env.py --dry-run
```

### 4.2 行为契约

| 输入 | exit code | stderr | 实际写盘 |
|------|-----------|--------|---------|
| `.env` 不存在 | 2 | `.env not found at <path>; copy from .env.example first` | 否 |
| 既无 `RHTHS_API_KEY` 也无 `FUYAO_API_KEY` | 0 | `nothing to migrate (no RHTHS_API_KEY or FUYAO_API_KEY present)` | 否 |
| 只有 `FUYAO_API_KEY` 已存在 | 0 | `FUYAO_API_KEY already present, skipping` | 否 |
| `RHTHS_API_KEY` + `FUYAO_API_KEY` 都存在 | 1 | `both vars present; resolve manually` | 否 |
| 只有 `RHTHS_API_KEY` 存在 | 0 | (silent) | **是** — rename + `.env.bak` |

### 4.3 安全约束

- **永远不在 stdout/stderr 打印 key 的真实值**
- `--dry-run` 输出 `would rename RHTHS_API_KEY → FUYAO_API_KEY (value redacted)`
- 写盘用 atomic rename (`os.replace`) 防半截写入
- 写盘前先备份到 `<path>.bak` (例: `.env.bak`)
- 若 `.env.bak` 已存在, 备份前先打 warning, 仍覆盖 (用户可手动恢复)

### 4.4 实现骨架

```python
"""Migrate .env: RHTHS_API_KEY=xxx → FUYAO_API_KEY=xxx (preserve value)."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


def migrate(path: Path, dry_run: bool = False) -> int:
    if not path.exists():
        print(f"{path} not found; copy from .env.example first", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    has_old = any(line.startswith("RHTHS_API_KEY=") for line in text.splitlines())
    has_new = any(line.startswith("FUYAO_API_KEY=") for line in text.splitlines())

    if has_old and has_new:
        print("both vars present; resolve manually", file=sys.stderr)
        return 1
    if not has_old:
        if has_new:
            print("FUYAO_API_KEY already present, skipping", file=sys.stderr)
        else:
            print("nothing to migrate (no RHTHS_API_KEY or FUYAO_API_KEY present)", file=sys.stderr)
        return 0

    # 仅 has_old, rename 保留 value
    new_lines = []
    for line in text.splitlines(keepends=True):
        if line.startswith("RHTHS_API_KEY="):
            new_lines.append("FUYAO_API_KEY=" + line[len("RHTHS_API_KEY="):])
        else:
            new_lines.append(line)
    new_text = "".join(new_lines)

    if dry_run:
        print("would rename RHTHS_API_KEY → FUYAO_API_KEY (value redacted)")
        return 0

    # 备份 + 写盘
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

## 5. 测试

### 5.1 现有测试 fixture 同步

- `tests/test_load_all_tools.py` 里 `rhths_meta:` → `fuyao_meta:` (1 处)

### 5.2 迁移脚本测试 `tests/test_migrate_rhths_env.py`

| 测试 | 断言 |
|------|------|
| `test_migrate_renames_var` | fixture `.env` 含 `RHTHS_API_KEY=v`; 运行后含 `FUYAO_API_KEY=v` 且无 `RHTHS_API_KEY`; 退出码 0; `.env.bak` 存在且内容 = 原 `.env` |
| `test_migrate_idempotent` | 第二次运行 exit 0 且文件 mtime 不变 |
| `test_migrate_dry_run_no_write` | 跑 `--dry-run` 后 mtime 不变; 不生成 `.env.bak` |
| `test_migrate_both_vars_error` | `.env` 同时含 `RHTHS_API_KEY=...` 和 `FUYAO_API_KEY=...` → exit 1 |
| `test_migrate_no_env_error` | `.env` 不存在 → exit 2 |
| `test_migrate_never_prints_value` | 用 `capsys` 捕获 stdout/stderr, 断言**不含**测试 fixture 里的 key value |
| `test_migrate_already_new_skips` | 仅含 `FUYAO_API_KEY=...` → exit 0, stderr 含 "already present" |

### 5.3 grep 回归保护 `tests/test_no_rhths_stragglers.py`

```python
"""Regression: no 'rhths' (case-insensitive) in active code, config, console, env."""
import re
from pathlib import Path

SCAN_GLOBS = ("src/**/*.py", "config/upstreams.yaml", "tests/**/*.py", "console.html", ".env.example")
EXCLUDE_DIRS = {"docs", "__pycache__", ".git"}

def test_no_rhths_in_active_code():
    pat = re.compile(r"rhths", re.IGNORECASE)
    hits = []
    root = Path(".")
    for glob in SCAN_GLOBS:
        for p in root.glob(glob):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{p}:{i}: {line.rstrip()}")
    assert hits == [], f"rhths references found:\n" + "\n".join(hits)
```

## 6. 数据流 (无变更)

本次纯重命名, 不改变任何运行时行为. 启动流程、路由决策、缓存键、HTTP 请求路径全部不变. 唯一运行时区别:

- env var 名: `RHTHS_API_KEY` → `FUYAO_API_KEY`
- upstream key: `rhths_*` → `fuyao_*` (在 HTTP request header / log 中可见)
- Python 异常堆栈里的类名: `RhthsClient` → `FuyaoClient`

## 7. 安全姿态变更

**改名前**: 网关从 `${RHTHS_API_KEY}` env 读 key, 发送给 `https://fuyao.aicubes.cn/*` 上游.

**改名后**: 网关从 `${FUYAO_API_KEY}` env 读 key (值相同), 发给同一上游.

**安全风险**: 
- 迁移脚本读 `.env` (本地 gitignored 文件) 包含真实 key; 脚本在 stdout/stderr **永不打印真实值**, 但读文件本身不构成泄漏 (脚本在用户机器本地执行)
- `.env.bak` 备份产生第二个含 key 的文件; 用户应记得清理 (`.bak` 未在 `.gitignore`, 用户自行 rm)

## 8. 兼容性 / Breaking changes

**Breaking** (一次性, 无 deprecation window):
- `RHTHS_API_KEY` env var 名消失; 任何外部 system / shell script / CI 仍用旧名的会立即失效
- upstream key 命名变化; 任何自定义 `config/upstreams.local.yaml` (gitignored) 仍用旧 key 的会立即失效
- Python 导入路径变化; 任何 import `src.rhths_client` 的代码会 `ImportError`

**回滚**: `git revert` 即可恢复全部命名, 但用户的 `.env` 已经被迁移脚本改成 `FUYAO_API_KEY=...`, 需要手动改回 (无 deprecation window).

## 9. 验收标准

- [ ] `grep -rin "rhths" src/ config/upstreams.yaml tests/ console.html .env.example .` → **0 hits** (排除 `docs/` 与 `.git/`)
- [ ] `src/fuyao_client.py` 存在, `src/rhths_client.py` 已删除
- [ ] `grep -rn "RHTHS_API_KEY" config/ src/ tests/ .env.example scripts/` → 0 hits (除文档)
- [ ] `grep -rn "FuyaoClient" src/ tests/` → ≥ 4 hits (class + import + tests)
- [ ] `pytest -q` → 118 passed (含 7 个新增 migrate 测试 + 1 个 grep 回归测试)
- [ ] `python scripts/migrate_rhths_env.py --dry-run` 在含 `RHTHS_API_KEY=xxx` 的 `.env` 上输出 `would rename ...`, 不写盘
- [ ] 实际跑 `python scripts/migrate_rhths_env.py` 后, `.env` 含 `FUYAO_API_KEY=xxx`, `.env.bak` 含原 `RHTHS_API_KEY=xxx`
- [ ] 网关启动 (stdio 模式) 不报 missing key 错
- [ ] console.html 在浏览器中仍能显示 4 个 fuyao 上游状态 (无前端报错)
- [ ] `docs/` 历史文件保持不动 (审计追踪完整)
