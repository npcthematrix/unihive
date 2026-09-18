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


def get_fuyao_endpoint_for_tool(config: dict, tool_name: str) -> str | None:
    """根据 upstream_tool_mapping 查 tool_name 属于哪个 fuyao endpoint。

    仅当 routing chain 上**唯一**上游是 fuyao_* 才返回 endpoint 名字 —
    多个上游混合时返回 None, 走 ttl_key 全局, 避免给非 fuyao 工具强加
    fuyao_cache TTL。

    Returns:
        endpoint name (如 "fuyao_ashare") 或 None。
    """
    if not tool_name:
        return None
    mapping = config.get("upstream_tool_mapping") or {}
    chain = mapping.get(tool_name)
    if not isinstance(chain, dict) or not chain:
        return None
    upstream_names = [name for name in chain.keys() if name]
    fuyao_only = [n for n in upstream_names if n.startswith("fuyao_")]
    if not fuyao_only or len(fuyao_only) != len(upstream_names):
        return None
    # 优先取 chain 顺序首位 (主路由)
    return fuyao_only[0]


def fuyao_ttl_for(
    config: dict,
    tool_name: str,
    *,
    endpoint: str | None = None,
) -> int | None:
    """FUYAO 工具级 TTL override 查询 (2026-09-18 P1 shape)。

    命中顺序:

    1. 该工具路由必须是纯 fuyao_* 链 (endpoint gate, 混合链不加 fuyao TTL);
    2. ``fuyao_cache.tools[tool_name]`` 工具级秒数 (0 = 显式不缓存);
    3. None — 调用方回退到 ttl_for(cache.ttl[ttl_key])。

    Args:
        config: 已加载的 gateway 配置 (含 fuyao_cache / upstream_tool_mapping;
            运行时为 YAML 基线合并 data/config.db 覆盖层后的 dict)
        tool_name: MCP tool 名称 (gateway 层, 非 routing_key)
        endpoint: 可选 — 外部已查到时直接传入, 节省一次 mapping 查询

    Returns:
        秒数 (含 0 表示「不缓存」) 或 None (未配置工具级 override)
    """
    if endpoint is None:
        endpoint = get_fuyao_endpoint_for_tool(config, tool_name)
    if not endpoint:
        return None
    fuyao_cache = config.get("fuyao_cache") or {}
    tools = fuyao_cache.get("tools") or {}
    value = tools.get(tool_name)
    if value is None:
        return None
    return int(value)


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


def chain_has_remote_http(chain: list[str] | None) -> bool:
    """路由候选链里是否含远程 HTTP(fuyao_*) 源。

    用于在路由前判断该工具是否值得走缓存读/单飞：只有链上存在远程源，
    缓存命中才有意义。实际是否写缓存仍由命中源 is_fuyao_source 决定。
    """
    return bool(chain) and any(is_fuyao_source(s) for s in chain)