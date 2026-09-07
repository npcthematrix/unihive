"""迁移 upstreams.yaml：仅在 tools: 列表下每个 fuyao tool 的 inline dict 中插入
`data_source_type: online`。

判定规则：upstream_tool_mapping[routing] 的 value 是 {fuyao_*: ...} 的即为 fuyao tool。

实现：行级正则编辑，避免 ruamel.yaml 重写整个文件（会破坏
upstream_tool_mapping 的对齐空格），仅修改 tools: 列表的 inline dict 行。
"""
import re
from pathlib import Path

YAML_PATH = Path("config/upstreams.yaml")

# 匹配 tools: 列表下 inline dict 行的 name 字段
TOOL_LINE_RE = re.compile(
    r'^(  - \{name: ([a-z_]+), )(.*)(\}\s*)$',
)


def parse_mapping(text: str) -> dict[str, dict[str, str]]:
    """简单解析 upstream_tool_mapping 节，得到 routing → upstream_dict。"""
    mapping: dict[str, dict[str, str]] = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("upstream_tool_mapping:"):
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # 下一个顶级 key 就退出
            if line and not line.startswith(" ") and ":" in line and not line.startswith("  "):
                break
            m = re.match(r"^  ([a-z_]+):\s*\{([^}]+)\}\s*$", line)
            if m:
                routing, body = m.group(1), m.group(2)
                # 解析 {fuyao_xxx: tool_name}
                pairs = re.findall(r"(\w+):\s*(\S+)", body)
                mapping[routing] = dict(pairs)
    return mapping


def has_fuyao_upstream(routing: str, mapping: dict[str, dict[str, str]]) -> bool:
    upstream_dict = mapping.get(routing) or {}
    return any(u.startswith("fuyao_") for u in upstream_dict.keys())


def insert_data_source_type(tool_body: str) -> tuple[str, bool]:
    """在 inline dict 中插入 `data_source_type: online`。

    插入位置：
    - 有 cache_ttl_key → 插在 cache_ttl_key 之后
    - 无 cache_ttl_key → 插在 params 之后（保持有序）

    Returns: (new_body, changed)
    """
    if "data_source_type:" in tool_body:
        return tool_body, False

    if "cache_ttl_key:" in tool_body:
        # 插在 cache_ttl_key: xxx 之后
        new = re.sub(
            r"(cache_ttl_key: \w+)",
            r"\1, data_source_type: online",
            tool_body,
            count=1,
        )
    else:
        # 插在最后一个 params [...] 之后（用 ] 作为锚点，最后一个 ] 之前为 params 列表）
        # 简化：直接在末尾 } 之前插入
        # 实际：把最后一个 "]" 之后 ", " 改为 ", data_source_type: online, "，
        #      但末尾的 ] 之后没有逗号，所以需要找 ", " 模式
        new = re.sub(
            r"(\])\s*",
            r"\1, data_source_type: online",
            tool_body,
            count=1,
        )
    return new, new != tool_body


def main() -> None:
    original = YAML_PATH.read_text(encoding="utf-8")
    mapping = parse_mapping(original)

    lines = original.splitlines(keepends=True)
    in_tools = False
    counts = {"online": 0, "skipped": 0, "already_set": 0}
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("tools:"):
            in_tools = True
            new_lines.append(line)
            continue
        if in_tools:
            # tools 节结束的标志：非空、非注释、非缩进行（blank line 不算结束）
            if (
                line
                and line.strip()
                and not line.startswith("  ")
                and not line.startswith("#")
                and not stripped.startswith("tools")
            ):
                in_tools = False

        m = TOOL_LINE_RE.match(line.rstrip("\n"))
        if in_tools and m:
            prefix, name, body, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
            routing = name
            if not has_fuyao_upstream(routing, mapping):
                counts["skipped"] += 1
                new_lines.append(line)
                continue
            new_body, changed = insert_data_source_type(body)
            if not changed:
                counts["already_set"] += 1
                new_lines.append(line)
                continue
            new_line = f"{prefix}{new_body}{suffix}\n"
            new_lines.append(new_line)
            counts["online"] += 1
        else:
            new_lines.append(line)

    new_text = "".join(new_lines)
    if new_text != original:
        YAML_PATH.write_text(new_text, encoding="utf-8")

    print(f"Migration complete: {counts}")


if __name__ == "__main__":
    main()