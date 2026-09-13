r"""从 skills/SKILL.md 生成 UniHive 工具定义。

输出: config/tools_tdx_quant.yaml（顶层 tools: 列表）

规则：
- 解析 `### X.Y 标题 `method_name`` 标题（含反引号包裹的 method 名）
- 抓取紧随的 markdown 参数表（列: 参数 | 必填 | 类型 | 说明）
- 按方法名规则推断 dangerous: true
- 按方法名精确匹配推断 cache_ttl_key
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


SKILL_PATH = Path("skills/SKILL.md")
OUTPUT_PATH = Path("config/tools_tdx_quant.yaml")


class CodegenParseError(Exception):
    pass


# ---------- dangerous 推断 ----------

_DANGEROUS_PREFIXES = (
    "order_", "cancel_order",
    "create_sector", "delete_sector", "rename_sector", "clear_sector",
)
_DANGEROUS_EXACT = frozenset({
    "send_message", "send_file", "send_warn", "send_bt_data",
    "send_user_block", "exec_to_tdx", "print_to_tdx",
    "refresh_cache", "refresh_kline", "download_file",
})
_DANGEROUS_STARTSWITH = ("formula_",)


def infer_dangerous(name: str) -> bool:
    if any(name.startswith(p) for p in _DANGEROUS_PREFIXES):
        return True
    if name in _DANGEROUS_EXACT:
        return True
    if any(name.startswith(p) for p in _DANGEROUS_STARTSWITH):
        return True
    return False


# ---------- cache_ttl_key 推断（精确方法名匹配） ----------

_CACHE_TTL_MAP: dict[str, str] = {
    # realtime_quote (10s)
    "get_market_snapshot": "realtime_quote",
    "get_more_info": "realtime_quote",
    "get_gp_one_data": "realtime_quote",
    # historical (3600s)
    "get_market_data": "historical",
    "get_divid_factors": "historical",
    "get_pricevol": "historical",
    # fundamentals (3600s)
    "get_financial_data": "fundamentals",
    "get_financial_data_by_date": "fundamentals",
    "get_stock_info": "fundamentals",
    "get_gb_info": "fundamentals",
    "get_gb_info_by_date": "fundamentals",
    "get_kzz_info": "fundamentals",
    "get_ipo_info": "fundamentals",
    "get_trackzs_etf_info": "fundamentals",
    "get_gpjy_value": "fundamentals",
    "get_gpjy_value_by_date": "fundamentals",
    "get_bkjy_value": "fundamentals",
    "get_bkjy_value_by_date": "fundamentals",
    "get_scjy_value": "fundamentals",
    "get_scjy_value_by_date": "fundamentals",
    # ticker_list (3600s)
    "get_stock_list": "ticker_list",
    "get_sector_list": "ticker_list",
    "get_user_sector": "ticker_list",
    "get_stock_list_in_sector": "ticker_list",
    "get_relation": "ticker_list",
    "get_match_stkinfo": "ticker_list",
    # workday (86400s)
    "get_trading_dates": "workday",
    "get_trading_calendar": "workday",
}


def infer_cache_ttl_key(name: str) -> str | None:
    return _CACHE_TTL_MAP.get(name)


# ---------- 解析 ----------

_METHOD_HEADING = re.compile(
    r"^###\s+\d+\.\d+\s+(?P<title>[^`\n]+?)`(?P<name>[A-Za-z_][A-Za-z0-9_]*)`\s*$",
    re.MULTILINE,
)
_PARAM_ROW = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<req>[^|]+?)\s*\|\s*(?P<type>[^|]+?)\s*\|\s*(?P<desc>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)
# Match `tq.method_name(...)` inside a ```python block; signature can span lines.
_PYTHON_SIG_RE = re.compile(
    r"```python\s*\n(?P<sig>tq\.[A-Za-z_][A-Za-z0-9_]*\([\s\S]*?\))\s*(?:->\s*[^\n]+)?\s*\n```",
    re.MULTILINE,
)
# Match a single param line inside the parens:
#   - `name: type` (required)
#   - `name: type = default` (optional)
#   - `name` (required, no type annotation — fallback "Any")
#   - `name = default` (optional, no type annotation — infer from default)
# Type may contain brackets (List[str], Optional[str]) — match up to `=` or `,` or end-of-line.
_PARAM_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*(?P<type>[^=,#\n]+?))?"
    r"(?:\s*=\s*(?P<default>[^,\n#]+?))?\s*,?\s*$",
    re.MULTILINE,
)
# Bullet-list param description: `- `name`：desc` or `- `name`: desc`
_PARAM_BULLET_RE = re.compile(
    r"^[-*+]\s+`(?P<name>[A-Za-z_][A-Za-z0-9_]*)`\s*[：:]\s*(?P<desc>.+?)\s*$",
    re.MULTILINE,
)


def _extract_python_signature_params(section: str) -> list[dict]:
    """Parse `tq.method(...)` from the ```python block in `section`.

    Returns [{"name", "required", "type", "description"}, ...] preserving signature order.
    Description left empty — enriched later from bullet list or table.
    """
    sig_match = _PYTHON_SIG_RE.search(section)
    if not sig_match:
        return []
    sig = sig_match.group("sig")
    # Strip the leading `tq.method_name(` and trailing `)`
    paren_start = sig.find("(")
    paren_end = sig.rfind(")")
    if paren_start == -1 or paren_end == -1 or paren_end <= paren_start:
        return []
    args_block = sig[paren_start + 1:paren_end]
    if not args_block.strip():
        return []

    params: list[dict] = []
    # Split args by comma at top level (not inside brackets like List[str])
    depth = 0
    current = []
    for ch in args_block:
        if ch == "[" or ch == "(" or ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "]" or ch == ")" or ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            params.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        params.append("".join(current))

    out: list[dict] = []
    for raw in params:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Strip trailing comments
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        m = _PARAM_LINE_RE.match(line)
        if not m:
            continue
        pname = m.group("name")
        ptype_raw = m.group("type")
        has_default = m.group("default") is not None
        default_val = m.group("default").strip() if has_default else None

        if ptype_raw is None or not ptype_raw.strip():
            # No type annotation — infer from default value
            if has_default:
                ptype = _infer_type_from_default(default_val)
            else:
                ptype = "Any"
        else:
            ptype = re.sub(r"\s+", "", ptype_raw.strip())

        out.append({
            "name": pname,
            "required": not has_default,
            "type": ptype,
            "description": "",
        })
    return out


def _infer_type_from_default(default_val: str) -> str:
    """Best-effort type inference for params without annotation."""
    v = default_val.strip()
    if v.startswith(("[", "list(")):
        return "List"
    if v.startswith(("{", "dict(")):
        return "Dict"
    if v.startswith(("'", '"')):
        return "str"
    if v.lower() in ("true", "false"):
        return "bool"
    if v.lower() in ("none", "null"):
        return "Optional"
    try:
        int(v)
        return "int"
    except ValueError:
        pass
    try:
        float(v)
        return "float"
    except ValueError:
        pass
    return "Any"


def _extract_bullet_descriptions(section: str) -> dict[str, str]:
    """Parse `- `param_name`：desc` bullet items into {param_name: desc}."""
    out: dict[str, str] = {}
    for m in _PARAM_BULLET_RE.finditer(section):
        out[m.group("name")] = m.group("desc").strip()
    return out

_METHOD_HEADING_ALT = re.compile(
    r"^####\s+(?P<title>[^`\n]*?)`(?P<name>[A-Za-z_][A-Za-z0-9_]*)`\s*$",
    re.MULTILINE,
)


