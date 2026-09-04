"""从 ~/.claude/skills/tdx-tq-local/SKILL.md 生成 UniHive 工具定义。

输出: config/tools_tdx_tq_local.yaml（顶层 tools: 列表）

规则：
- 解析 `#### \`method_name\`: <title>` 标题
- 抓取紧随的 markdown 参数表（列: 参数 | 必填 | 类型 | 说明）
- 按方法名规则推断 dangerous: true
- 缺表 method 仍写入（params=[]）

CLI:
- 默认：写入
- --dry-run：仅打印统计
- --check：与磁盘内容比对，不一致则 exit 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


SKILL_PATH = Path.home() / ".claude" / "skills" / "tdx-tq-local" / "SKILL.md"
OUTPUT_PATH = Path("config/tools_tdx_tq_local.yaml")


class CodegenParseError(Exception):
    pass


# ---------- dangerous 推断 ----------

_DANGEROUS_PREFIXES = ("order_", "cancel_", "create_", "delete_", "clear_", "rename_")
_DANGEROUS_EXACT = frozenset({
    "send_message", "send_file", "send_warn", "send_bt_data",
    "send_user_block", "exec_to_tdx",
    "refresh_cache", "refresh_kline", "download_file",
})
_DANGEROUS_STARTSWITH = ("formula_set_data",)


def infer_dangerous(name: str) -> bool:
    if any(name.startswith(p) for p in _DANGEROUS_PREFIXES):
        return True
    if name in _DANGEROUS_EXACT:
        return True
    if any(name.startswith(p) for p in _DANGEROUS_STARTSWITH):
        return True
    return False


# ---------- 解析 ----------


_METHOD_HEADING = re.compile(r"^####\s+`([A-Za-z_][A-Za-z0-9_]*)`\s*:", re.MULTILINE)
_PARAM_ROW = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<req>[^|]+?)\s*\|\s*(?P<type>[^|]+?)\s*\|\s*(?P<desc>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)


def parse_skill_md(text: str) -> list[dict]:
    """返回 [{"name", "description", "params"}, ...]，按文档顺序。"""
    methods: list[dict] = []
    matches = list(_METHOD_HEADING.finditer(text))
    if not matches:
        return methods

    for i, m in enumerate(matches):
        name = m.group(1)
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[section_start:section_end]

        # description: 跳过紧跟 heading 的 inline title 行, 取第一个真正段落
        description = ""
        skipped_title = False
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|"):
                break
            if stripped.startswith("#"):
                continue
            if not skipped_title:
                skipped_title = True
                continue
            has_period = stripped.endswith("。") or stripped.endswith(".")
            if has_period:
                description = stripped
            else:
                description = stripped.rstrip("：:") + "。"
            break

        # 参数表
        params: list[dict] = []
        for row in _PARAM_ROW.finditer(section):
            cells = row.groupdict()
            pname = cells["name"].strip()
            if pname in {"参数", "字段", "---"} or pname.startswith("---"):
                continue
            params.append({
                "name": pname,
                "required": cells["req"].strip().upper() in ("Y", "YES", "TRUE", "是", "✓"),
                "type": cells["type"].strip(),
                "description": cells["desc"].strip(),
            })

        methods.append({
            "name": name,
            "description": description,
            "params": params,
        })

    # 检查重复
    seen = set()
    for m in methods:
        if m["name"] in seen:
            raise CodegenParseError(f"duplicate method: {m['name']}")
        seen.add(m["name"])

    return methods


# ---------- 构造 spec ----------


def build_tool_specs(methods: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for m in methods:
        name = m["name"]
        specs.append({
            "name": name,
            "description": m["description"] or name,
            "routing": name,
            "upstream_tool_mapping": {"tdx_tq_local": name},
            "cache_ttl_key": None,
            "dangerous": infer_dangerous(name),
            "params": [
                {"name": p["name"], "required": p["required"], "type": p["type"]}
                for p in m["params"]
            ],
        })
    return specs


# ---------- 输出 ----------


def render_yaml(specs: list[dict]) -> str:
    body = yaml.safe_dump({"tools": specs}, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"# Auto-generated from ~/.claude/skills/tdx-tq-local/SKILL.md\n# DO NOT EDIT — re-run scripts/gen_tdx_tq_local_tools.py\n{body}"


# ---------- CLI ----------


def _stats(specs: list[dict]) -> str:
    dangerous = sum(1 for s in specs if s["dangerous"])
    return f"{len(specs)} methods ({dangerous} dangerous)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill-path", default=str(SKILL_PATH))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    skill = Path(args.skill_path)
    if not skill.exists():
        print(f"SKILL.md not found: {skill}", file=sys.stderr)
        return 2
    text = skill.read_text(encoding="utf-8")

    try:
        methods = parse_skill_md(text)
    except CodegenParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1

    specs = build_tool_specs(methods)
    out = render_yaml(specs)

    print(_stats(specs))

    if args.dry_run:
        return 0

    if args.check:
        existing = Path(args.output).read_text(encoding="utf-8") if Path(args.output).exists() else ""
        if existing != out:
            print(f"drift detected: {args.output}", file=sys.stderr)
            return 1
        return 0

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(out, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
