"""FUYAO 缓存配置模型 — 工具级 TTL override (2026-09-18 P1 shape)。

设计:
- ``fuyao_cache.tools.<gateway_tool_name>`` = 秒数, 扁平工具级覆盖;
- 持久化在 data/config.db 的 config_overlay 表 (点路径), 不再写 YAML;
- 未列出的工具回退到 spec 的 ``cache_ttl_key`` 全局档位;
- 值 0 表示该工具显式「不缓存」。

历史上曾设计过 ``endpoints.<ep>.default/override`` 两层结构, 但从未真正
落盘 (upstreams.yaml 无 fuyao_cache 段), P1 切 SQLite 时定稿为单层 tools。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class FuyaoCacheConfig(BaseModel):
    """FUYAO 缓存配置根节点 — ``{"tools": {工具名: 秒数}}``。"""

    model_config = ConfigDict(extra="forbid")

    # StrictInt: JSON 里 "60" / true / 1.5 一律拒绝, 不做静默强转
    tools: dict[str, StrictInt] = Field(
        default_factory=dict,
        description="工具级 TTL override, 键为 gateway MCP 工具名, 值为 ≥0 秒数",
    )

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, v: dict[str, int]) -> dict[str, int]:
        for tool_name, seconds in v.items():
            if not isinstance(tool_name, str) or not tool_name:
                raise ValueError(f"工具名必须是非空字符串, got {tool_name!r}")
            # bool 是 int 子类, 显式挡掉 true/false
            if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
                raise ValueError(
                    f"tools[{tool_name!r}] 必须是 ≥ 0 的整数秒, got {seconds!r}"
                )
        return dict(sorted(v.items()))  # 排序保证 GET / diff 顺序稳定

    def override_count(self) -> int:
        return len(self.tools)

    def is_empty(self) -> bool:
        return not self.tools
