"""
缓存策略模块 - 缓存 key 生成、 数据源可缓存性判定、 TTL 查询

从 GatewayServer 中抽出，独立模块便于测试与复用。
所有函数都是纯函数，无实例状态。
"""
import hashlib
import json
import re
import time
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
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


# ===== 参数(日期)感知 TTL（2026-09-21）=====

_SHANGHAI_TZ = timezone(timedelta(hours=8))

# 归一化后 params 里可能承载"截至日期(as-of date)"的键，按优先级取第一个。
# 不同工具归一后日期落在不同键：
#   - 涨停/跌停/炸板池（normalize: date_ms）→ date_ms，上海零点毫秒戳；
#   - 龙虎榜/竞价基准/热门股历史（无 normalize）→ date，原始 "YYYY-MM-DD" 串；
#   - 热门股排名趋势 → end_date，归一为 8 位 "YYYYMMDD" 串。
# 刻意不含 start_date：热门股排名趋势语义是「start_date → 今天」(end_date
# 可省), 只有 start_date 时结果含今日仍在变的排名, 误判冻结会把 TTL 锁 10 年。
_AS_OF_DATE_KEYS = ("date_ms", "date", "end_date")

# 过去交易日冻结快照所用的 TTL 档名（cache.ttl.historical_immutable）。
HISTORICAL_IMMUTABLE_KEY = "historical_immutable"

# 哪些 ttl_key 的工具允许"过去日期 → 冻结档"提升。只放开 special_data：
# 当日特色榜单在历史日收盘后冻结。刻意不含 historical（前复权K线会因未来
# 除权整体平移）与 fund_history，避免误冻。
_FREEZABLE_TTL_KEYS = frozenset({"special_data"})


def shanghai_today(now: float | None = None) -> date_cls:
    """当前上海(北京)时区的日历日。"""
    if now is None:
        now = time.time()
    return datetime.fromtimestamp(now, tz=_SHANGHAI_TZ).date()


