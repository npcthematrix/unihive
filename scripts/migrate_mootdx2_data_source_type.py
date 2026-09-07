"""迁移 tools_mootdx2.yaml：为每个 tool 增加 data_source_type 字段，并从 description 中删除
【· 在线】/【· 离线】/【· 混合】 marker 行。

判定规则：从 description 第一行读取（去除缩进后以 【· X】 开头的行）。

字段插入位置：cache_ttl_key 之后（与 cache_ttl_key 同属分类元数据，逻辑相近）。
"""
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

YAML_PATH = Path("config/tools_mootdx2.yaml")

MARKER_MAP = {
    "【· 在线】": "online",
    "【· 离线】": "offline",
    "【· 混合】": "mixed",
}


def classify_and_strip_description(description: str | None) -> tuple[str | None, str | None]:
    """从 description 字符串中抽取首行 marker，返回 (清洗后 description, data_source_type)。"""
    if not description:
        return description, None
    lines = description.splitlines(keepends=False)
    if not lines:
        return description, None
    first_stripped = lines[0].strip()
    for marker, value in MARKER_MAP.items():
        if first_stripped == marker:
            # 删除 marker 行和其后的空行
            remaining = lines[1:]
            while remaining and not remaining[0].strip():
                remaining.pop(0)
            return "\n".join(remaining) if remaining else "", value
    return description, None


def main() -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    yaml.indent = 2
    # 序列条目缩进 4 空格（使 - name: 保持为 "  - name:"）
    yaml.sequence_indent = 4
    yaml.sequence_dash_offset = 2

    with YAML_PATH.open(encoding="utf-8") as f:
        data = yaml.load(f)

    tools = data.get("tools", [])
    counts = {"online": 0, "offline": 0, "mixed": 0, "unchanged": 0}

    for tool in tools:
        desc = tool.get("description")
        cleaned, dst = classify_and_strip_description(desc)
        if dst is None:
            counts["unchanged"] += 1
            continue

        # 更新 description（ruamel 会自动保留 block scalar style）
        tool["description"] = cleaned

        # 在 cache_ttl_key 之后插入 data_source_type
        keys = list(tool.keys())
        if "cache_ttl_key" in keys:
            idx = keys.index("cache_ttl_key")
            # 插入到 cache_ttl_key 之后
            new_tool = CommentedMap()
            for k, v in tool.items():
                new_tool[k] = v
                if k == "cache_ttl_key":
                    new_tool["data_source_type"] = dst
            # 替换原 tool
            tool.clear()
            tool.update(new_tool)
        else:
            tool["data_source_type"] = dst

        counts[dst] += 1

    with YAML_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

    print(f"Migration complete: {counts}")
    print(f"Total tools: {len(tools)}")


if __name__ == "__main__":
    main()
