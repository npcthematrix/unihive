"""
缓存策略模块 - 缓存 key 生成、 数据源可缓存性判定、 TTL 查询

从 GatewayServer 中抽出，独立模块便于测试与复用。
所有函数都是纯函数，无实例状态。
"""
import hashlib
import json
from typing import Any


def cache_key(name: str, params: dict[str, Any]) -> str:
    """生成稳定的缓存 key。使用完整 SHA256 哈希避免碰撞。"""
    payload = json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{name}:{h}"


def ttl_for(config: dict, ttl_key: str) -> int | None:
    """从 config.cache.ttl 查 TTL（秒）；未配置返回 None。"""
    ttl = config.get("cache", {}).get("ttl", {})
    return ttl.get(ttl_key)


def is_cacheable_source(source: str | None) -> bool:
    """缓存策略：仅缓存 FUYAO 和 MooTDX2 在线接口。"""
    if not source:
        return False
    return source.startswith("fuyao_") or source == "mootdx2"


def is_fuyao_source(source: str | None) -> bool:
    """是否 FUYAO 源：仅以 fuyao_ 开头（myfuyao_xx 不算）。"""
    if not source:
        return False
    return source.startswith("fuyao_")


def is_realtime_ttl(ttl_key: str | None) -> bool:
    """实时接口不缓存（realtime_quote TTL 极短，无意义）。"""
    return ttl_key == "realtime_quote"


def is_cacheable_data_source(data_source_type: str | None) -> bool:
    """仅在线数据源可缓存，离线和混合类型不缓存。"""
    if not data_source_type:
        return False
    return data_source_type == "online"