def _all_method_headings(text: str) -> list[tuple[str, str, int, int, int]]:
    """返回 [(method_name, heading_title, heading_start, heading_end, section_end), ...]。"""
    matches: list[tuple[int, str, str, int]] = []
    for m in _METHOD_HEADING.finditer(text):
        matches.append((m.start(), m.group("name"), m.group("title").strip()))
    for m in _METHOD_HEADING_ALT.finditer(text):
        matches.append((m.start(), m.group("name"), m.group("title").strip()))
    matches.sort(key=lambda x: x[0])
    result: list[tuple[str, str, int, int, int]] = []
    for i, (start, name, title) in enumerate(matches):
        heading_end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        section_end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        # heading_end = end of THIS heading line; section_end = start of NEXT heading
        # We need end of heading line — find first newline after start
        nl = text.find("\n", start)
        if nl == -1:
            nl = len(text)
        result.append((name, title, start, nl, section_end))
    return result


def parse_skill_md(text: str) -> list[dict]:
    """返回 [{"name", "description", "params"}, ...]，按文档顺序。"""
    methods: list[dict] = []
    headings = _all_method_headings(text)
    if not headings:
        return methods

    for name, heading_title, _heading_start, heading_end, section_end in headings:
        section = text[heading_end:section_end]

        description = heading_title or name
        in_code_fence = False
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("```"):
                in_code_fence = not in_code_fence
                continue
            if in_code_fence:
                continue
            if stripped.startswith("|"):
                break
            if stripped.startswith("#"):
                continue
            if stripped.startswith("**"):
                continue
            # Skip bullet-list lines (markdown list items starting with -, *, +)
            if stripped[:2] in ("- ", "* ", "+ "):
                continue
            # Skip blockquote lines
            if stripped.startswith(">"):
                continue
            # Skip horizontal rules (---, ***, ___)
            if stripped in ("---", "***", "___"):
                continue
            description = stripped.rstrip("：:")
            break

        table_lines: list[str] = []
        for line in section.splitlines():
            if line.lstrip().startswith("|"):
                table_lines.append(line)
            elif table_lines:
                break

        params: list[dict] = []
        first_block = "\n".join(table_lines)
        _VALID_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for row in _PARAM_ROW.finditer(first_block):
            cells = row.groupdict()
            pname = cells["name"].strip()
            if not pname or set(pname) <= {"-", ":"}:
                continue
            if pname in {"参数", "字段"} or "字段" in pname or "返回" in pname:
                continue
            if not _VALID_IDENT.match(pname):
                continue
            params.append({
                "name": pname,
                "required": cells["req"].strip().upper() in ("Y", "YES", "TRUE", "是", "✓"),
                "type": cells["type"].strip(),
                "description": cells["desc"].strip(),
            })

        # Fallback: no params table → parse Python signature in the ```python block
        if not params:
            params = _extract_python_signature_params(section)

        # Enrich empty descriptions from bullet list (`- `name`：desc`)
        if any(not p["description"] for p in params):
            bullet_map = _extract_bullet_descriptions(section)
            for p in params:
                if not p["description"] and p["name"] in bullet_map:
                    p["description"] = bullet_map[p["name"]]

        methods.append({
            "name": name,
            "description": description,
            "params": params,
        })

    seen: set[str] = set()
    for m in methods:
        if m["name"] in seen:
            raise CodegenParseError(f"duplicate method: {m['name']}")
        seen.add(m["name"])
    return methods


