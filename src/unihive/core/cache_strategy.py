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
    # Round-12 簇E: PUT 侧有 pydantic StrictInt 门, 但 YAML 出厂模板/
    # 手改库内行可能绕过。裸 int() 对 "1h" 报无名 ValueError, 在请求路径
    # 上变成 500; 这里报出点路径字段名, 由调用方降级。
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"fuyao_cache.tools.{tool_name} 必须是 ≥0 的整数秒, "
            f"got {value!r} ({type(value).__name__})"
        )
    return value


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

def normalize_ttl(ttl: Any) -> int | None:
    """把配置里的 TTL 值归一化成正整数秒, 或 None(不缓存)。

    合同 (见 fuyao_cache 模型与 cache_strategy 模块):
      - None: 未配置 → 不缓存;
      - 0 或负数: 显式不缓存 (Round-12 H2: 旧链路把 0 透传到
        ``Cache.set``, ``if ttl`` 判定为假 → expires_at=NULL → 永久缓存);
      - 正整数/正浮点: 秒数 (浮点向下取整);
      - bool / 字符串 / 其他类型: 配置错误, 抛 TypeError/ValueError,
        调用方应记 warning 后按"不缓存"降级, 而不是静默写错值。
    """
    if ttl is None:
        return None
    # bool 是 int 子类, 必须先挡
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        raise TypeError(f"TTL 必须是 int/float 秒数, got {ttl!r} ({type(ttl).__name__})")
    if ttl != ttl or ttl in (float("inf"), float("-inf")):
        raise ValueError(f"TTL 必须是有限数值, got {ttl!r}")
    if ttl <= 0:
        return None
    return int(ttl)