def _coerce_as_of_date(value: Any) -> date_cls | None:
    """把单个归一化日期值转成日历日；无法识别返回 None。

    - int/float: 上海零点毫秒戳（date_ms）；
    - str: "YYYY-MM-DD" / "YYYY/MM/DD" / "YYYYMMDD"。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=_SHANGHAI_TZ).date()
    if isinstance(value, str):
        digits = re.sub(r"\D", "", value)
        if len(digits) == 8:
            try:
                return datetime.strptime(digits, "%Y%m%d").date()
            except ValueError:
                return None
    return None


def as_of_date(params: dict[str, Any]) -> date_cls | None:
    """从归一化 params 解析本次调用的 as-of 日期；无日期返回 None。"""
    for key in _AS_OF_DATE_KEYS:
        if key in params and params[key] is not None:
            parsed = _coerce_as_of_date(params[key])
            if parsed is not None:
                return parsed
    return None


def is_frozen_snapshot(
    params: dict[str, Any],
    *,
    today: date_cls | None = None,
) -> bool:
    """as-of 日期是否严格早于今天（历史交易日 → 数据收盘后冻结）。

    当天、未来（池子通常返空）、无日期（多数工具默认当天）均返回 False。
    """
    day = as_of_date(params)
    if day is None:
        return False
    if today is None:
        today = shanghai_today()
    return day < today


def is_historical_special_data(
    ttl_key: str | None,
    params: dict[str, Any],
    *,
    today: date_cls | None = None,
) -> bool:
    """该 special_data 工具本次是否查"过去交易日"（可按冻结档长缓存）。"""
    return (
        ttl_key in _FREEZABLE_TTL_KEYS
        and is_frozen_snapshot(params, today=today)
    )


# ===== 短档 TTL 的交易时段感知（2026-09-21）=====
#
# 任何配置值 ≤ SESSION_AWARE_TTL_MAX（1 小时）的 TTL 档，都按交易时段
# 感知处理（当前为 special_data 300s / index_data 60s）：
#   - 交易时段内，数据可能变化 → 用该分钟级短档；
#   - 非交易时段，数据不再变化 → 不必按分钟重拉，缓存到「下一次数据会
#     变化的边界」为止（开盘/午后开盘/当日结束），而不是机械地到当天
#     24:00 —— 那样会把昨日数据一路缓存到今天开盘之后。
# A 股连续竞价时段（上海/深圳一致）；忽略法定节假日（节假日落在工作日
# 会按交易日报短档，影响仅为多几次无害重拉）。
SESSION_AWARE_TTL_MAX = 3600
_MORNING_OPEN = (9, 30)
_MORNING_CLOSE = (11, 30)
_AFTERNOON_OPEN = (13, 0)
_MARKET_CLOSE = (15, 0)


def _seconds_until(now_dt: datetime, hour: int, minute: int) -> int:
    """now_dt（上海时区）到当天 hour:minute 的整秒差（下限 1 秒）。"""
    target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return max(1, int((target - now_dt).total_seconds()))


def _seconds_to_midnight(now_dt: datetime) -> int:
    """到下一个本地零点的整秒差（下限 1 秒）。"""
    tomorrow = datetime(
        now_dt.year, now_dt.month, now_dt.day, tzinfo=now_dt.tzinfo
    ) + timedelta(days=1)
    return max(1, int((tomorrow - now_dt).total_seconds()))


def intraday_session_ttl(
    session_ttl: int,
    *,
    now: float | None = None,
    post_close_boundary: tuple[int, int] | None = None,
) -> int:
    """短档（≤1h）TTL 请求的时段感知 TTL（秒）。传入该短档的配置秒数。

    规则（上海时区）：
      - 周末：市场全天休市，数据自上周五收盘冻结 → 缓存到次日零点；
      - 开盘前(00:00–9:30)：当前是昨日/空数据，9:30 会变 → 缓存到 9:30；
      - 上午盘(9:30–11:30)：分钟级短档；
      - 午间休市(11:30–13:00)：数据冻结 → 缓存到 13:00；
      - 下午盘(13:00–15:00)：分钟级短档；
      - 收盘后(15:00–24:00)：当日定格 → 缓存到次日零点；若给了
        ``post_close_boundary`` (时,分) 且当前未到该点, 则只缓存到该点
        —— 龙虎榜等盘后才发布的榜单, 15:00 拿到的还是前一交易日数据。
    """
    if now is None:
        now = time.time()
    now_dt = datetime.fromtimestamp(now, tz=_SHANGHAI_TZ)

    # 周六=5, 周日=6：全天休市，缓存到次日零点（跨周末逐日自愈）。
    if now_dt.weekday() >= 5:
        return _seconds_to_midnight(now_dt)

    minutes = now_dt.hour * 60 + now_dt.minute
    open_am = _MORNING_OPEN[0] * 60 + _MORNING_OPEN[1]
    close_am = _MORNING_CLOSE[0] * 60 + _MORNING_CLOSE[1]
    open_pm = _AFTERNOON_OPEN[0] * 60 + _AFTERNOON_OPEN[1]
    close_pm = _MARKET_CLOSE[0] * 60 + _MARKET_CLOSE[1]

    if minutes < open_am:
        return _seconds_until(now_dt, *_MORNING_OPEN)
    if minutes < close_am:
        return int(session_ttl)
    if minutes < open_pm:
        return _seconds_until(now_dt, *_AFTERNOON_OPEN)
    if minutes < close_pm:
        return int(session_ttl)
    if post_close_boundary is not None:
        bh, bm = post_close_boundary
        if minutes < bh * 60 + bm:
            return _seconds_until(now_dt, bh, bm)
    return _seconds_to_midnight(now_dt)


# M9 (2026-09-21 round-2): 盘后实际发布时间晚于收盘的 special_data 工具。
# 龙虎榜交易所盘后披露, 实际 17:30–18:00 才可查当日; 不给这个边界,
# 15:00 的缓存 (默认日期仍解析为前一交易日) 会一路锁到次日零点。
_POST_CLOSE_PUBLICATION_BOUNDARY: dict[str, tuple[int, int]] = {
    "a_share_special_data_dragon_tiger_list": (18, 0),
}

# M8: 集合竞价快照在 9:25 才定格; 9:15–9:25 集合竞价期间数据逐秒变化,
# 空/半成品快照若按 86400 长档锁死, 全天都拿不到真实竞价结果。
_AUCTION_START = (9, 15)
_AUCTION_FIXED = (9, 25)
_AUCTION_INTRADAY_TTL = 60


def auction_snapshot_ttl(base_ttl: int, *, now: float | None = None) -> int:
    """集合竞价快照 (base 为 1 天长档) 的时段感知 TTL。

    - 周末：休市 → 缓存到次日零点 (跨周末逐日自愈, 不把空结果带到周一);
    - 00:00–9:15：昨日/空数据, 9:15 开始竞价 → 缓存到 9:15;
    - 9:15–9:25：竞价进行中 → 60s 短档;
    - 9:25 之后：竞价定格全天不变 → base 长档。
    """
    if now is None:
        now = time.time()
    now_dt = datetime.fromtimestamp(now, tz=_SHANGHAI_TZ)
    if now_dt.weekday() >= 5:
        return _seconds_to_midnight(now_dt)
    minutes = now_dt.hour * 60 + now_dt.minute
    start_min = _AUCTION_START[0] * 60 + _AUCTION_START[1]
    fixed_min = _AUCTION_FIXED[0] * 60 + _AUCTION_FIXED[1]
    if minutes < start_min:
        return _seconds_until(now_dt, *_AUCTION_START)
    if minutes < fixed_min:
        return _AUCTION_INTRADAY_TTL
    return int(base_ttl)


# M7: "省略日期 → 返回当年全部交易日" 的工具: 无日期入参时缓存必须在
# 年末断开, 否则 12 月缓存的 2026 全年日历 (workday 档 180 天) 在
# 2027-01 仍被命中, 调用方拿到旧一年数据。
_YEAR_BOUNDARY_TOOLS = frozenset({"a_share_calendar_trading_days"})


def _seconds_to_year_boundary(now: float | None = None) -> int:
    """到下一个公历年 1 月 1 日零点 (上海时区) 的整秒数, 下限 1 秒。"""
    if now is None:
        now = time.time()
    now_dt = datetime.fromtimestamp(now, tz=_SHANGHAI_TZ)
    next_jan1 = datetime(now_dt.year + 1, 1, 1, tzinfo=_SHANGHAI_TZ)
    return max(1, int((next_jan1 - now_dt).total_seconds()))


def effective_ttl(
    config: dict,
    tool_name: str,
    ttl_key: str | None,
    params: dict[str, Any],
    *,
    now: float | None = None,
) -> int | None:
    """解析本次调用的 TTL（秒），None = 不缓存。命中顺序：

    1. ``fuyao_cache.tools[tool_name]`` 工具级 override —— 手工指定，最高优先；
    2. ``special_data`` 传了严格早于今天的日期 → ``cache.ttl.
       historical_immutable``（≈永久）；
    3. 该 ttl_key 的全局档：
       a. 值 ≤1 小时（交易时段短档）→ 交易时段用该值、非交易时段缓存到
          下一变化边界（见 ``intraday_session_ttl``）; 龙虎榜等盘后发布
          榜单额外缓存到 18:00 发布边界;
       b. 集合竞价快照 → 9:25 定格前按竞价时段缩短（M8）；
       c. 交易日历无日期入参 → 不超过年末边界（M7）；
       d. 否则直接用该全局档。
    """
    override = fuyao_ttl_for(config, tool_name)
    if override is not None:
        return override
    if ttl_key in _FREEZABLE_TTL_KEYS and is_frozen_snapshot(params):
        frozen = ttl_for(config, HISTORICAL_IMMUTABLE_KEY)
        if frozen is not None:
            return frozen
    base = ttl_for(config, ttl_key) if ttl_key else None
    if base is None:
        return None
    # 0 / 负数 = 显式不缓存 (Round-12 H2 合同), 必须在时段感知换算之前
    # 短路; 否则 base=0 也满足 ≤1h, 会被 intraday_session_ttl 提升成
    # 「缓存到下一变化边界」的长 TTL, 显式不缓存反而缓存得最久。
    if base <= 0:
        return None
    if base <= SESSION_AWARE_TTL_MAX:
        return intraday_session_ttl(
            base,
            now=now,
            post_close_boundary=_POST_CLOSE_PUBLICATION_BOUNDARY.get(tool_name),
        )
    if tool_name == "a_share_auction_snapshot":
        # M8: 9:25 定格前按短档/边界缓存, 定格后才给全天长档。
        return auction_snapshot_ttl(base, now=now)
    if tool_name in _YEAR_BOUNDARY_TOOLS and not params.get("date"):
        # M7: 无日期入参 (返回当年全年) 时不超过年末边界。
        return min(base, _seconds_to_year_boundary(now))
    return base


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


# 完全不参与缓存的上游（2026-09-21 用户指定）：
# 通达信量化终端 (tdx_quant) 与 THSDK 本地终端 (thsdk)。这些本地终端数据
# 实时性要求高、本地取数本身足够快，不套任何缓存（不读、不单飞、不写）。
NON_CACHED_UPSTREAMS = frozenset({"tdx_quant", "thsdk"})


def chain_is_cache_exempt(chain: list[str] | None) -> bool:
    """路由候选链是否全部由「免缓存上游」构成（纯 tdx_quant / thsdk 链）。

    仅当链上每个源都免缓存才返回 True；混合链（含 fuyao 等）不算，
    以便远程降级响应仍可正常缓存。
    """
    return bool(chain) and all(source in NON_CACHED_UPSTREAMS for source in chain)


def chain_has_remote_http(chain: list[str] | None) -> bool:
    """路由候选链里是否含远程 HTTP(fuyao_*) 源。

    用于在路由前判断该工具是否值得走缓存读/单飞：只有链上存在远程源，
    缓存命中才有意义。实际是否写缓存仍由命中源 is_fuyao_source 决定。
    """
    return bool(chain) and any(is_fuyao_source(s) for s in chain)

def normalize_ttl(ttl: Any) -> int | None:
    """把配置里的 TTL 值归一化成正整数秒, 或 None(不缓存)。

    合同 (见 config 的 fuyao_cache 段与 cache_strategy 模块):
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