# ---------- enum 提取 ----------

# 说明文字里出现这些词，表示后面跟的是“示例 / 格式 / 默认值”，不是封闭取值集合
_ENUM_EXAMPLE_MARKERS = ("如", "例如", "比如", "格式", "示例", "默认", "参考")

# 路径 / URL / 表达式碎片 / 列表字面量都不可能是枚举值
_ENUM_ATOM_BLOCKED_CHARS = "/\\:*()[]{}<>=!&|、，"


def _clean_enum_atom(atom: str) -> str | None:
    """规范化单个内联代码值；不是枚举候选时返回 None。"""
    value = atom.strip()
    # 文档常写作 `'1d'` / `"front"`，去掉包裹引号
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value or " " in value:
        return None
    if any(ch in value for ch in _ENUM_ATOM_BLOCKED_CHARS):
        return None
    if value[0].isupper():
        return None
    if "_" in value and value.replace("_", "").islower():
        return None
    if value in ("Y", "N", "Yes", "No", "y", "n"):
        return None
    return value


def extract_enum_from_description(desc: str) -> list[str] | None:
    """从说明文字提取真正的枚举取值。

    只有“封闭取值集合”才应生成 enum。文档里的示例值 / 格式占位符
    （如 `'600519.SH'`、`'YYYYMMDD'`、`'http://...'`）绝不能当成枚举，否则生成 Literal
    后真实入参会被 pydantic 拒绝。
    """
    if "`" not in desc:
        return None
    first = desc.find("`")
    if any(marker in desc[:first] for marker in _ENUM_EXAMPLE_MARKERS):
        return None
    values: set[str] = set()
    for atom in re.findall(r"`([^`]+)`", desc):
        value = _clean_enum_atom(atom)
        if value:
            values.add(value)
    # 只有一个候选值几乎总是“默认值 / 示例”，不是枚举
    if len(values) < 2:
        return None
    return sorted(values)


# ---------- 构造 spec ----------

def build_tool_specs(methods: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for m in methods:
        name = m["name"]
        params_out: list[dict] = []
        for p in m["params"]:
            param: dict = {
                "name": p["name"],
                "required": p["required"],
                "type": p["type"],
                "description": p["description"],
            }
            enum_vals = extract_enum_from_description(p["description"])
            if enum_vals:
                param["enum"] = enum_vals
            params_out.append(param)
        specs.append({
            "name": name,
            "description": m["description"] or "",
            "routing": name,
            "upstream_tool_mapping": {"tdx_quant": name},
            "cache_ttl_key": infer_cache_ttl_key(name),
            "dangerous": infer_dangerous(name),
            "params": params_out,
        })
    return specs


# ---------- 输出 ----------

def render_yaml(specs: list[dict]) -> str:
    body = yaml.safe_dump(
        {"tools": specs},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return (
        f"# Auto-generated from skills/SKILL.md\n"
        f"# DO NOT EDIT — re-run scripts/gen_tdx_quant_tools.py\n"
        f"{body}"
    )


# ---------- CLI ----------

def _stats(specs: list[dict]) -> str:
    dangerous = sum(1 for s in specs if s["dangerous"])
    cached = sum(1 for s in specs if s["cache_ttl_key"])
    return f"{len(specs)} methods ({dangerous} dangerous, {cached} cacheable)"


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
