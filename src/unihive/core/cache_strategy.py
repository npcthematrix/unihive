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
    """是否远程同花顺 HTTP 源：仅以 fuyao_ 开头（myfuyao_xx 不算）。

    只有这类"远程 HTTP"响应值得缓存。本地终端/本地库（mootdx2 /
    tdx_quant / thsdk / omni）本身就是本地数据访问，再加一层缓存纯属多余。
    """
    if not source:
        return False
    return source.startswith("fuyao_")


def is_realtime_ttl(ttl_key: str | None) -> bool:
    """实时行情不缓存：行情要求最新，且本地终端获取本身足够快。"""
    return ttl_key == "realtime_quote"


def is_cacheable_data_source(data_source_type: str | None) -> bool:
    """仅在线数据源可缓存，离线和混合类型不缓存。"""
    if not data_source_type:
        return False
    return data_source_type == "online"


def chain_has_remote_http(chain: list[str] | None) -> bool:
    """路由候选链里是否含远程 HTTP(fuyao_*) 源。

    用于在路由前判断该工具是否值得走缓存读/单飞：只有链上存在远程源，
    缓存命中才有意义。实际是否写缓存仍由命中源 is_fuyao_source 决定。
    """
    return bool(chain) and any(is_fuyao_source(s) for s in chain)