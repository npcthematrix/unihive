"""FUYAO cache 配置模型 — endpoint 级 default + 工具级 override。

设计：
- fuyao_cache.endpoints.<endpoint>.default   必填整数秒
- fuyao_cache.endpoints.<endpoint>.override  工具名 → 秒数 (可空)
- 未填的 endpoint 视为未配置,运行时回退到 cache_ttl_key 全局
"""
from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


# 4 个 endpoint 名字固化。fuyao_ashare / fuyao_index / fuyao_meta / fuyao_fund
# 与 upstreams.yaml 的 upstream name 一致 (upstream_tool_mapping 第一位)。
FUYAO_ENDPOINT_NAMES: tuple[str, ...] = (
    "fuyao_ashare",
    "fuyao_index",
    "fuyao_meta",
    "fuyao_fund",
)


class FuyaoEndpointConfig(BaseModel):
    """单个 endpoint 的 default + override。"""

    model_config = ConfigDict(extra="forbid")

    default: int = Field(..., ge=0, description="endpoint 级默认 TTL 秒数, 必须 ≥ 0")
    override: dict[str, int] = Field(
        default_factory=dict,
        description="工具级 override, 键为 MCP tool 名称, 值为秒数",
    )

    @field_validator("override")
    @classmethod
    def _validate_override(cls, v: dict[str, int]) -> dict[str, int]:
        for tool_name, seconds in v.items():
            if not isinstance(seconds, int) or seconds < 0:
                raise ValueError(
                    f"override[{tool_name!r}] 必须是 ≥ 0 的整数秒, got {seconds!r}"
                )
        return dict(sorted(v.items()))  # 排序便于落盘 diff 稳定


class FuyaoCacheConfig(BaseModel):
    """FUYAO 缓存配置根节点 — 嵌套在 GatewayConfig.fuyao_cache 字段。"""

    model_config = ConfigDict(extra="forbid")

    endpoints: dict[str, FuyaoEndpointConfig] = Field(
        default_factory=dict,
        description="endpoint → default + override 配置",
    )

    @field_validator("endpoints")
    @classmethod
    def _validate_endpoints(cls, v: Mapping[str, FuyaoEndpointConfig]) -> dict[str, FuyaoEndpointConfig]:
        unknown = set(v.keys()) - set(FUYAO_ENDPOINT_NAMES)
        if unknown:
            raise ValueError(
                f"未知的 fuyao endpoint: {sorted(unknown)}; "
                f"只支持 {list(FUYAO_ENDPOINT_NAMES)}"
            )
        # 排序保证落盘 + GET 返回顺序稳定
        return {name: v[name] for name in FUYAO_ENDPOINT_NAMES if name in v}

    def total_override_count(self) -> int:
        return sum(len(ep.override) for ep in self.endpoints.values())

    def is_empty(self) -> bool:
        return not self.endpoints or all(ep.default == 0 and not ep.override for ep in self.endpoints.values())


def default_fuyao_cache_config() -> FuyaoCacheConfig:
    """工厂默认 — 与旧 cache.ttl 风格对齐的合理 TTL。"""
    return FuyaoCacheConfig.model_validate({
        "endpoints": {
            "fuyao_ashare": {"default": 300, "override": {}},
            "fuyao_index": {"default": 3600, "override": {}},
            "fuyao_meta": {"default": 86400, "override": {}},
            "fuyao_fund": {"default": 600, "override": {}},
        }
    })
