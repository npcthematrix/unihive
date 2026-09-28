"""MooTDX2 MCP Client 基于 mootdx2 库"""
import asyncio
import datetime
import contextlib
import functools
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .upstream_client import ToolResult, UpstreamStatus
from ..core.blocking_pool import get_mootdx2_executor
from ..core.config_db import resolve_project_path
from ..models.mootdx2_errors import MooTDXErrorType, error_to_result
from .index_registry import INDEX_KIND, STOCK_KIND, parse_symbol, to_reader_symbol
from ._l1_slot import _L1Slot


def _bare_000_ambiguity_warn(code: str) -> str:
    """裸 000xxx 在通用日线入口被按深市股票解析时的消歧提示。

    000001/000002 等是跨市场同代码标的（深市股票 vs 上证指数），
    静默返回"另一个标的"的数据最难发现，故在 warn 里给出指数入口指引。
    """
    return (
        f"代码 {code} 为跨市场歧义码（深市股票与上证指数同代码），"
        f"本次已按深市股票解析；如需上证指数请显式传 sh{code} "
        f"或改用 get_index_kline_local"
    )


def _is_bare_000_stock(parsed) -> bool:
    """通用日线入口里被按深市股票解析的裸 000xxx 歧义码。"""
    return (
        not parsed.had_suffix
        and parsed.kind == STOCK_KIND
        and parsed.code.startswith("000")
    )


def _daily_request_warn(parsed, adjust: str) -> str | None:
    """get_daily 成功路径的非阻断提示（miss / L1-hit 两路径必须共用）。

    - 指数传 qfq/hfq：复权被强制为 none 的说明
    - 裸 000xxx 股票：跨市场歧义消歧提示
    两者按 kind 互斥。
    """
    if parsed.kind == INDEX_KIND and adjust in ("qfq", "hfq"):
        return f"指数 {parsed.code} 不支持 {adjust} 复权，已强制为 none"
    if _is_bare_000_stock(parsed):
        return _bare_000_ambiguity_warn(parsed.code)
    return None


# 分钟日期窗只收零填充 YYYY-MM-DD 或恰好 8 位 YYYYMMDD。不能直接用
# strptime：CPython 的 %m/%d 正则是 \d{1,2}，"2026-9-1"/"2026091" 会被
# 静默解析（前者非零补、后者只有 7 位）。
_DASH_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")


def _parse_strict_window_date(value: str) -> datetime.date | None:
    """严格形状 + 合法日历日双校验，通过返 date，否则 None。"""
    if _DASH_DATE_RE.match(value):
        iso = value
    elif _COMPACT_DATE_RE.match(value):
        iso = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    else:
        return None
    try:
        return datetime.date.fromisoformat(iso)
    except ValueError:
        return None


def _normalize_date_window(
    start_date: str, end_date: str,
) -> tuple[str, str, str | None]:
    """校验并归一离线分钟线日期窗。

    同时接受 YYYY-MM-DD（文档合同）与 YYYYMMDD（registry 对 *_date 入参
    的归一格式：HTTP 边界上 "2026-09-18" 会被改写成 "20260918"）；统一
    输出零填充 YYYY-MM-DD 供分钟索引字典序过滤。非零填充（"2026-9-1"）、
    非法日历日（"2026-13-01"）、7 位紧凑串均拒绝。

    Returns:
        (start_canonical, end_canonical, error)；error 非 None 时前两个为 ""。
    """
    canon: dict[str, str] = {}
    for label, value in (("start_date", start_date), ("end_date", end_date)):
        if not value:
            canon[label] = ""
            continue
        # 防御直调进程内方法传入 int（HTTP/Pydantic 边界只会给 str）：
        # re.match 对非 str 抛 TypeError，会逃出工具自己的 try/except
        if not isinstance(value, str):
            return "", "", (
                f"{label} 需为 YYYY-MM-DD 或 YYYYMMDD 字符串，收到 {value!r}"
            )
        parsed_date = _parse_strict_window_date(value)
        if parsed_date is None:
            return "", "", (
                f"{label} 需为 YYYY-MM-DD 或 YYYYMMDD 合法日历日，收到 {value!r}"
            )
        canon[label] = parsed_date.strftime("%Y-%m-%d")
    start, end = canon["start_date"], canon["end_date"]
    if start and end and start > end:
        return "", "", (
            f"start_date({start_date}) 不能晚于 end_date({end_date})"
        )
    return start, end, None


if TYPE_CHECKING:
    from ..models.mootdx2_config import MooTDX2Settings
    from .mootdx2_pool import ConnectionPool

logger = logging.getLogger(__name__)

# K线频率映射
FREQ_MAP = {
    "day": 9,
    "week": 5,
    "month": 6,
    "minute1": 8,
    "minute5": 0,
    "minute15": 1,
    "minute30": 2,
    "minute60": 3,
}

# 数据量限制常量
MAX_LIMIT_KLINE = 1000  # K线最大返回条数
# 离线分钟线默认/最大返回条数：1 分钟线 240 根/交易日，20000 ≈ 83 个
# 交易日，覆盖本机当前全量 .lc1（约 1.7 万根）且拦住多年文件的超大响应
DEFAULT_LIMIT_MINUTE = 1000
MAX_LIMIT_MINUTE = 20_000
MAX_LIMIT_BATCH_QUOTE = 50  # 批量行情最大股票数
# 2026-09-19 合同修复: batch_bars YAML 合同明示 ≤20 只 / 每只 ≤500 根,
# 违约返 invalid_param (旧实现静默截断 limit, >20 只也不拦)。
MAX_LIMIT_BATCH_BARS_CODES = 20
MAX_LIMIT_BATCH_BARS = 500
# workday 往前推算的保护性上限 (纯周末数学, 不做节假日历)
MAX_WORKDAY_COUNT = 365

# call_tool 分发表 (2026-09-19 LOW): 工具名 → 方法名。旧实现每次调用新建
# ~40 个 bound method 的 dict; 类方法都是稳定属性, getattr 即可。
_TOOL_METHOD_NAMES: dict[str, str] = {
    "get_quote": "get_quote",
    "get_kline": "get_kline",
    "get_batch_quote": "get_batch_quote",
    "get_minute_data": "get_minute_data",
    "get_trade": "get_trade",
    "get_trade_history": "get_trade_history",
    "get_index_kline": "get_index_kline",
    "get_code_list": "get_code_list",
    "get_stock_codes": "get_stock_codes",
    "get_etf_codes": "get_etf_codes",
    "get_etf_list": "get_etf_list",
    "get_market_count": "get_market_count",
    "get_workday": "get_workday",
    "get_workday_range": "get_workday_range",
    "get_index_all": "get_index_all",
    "get_f10": "get_f10",
    "get_f10_company": "get_f10_company",
    "get_minutes": "get_minutes",
    "get_index_bars": "get_index_bars",
    "get_xdxr": "get_xdxr",
    "get_stock_all": "get_stock_all",
    "get_k_data": "get_k_data",
    "search_stock": "search_stock",
    "get_stock_info": "get_stock_info",
    "get_custom_sector_list": "get_custom_sector_list",
    "get_custom_sector_stocks": "get_custom_sector_stocks",
    "get_index_overview": "get_index_overview",
    "get_index_kline_local": "get_index_kline_local",
    "get_index_minute_local": "get_index_minute_local",
    "get_index_fzline_local": "get_index_fzline_local",
    "stock_top_board": "stock_top_board",
    "stock_unusual": "stock_unusual",
    "indicator_atr": "indicator_atr",
    "get_daily": "get_daily",
    "get_minute": "get_minute",
    "get_fzline": "get_fzline",
    "batch_bars": "batch_bars",
    # YAML 工具名别名
    "get_daily_local": "get_daily",
    "get_daily_batch_local": "get_daily_batch_local",
    "get_fzline_local": "get_fzline",
    "get_minute_local": "get_minute",
    "mootdx2_stock_info": "get_stock_info",
    "search_stock_local": "search_stock",
}

# 数据量过大错误消息模板
DATA_TOO_LARGE_MSG = "数据量超过{limit}条，请缩小查询范围或增加筛选条件"

# ---- 2026-09-19 HIGH 审视: 实时行情报文归一化常量/helper --------------------
# tdxpy 0.2.7 parser/std/get_security_quotes.py 实测报文行只有
# market(int: 0=SZ/1=SH/2=BJ) / code / price / last_close / open / high /
# low / vol / amount ... **没有** close / pct_chg / symbol / name 列
# (旧代码按 close/pct_chg/symbol 取值, 生产全是 0 或 KeyError)。
_MARKET_ID_TO_PREFIX = {0: "sz", 1: "sh", 2: "bj"}
# A 股代码前缀 (按交易所划分, stocks() 列表里还混有基金/债券, 必须过滤)
_A_SHARE_PREFIXES_SH = ("60", "68")
_A_SHARE_PREFIXES_SZ = ("00", "30")
# ETF 代码前缀: 沪市 51/56/58, 深市 15 (16 开头是 LOF/分级, 不算 ETF)
_ETF_PREFIXES_BY_MARKET = {1: ("51", "56", "58"), 0: ("15",)}
# block.dat FLAT 行 code 为 7 位 "市场位 + 6 位代码": 0=SZ / 1=SH / 2=BJ
_BLOCK_MARKET_CHAR_TO_PREFIX = {"0": "sz", "1": "sh", "2": "bj"}


class _CustomSectorError(Exception):
    """自定义板块的内部信号异常: 本机环境/业务状态而非上游故障。

    tdxdir 缺失 (tdx_not_installed) / blocknew 目录缺失
    (tdx_custom_sector_unavailable) / 板块名未命中 (sector_not_found)。
    wrapper 显式捕获后返回 success=False 的类型化结果, 不进 error_counts、
    不降级上游 status。
    """

    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        super().__init__(message)


def _quote_symbol(rec: dict) -> str:
    """从报文 market/code 拼出带前缀 symbol, 如 'sh600519'。"""
    try:
        prefix = _MARKET_ID_TO_PREFIX.get(int(rec.get("market", 0)), "sz")
    except (TypeError, ValueError):
        prefix = "sz"
    return f"{prefix}{rec.get('code', '')}"


def _quote_pre_close(rec: dict) -> float:
    """昨收: mootdx2 ≤1.4 报文列名 last_close; 1.6+ 改名 pre_close。"""
    try:
        value = rec.get("last_close")
        if value is None:
            value = rec.get("pre_close")
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _quote_pct_chg(rec: dict) -> float:
    """报文给 price/昨收, 涨跌幅由网关计算 (%)。"""
    price = float(rec.get("price", 0) or 0)
    last_close = _quote_pre_close(rec)
    if last_close > 0:
        return round((price - last_close) / last_close * 100, 2)
    return 0.0


def _quote_amplitude(rec: dict) -> float:
    """振幅 = (最高 - 最低) / 昨收 * 100 (%)。"""
    try:
        high = float(rec.get("high", 0) or 0)
        low = float(rec.get("low", 0) or 0)
        last_close = _quote_pre_close(rec)
        if last_close > 0:
            return round((high - low) / last_close * 100, 2)
    except (TypeError, ValueError):
        pass
    return 0.0


def _annotate_quote_records(records: list[dict]) -> None:
    """就地补出网关合同需要的 symbol/close/pct_chg/amplitude 列。"""
    for rec in records:
        rec["symbol"] = _quote_symbol(rec)
        rec["close"] = float(rec.get("price", 0) or 0)
        rec["pct_chg"] = _quote_pct_chg(rec)
        rec["amplitude"] = _quote_amplitude(rec)


def _drop_internal_quote_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """剔除 tdxpy 行情报文里的内部列。

    tdxpy 新版 get_security_quotes 解析结果带 ``_raw`` 列 (该条报价的
    原始协议字节, bytes), mootdx2 to_data 原样透传进 DataFrame。bytes
    进 JSON 序列化会报 "invalid utf-8 sequence", 导致 get_quote /
    get_batch_quote 整工具失败。所有 ``_`` 前缀列都不属于对外合同, 统一
    丢弃; 无内部列时返回原 DataFrame (不做无谓拷贝)。
    """
    internal = [c for c in df.columns if str(c).startswith("_")]
    return df.drop(columns=internal) if internal else df


def _strip_market_prefix(code: str) -> str:
    """剥 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀, 返回裸码。

    取代散落在 10+ 处的 ``code.lower().replace("sh","").replace("sz","")``
    链式替换 (旧写法有顺序坑: 裸码含 "sh" 子串也会被误删)。
    """
    s = str(code or "").strip().lower()
    if len(s) >= 9 and s[-3:] in (".sh", ".sz", ".bj"):
        s = s[:-3]
    if s[:2] in ("sh", "sz", "bj"):
        s = s[2:]
    return s


def _classify_bare_market(code: str) -> str:
    """按 tdxpy get_stock_market 的裸码推断规则返回 sh/sz/bj。

    tdxpy: 4/8 开头 → bj (北交所), 5/6/7/9 及 50/51/60/68 等 → sh,
    0/3 及 12/13/15/16/18/20/39 等 → sz, 默认 sh。北交所 2024 后新号段
    920xxx 同样走 bj (旧规则漏判)。
    """
    code = str(code or "")
    if code.startswith("920") or code[:1] in ("4", "8"):
        return "bj"
    if code[:3] in ("110", "113", "132", "204"):
        return "sh"
    if code[:2] in ("50", "51", "56", "58", "60", "68", "90"):
        return "sh"
    if code[:1] in ("5", "6", "7", "9"):
        return "sh"
    return "sz"


def _parse_quote_symbol(code: str) -> tuple[str, str, str]:
    """行情接口代码归一化 → (market_prefix, bare6, library_token)。

    实测的 tdxpy 路由约束:
    - sh/sz 前缀被 get_stock_markets 显式识别, 必须**前缀直传** — 裸码
      "000001" 会被数字推断成深市股票平安银行, 无法表达上证指数;
    - "bj" 前缀**不被识别** (bj430047 会错路由到 SH), 北交所只能发裸码,
      且批次中混入 market=2 时 tdxpy 整批返 None → BJ 只能单只独立批次。
    """
    s = str(code or "").strip().lower()
    prefix: str | None = None
    if len(s) >= 9 and s[-3:] in (".sh", ".sz", ".bj"):
        prefix, s = s[-2:], s[:-3]
    elif s[:2] in ("sh", "sz", "bj"):
        prefix, s = s[:2], s[2:]
    if not (len(s) == 6 and s.isdigit()):
        raise ValueError(f"代码格式错误: {code!r} (需 6 位数字, 可带 sh/sz/bj 前缀)")
    if prefix is None:
        prefix = _classify_bare_market(s)
    token = s if prefix == "bj" else f"{prefix}{s}"
    return prefix, s, token


def _split_prefixed_symbol(sym: str) -> tuple[str, str]:
    """'sh600519' → ('sh','600519'); 无前缀回落 ('sz', sym)。"""
    s = str(sym or "")
    if s[:2] in ("sh", "sz", "bj"):
        return s[:2], s[2:]
    return "sz", s


def _classify_search_code(code: str) -> str:
    """search_stock 两条数据源共用的市场分类: block FLAT 7 位码或 6 位裸码。"""
    code = str(code or "")
    if len(code) == 7 and code[0] in _BLOCK_MARKET_CHAR_TO_PREFIX:
        return _BLOCK_MARKET_CHAR_TO_PREFIX[code[0]]
    return _classify_bare_market(code)


def _parse_compact_date(value: str) -> datetime.datetime:
    """YYYYMMDD / YYYY-MM-DD → datetime; 均不匹配抛 ValueError。"""
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"日期格式错误: {value!r} (需 YYYYMMDD 或 YYYY-MM-DD)")


def _validate_kline_record(record: dict) -> bool:
    """校验单条K线数据合理性"""
    try:
        close = float(record.get("close", 0))
        open_ = float(record.get("open", 0))
        high = float(record.get("high", 0))
        low = float(record.get("low", 0))
        vol = float(record.get("vol", 0))
        # 收盘价必须 > 0
        if close <= 0:
            return False
        # 最高价 >= 最低价
        if high < low:
            return False
        # 开盘/收盘应该在最高/最低范围内
        if open_ > high or open_ < low:
            return False
        if close > high or close < low:
            return False
        # 成交量 >= 0
        if vol < 0:
            return False
        return True
    except (TypeError, ValueError):
        return False


@dataclass
class MooTDX2Config:
    """MooTDX2 配置"""
    name: str
    market: str = "std"
    # 新增配置字段
    settings: "MooTDX2Settings | None" = None  # 完整配置
    pool: "ConnectionPool | None" = None  # 连接池


class _SearchDataUnavailable(Exception):
    """search_stock 双源 (tdxdir + board.db) 都不可用, async 入口包装为 no_data。

    2026-09-28 Round-3 H1-silent: 旧码两源都缺时静默 success=True data=[],
    运维以为搜索功能坏了。抛该异常让 async 入口显式走 ``_no_data_result`` 路径,
    业务侧可基于 ``error_detail.error_type=no_data`` 分流 (与单源无结果区分)。
    """


class MooTDX2Client:
    """MooTDX2 MCP 客户端"""

    def __init__(self, config: MooTDX2Config):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._quotes = None
        self._reader = None  # Reader 实例缓存
        self._reader_lock = threading.Lock()  # Reader 缓存锁（高并发保护）
        self._quotes_lock = threading.Lock()  # Quotes 缓存锁（高并发保护）

        # M2 (round 11): 生命周期代次标记。start() 超时被网关取消后在途
        # factory 线程仍可能完成; 用于防止已停 client 被误调用复活,
        # 并让 late recovery 入口拒绝已停实例。
        self._stopped = False

        # 指标收集
        self._request_id = 0
        # L3 (2026-09-15 MCP 二次审视): error_counts 上限防止内存无限增长
        self._ERROR_COUNTS_MAX = 100
        self._metrics = {
            "total_requests": 0,
            "total_errors": 0,
            "error_counts": {},
        }
        # 2026-09-14 round 10 audit: total_errors / error_counts 多 key 复合
        # 写入非原子, 多个 async task 并发会丢更新。加 lock 保护。
        # total_requests 单 int += 是 GIL 原子的, 不需要。
        self._metrics_lock = threading.Lock()

        # 2026-09-15 get_daily_local 内存缓存 (思路 3):
        # 2026-09-16: 缓存策略改为当日有效（到次日 9:00 过期）
        # 方案：用日期作为缓存 key 的一部分，同一天内命中，次日自动 miss
        # 这是为了避免缓存隔日陈旧行情数据（如停牌/退市/复权价）
        # 2026-09-28 Round-3 Batch 2: 抽 _L1Slot 抽象, 统一 8 个单 symbol 工具缓存
        # 并改造 daily L1 为 2 层 (key 不含 start/end, 命中后切窗)
        # 次日 9:00 过期 = 计算到次日 9:00 的秒数
        now = datetime.datetime.now()
        tomorrow_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now.hour >= 9:
            tomorrow_9am += datetime.timedelta(days=1)
        ttl_seconds = (tomorrow_9am - now).total_seconds()
        self._DAILY_L1_TTL = ttl_seconds
        self._l1_daily = _L1Slot("daily", maxsize=2048, ttl=ttl_seconds)
        self._DAILY_L1_ENABLED = self._l1_daily.enabled
        if self._DAILY_L1_ENABLED:
            logger.info(
                f"get_daily L1 cache initialized: ttl={ttl_seconds}s "
                f"(expires at {tomorrow_9am.strftime('%Y-%m-%d %H:%M')})"
            )

        # 2026-09-16: Reader 并发控制 - 限制同时执行的 Reader 数量
        # 防止过多并发 Reader 导致 TDX 库性能下降或超时
        # 容量优先级: settings.reader_limit (config_db / yaml 直传) >
        #             env UNIHIVE_MOOTDX2_READER_LIMIT > 默认 6
        # getattr 兜底: 历史 mock 用 SimpleNamespace 装配 settings, 没有
        # reader_limit 字段; 真生产 config 走 MooTDX2Settings 有 reader_limit。
        _reader_limit = 6
        cfg_reader_limit = getattr(config.settings, "reader_limit", None) if config.settings is not None else None
        if cfg_reader_limit is not None and cfg_reader_limit > 0:
            _reader_limit = int(cfg_reader_limit)
        else:
            _env = os.environ.get("UNIHIVE_MOOTDX2_READER_LIMIT")
            if _env:
                try:
                    _reader_limit = max(1, int(_env))
                except (TypeError, ValueError):
                    pass  # 非法 env 值回落默认
        self._READER_SEMAPHORE_LIMIT = _reader_limit
        self._reader_semaphore = asyncio.Semaphore(self._READER_SEMAPHORE_LIMIT)
        # 2026-09-28 性能审视 (C): 批量子闸门 — 限制批量入口内部并发,
        # 给单 symbol get_daily 留闸门余量。批量 20 只全 miss 不再把闸门
        # 槽全占住把单 symbol 挤到队尾。子闸门 = reader_limit // 2 (至少 1;
        # reader_limit=6 时子闸门=3, 给单 symbol 留 3 个槽),
        # 等待不占线程 (asyncio 闸门), 故廉价。
        self._batch_reader_semaphore = asyncio.Semaphore(
            max(1, self._READER_SEMAPHORE_LIMIT // 2)
        )
        # 闸门累计/实时计数 (突发短于轮询间隔, 瞬时值抓不到, 靠累计/峰值留存)
        # waiters_now 由 _reader_gate 自持, 不读 CPython Semaphore 私有字段
        self._gate_counters = {
            "total": 0, "current": 0, "peak": 0,
            "waiters_now": 0, "waiters_total": 0,
        }

        # 2026-09-15 P0 (MOOTDX2 性能审视 H2-a):
        # stock_top_board / stock_unusual 全市场扫描 N 个 50-batch roundtrip
        # (~5-20s), 加 10s TTL 结果缓存, 命中后 0ms 返回。
        # 行情 10s 内变化 < 0.5%, 用户感知不到差异。
        # 2026-09-28 Round-3 Batch 2 H4-silent: 改用 _L1Slot, cache 只存 records,
        # ToolResult 每次重包, 杜绝引用共享导致的下游修改污染。
        self._TOP_BOARD_L1_TTL = 10.0
        self._l1_top_board = _L1Slot("top_board", maxsize=128, ttl=self._TOP_BOARD_L1_TTL)
        self._UNUSUAL_L1_TTL = 10.0
        self._l1_unusual = _L1Slot("unusual", maxsize=128, ttl=self._UNUSUAL_L1_TTL)

        # 2026-09-28 Round-3 Batch 2 H1-perf: 单 symbol 离线工具 L1
        # 排除 get_daily_batch_local 等批量入口 (见 feedback_no_l1_on_batch_interfaces)
        # 2026-09-28 性能审视: 分钟线暂不做 L1 缓存 (minute/fzline/index_minute/
        # index_fzline 4 槽移除), 仅保留日线类 (daily / index_kline) 与非 K 线
        # 工具 (search/indicator_atr/block_scan) 缓存。分钟线高频但用户暂无需求。
        self._l1_index_kline = _L1Slot("index_kline", maxsize=256, ttl=ttl_seconds)
        self._l1_search = _L1Slot("search", maxsize=128, ttl=600)
        self._l1_indicator_atr = _L1Slot("indicator_atr", maxsize=64, ttl=600)
        self._l1_block_scan = _L1Slot("block_scan", maxsize=8, ttl=600)

        # 线程池入口累计计数 (突发短于轮询间隔, 瞬时值抓不到, 靠累计/峰值留存)
        self._pool_counters = {"total": 0, "current": 0, "peak": 0}

    async def _run_blocking(self, func, *args, **kwargs):
        """把同步阻塞调用丢到 mootdx2 专用线程池 (非默认 executor)。

        替代原本重复 30+ 处的 ``loop = asyncio.get_event_loop()`` + ``loop.run_in_executor``
        模板。``asyncio.get_event_loop`` 在 Python 3.10+ deprecated, 3.12+ 在没
        running loop 的线程里会 emit DeprecationWarning; ``get_running_loop`` 是
        推荐写法, 仅在 event loop 上下文里可用 (本类的所有 tool 都是 gateway
        server 调进来的, 必然在 loop 里)。

        走 mootdx2 专用 ``get_mootdx2_executor()`` 线程池 (16 worker), 而不是
        ``asyncio.to_thread`` 的默认 executor — 后者会让金融阻塞调用与其它默认
        池任务互相挤占, 且会与 tdx_quant 的阻塞调用混进同一快照, 监控行不诚实
        (见 core/blocking_pool.py 注释)。
        """
        loop = asyncio.get_running_loop()
        # getattr: __new__ 旁路 __init__ 的测试双实例可能没有计数字段
        counters = getattr(self, "_pool_counters", None)
        if counters is not None:
            counters["total"] += 1
            counters["current"] += 1
            counters["peak"] = max(counters["peak"], counters["current"])
        try:
            return await loop.run_in_executor(
                get_mootdx2_executor(), functools.partial(func, *args, **kwargs)
            )
        finally:
            if counters is not None:
                counters["current"] -= 1

    @contextlib.asynccontextmanager
    async def _reader_gate(self):
        """Reader 信号量 + 累计/峰值/实时等待者计数。

        闸门满时记一次等待 (waiters_total+1) 并把 waiters_now 增 1,
        进闸后 waiters_now-1、current+1、total+1、peak 滚动;
        离开闸门时 current-1。所有计数 self 持, 不读 CPython Semaphore 私有字段
        (跨 Python 版本脆弱)。
        """
        if self._reader_semaphore.locked():
            self._gate_counters["waiters_total"] += 1
            self._gate_counters["waiters_now"] += 1
        async with self._reader_semaphore:
            self._gate_counters["waiters_now"] -= 1
            self._gate_counters["total"] += 1
            self._gate_counters["current"] += 1
            self._gate_counters["peak"] = max(
                self._gate_counters["peak"], self._gate_counters["current"]
            )
            try:
                yield
            finally:
                self._gate_counters["current"] -= 1

    def _new_request_id(self) -> str:
        """生成请求 ID"""
        self._request_id += 1
        return f"{self.name}-{int(time.time())}-{self._request_id}"

    def _record_error(self, error_type: str) -> None:
        """原子记录一次错误 (round 10 audit: error_counts[k] += 1 非原子).

        total_errors 简单 int += 是 GIL 安全的, 但 error_counts[key] 复合
        读写需要 lock 保护, 否则并发下会丢更新。

        L3 (2026-09-15 MCP 二次审视): 达到上限时 LRU 清理最老的条目。
        """
        with self._metrics_lock:
            self._metrics["total_errors"] += 1
            error_counts = self._metrics["error_counts"]
            # LRU 清理: 超过上限时删除最老的条目
            if len(error_counts) >= self._ERROR_COUNTS_MAX and error_type not in error_counts:
                oldest_key = next(iter(error_counts))
                del error_counts[oldest_key]
            error_counts[error_type] = error_counts.get(error_type, 0) + 1

    def _get_tdxdir(self) -> str | None:
        """获取 tdxdir，如果没有配置则返回 None 让 mootdx2 自动探测"""
        if self.config.settings:
            tdxdir = self.config.settings.tdxdir
            if tdxdir:
                return tdxdir
        return None

    def _require_blocknew_dir(self) -> Path:
        """自定义板块工具的统一前置闸: 返回 T0002/blocknew 目录。

        - tdxdir 未配置或目录不存在 → tdx_not_installed (旧码 Path(None)
          抛 TypeError, 对外误报 internal_error);
        - T0002/blocknew 不存在 (V7.73+ 未同步自定义板块) →
          tdx_custom_sector_unavailable。
        """
        tdxdir = self._get_tdxdir()
        if not tdxdir:
            raise _CustomSectorError(
                MooTDXErrorType.TDX_NOT_INSTALLED.value,
                "未配置通达信安装目录 (settings.tdxdir), 无法访问自定义板块",
            )
        root = Path(tdxdir)
        if not root.exists():
            raise _CustomSectorError(
                MooTDXErrorType.TDX_NOT_INSTALLED.value,
                f"通达信安装目录不存在: {tdxdir}",
            )
        blocknew_dir = root / "T0002" / "blocknew"
        if not blocknew_dir.exists():
            raise _CustomSectorError(
                MooTDXErrorType.TDX_CUSTOM_SECTOR_UNAVAILABLE.value,
                f"自定义板块目录不存在: {blocknew_dir} (请在通达信客户端维护自定义板块)",
            )
        return blocknew_dir

    @staticmethod
    def _load_custom_sector_name_map(tdxdir: str) -> dict[str, str]:
        """Customize.search(group=True) DataFrame → {block_type(stem): 中文名}。

        mootdx2 1.4.2 实测返回 DataFrame, 列 blockname(中文名)/
        block_type(.blk 文件名 stem)/stock_count/code_list。cfg 不兼容时
        返回空映射, 调用方退化为 .blk 文件名展示。
        """
        try:
            from mootdx2.tools.customize import Customize

            grouped = Customize(tdxdir=tdxdir).search(group=True)
        except Exception as e:  # V7.73+ cfg 格式可能不兼容
            logger.info(f"Customize.search failed, fallback to .blk stems: {e}")
            return {}
        name_by_stem: dict[str, str] = {}
        if grouped is not None and hasattr(grouped, "iterrows") and len(grouped) > 0:
            for _, row in grouped.iterrows():
                stem = str(row.get("block_type", "")).strip()
                name = str(row.get("blockname", "")).strip()
                if stem and name:
                    name_by_stem[stem] = name
        return name_by_stem

    @staticmethod
    def _records_from_indexed_df(df, out_key: str = "date") -> list:
        """DataFrame（DatetimeIndex）→ records，索引列抽成字符串字段。

        2026-09-28 fix: mootdx2 get_df 先 ``df["date"]=pd.to_datetime(...)``
        再 ``set_index("date")``（tdx/offline/daily_bar.py:170-171），索引名是
        ``"date"`` — reset_index 后索引列叫 "date" 而非 "index"。旧码只认
        ``"index"`` 列名，pop 不执行，Timestamp 原样留在 records 里；不传
        日期时被序列化 default=str 兜住，一传 start/end 就在
        ``_filter_records_by_date`` 里拿 Timestamp 和 str 比较抛 TypeError
        （get_daily_local 带日期必炸的根因）。

        这里不依赖索引列名：先 pop "index"，再回退 pop "date"（daily 的
        索引名恰为 "date"，pop 后按 out_key 重写不冲突）。minute 类 df 的
        索引名同为 "date"，out_key="datetime" 时写回完整 "YYYY-MM-DD HH:MM:SS"。

        Args:
            df: 含 DatetimeIndex 的 DataFrame（reset_index 前）
            out_key: 索引字符串写回的字段名（"date" 截 YYYY-MM-DD；
                其它值写完整 str(idx)，供 minute/fzline 用 "datetime"）
        """
        records = df.reset_index().to_dict(orient="records")
        for r in records:
            idx = r.pop("index", None)
            if idx is None:
                idx = r.pop("date", None)
            if idx is None:
                continue
            if out_key == "date":
                r["date"] = str(idx)[:10]
            else:
                r[out_key] = str(idx)
        return records

    @staticmethod
    def _filter_records_by_date(records: list, start_date: str, end_date: str) -> list:
        """缓存命中后在 records 上做 start/end 过滤。O(N) dict 遍历。
        date 字段为 'yyyy-MM-dd' 字符串; 字符串比较即可 (ISO 8601)。"""
        if not (start_date or end_date):
            return list(records)
        out = []
        for r in records:
            d = r.get("date", "")
            if not d:
                continue
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            out.append(r)
        return out

    def _get_reader(self):
        """获取缓存的 Reader 实例（懒初始化，线程安全）。

        Round-12 簇H: stop() 之后的晚到调用不得重新 factory 出无人关闭的
        Reader; 锁内二次复检挡住 "过外层闸后 stop 抢先" 的竞态。start()
        复位 _stopped 后恢复可用。
        """
        if self._stopped:
            raise RuntimeError(
                f"{self.name}: client has been stopped; call start() first"
            )
        if self._reader is None:
            with self._reader_lock:
                # 双重检查
                if self._stopped:
                    raise RuntimeError(
                        f"{self.name}: client has been stopped; call start() first"
                    )
                if self._reader is None:
                    from mootdx2.reader import Reader
                    from .tdx_reader_patch import patch_reader_bj_paths
                    tdxdir = self._get_tdxdir()
                    self._reader = Reader.factory(market="std", tdxdir=tdxdir)
                    # mootdx2 1.5.x: 上游 find_path 不支持 bj 前缀/补前缀,
                    # 北交所股票与 899 北证指数全部读不到，实例级打补丁
                    patch_reader_bj_paths(self._reader)
        return self._reader

    # 2026-09-14 round 11 audit (坑 #6 防御):
    # mootdx2 `_default_tdxdir()` 按平台惯例返回:
    #   - Windows: C:/new_tdx
    #   - macOS:   ~/new_tdx
    #   - Linux:   ~/.local/share/new_tdx
    # 跨机器部署时若用户没显式配 tdxdir, 会静默指向空目录 → Reader
    # 返回 DataFrame(data=None) → 上游 no_data, 调用方无法区分
    # "代码错" vs "本地未下载"。这里在 start() 探测一次, 写 INFO/WARN
    # 日志告知用户当前 tdxdir 是否能命中白名单指数 .day 文件。
    # best-effort: 不抛、不改 status、不影响在线接口。
    _PROBE_INDEX_SAMPLES: tuple[tuple[str, str], ...] = (
        ("000001", "sh"),   # 上证指数（必检 SH 命中）
        ("399001", "sz"),   # 深证成指（必检 SZ 命中）
        ("000300", "sh"),   # 沪深300（SH 宽基，曾因前缀推断被错放 sz）
    )

    def _probe_tdxdir(self) -> None:
        """启动期 tdxdir 健康探测（best-effort）。

        探测失败原因（仅写日志，不抛）:
          (1) tdxdir 为空
          (2) 目录不存在
          (3) vipdoc 子目录缺失
          (4) 指数白名单文件一个都没有命中（本地数据未下载）
        """
        tdxdir = self._get_tdxdir()
        if not tdxdir:
            logger.warning(
                f"{self.name}: tdxdir 未配置；离线 reader 工具 "
                f"将返回 tdx_not_installed（在线模式可忽略）"
            )
            return

        base = Path(tdxdir)
        if not base.exists() or not base.is_dir():
            logger.warning(
                f"{self.name}: tdxdir={base} 不存在或不是目录"
            )
            return

        vipdoc = base / "vipdoc"
        if not vipdoc.is_dir():
            logger.warning(
                f"{self.name}: tdxdir={base} 下未发现 vipdoc/ 子目录；"
                f"离线 reader 工具不可用"
            )
            return

        # 探一下白名单指数 .day 文件是否存在（任一命中即视为 OK）
        hits: list[str] = []
        for code, mkt in self._PROBE_INDEX_SAMPLES:
            p = vipdoc / mkt / "lday" / f"{mkt}{code}.day"
            if p.exists():
                hits.append(p.name)
        if hits:
            logger.info(
                f"{self.name}: tdxdir 探测 OK；本地 .day 命中 "
                f"{len(hits)}/{len(self._PROBE_INDEX_SAMPLES)} 个指数样本"
                f"（{hits[:3]}）"
            )
        else:
            logger.warning(
                f"{self.name}: tdxdir/vipdoc 存在但未发现任何白名单指数 "
                f".day 文件；离线指数工具将返回 no_data（请通过 TDX 客户端 "
                f"下载完整本地数据）"
            )

    def _error_result(self, exc: Exception, context: str) -> ToolResult:
        """将异常转换为错误结果"""
        err_info = error_to_result(exc, context)
        error_detail = err_info.get("error", {})
        error_type = error_detail.get("error_type", "internal_error")
        self._record_error(error_type)
        return ToolResult(
            success=False,
            error=error_detail.get("message", str(exc)[:100]),
            error_detail=error_detail,
        )

    def _no_data_result(self, message: str = "查询成功但无数据") -> ToolResult:
        """返回 no_data 状态（不是错误，是正常业务状态）"""
        return ToolResult(
            success=True,
            data=None,
            error=message,
            error_detail={
                "error_type": MooTDXErrorType.NO_DATA.value,
                "message": message,
                "recoverable": True,
            },
        )

    def _invalid_param_result(self, message: str) -> ToolResult:
        """参数门控失败 (不可重试, 不打 TDX, 不记 error_counts)。"""
        return ToolResult(
            success=False,
            error=message,
            error_detail={
                "error_type": MooTDXErrorType.INVALID_PARAM.value,
                "message": message,
                "recoverable": False,
            },
        )

    def _invalid_symbol_result(self, message: str) -> ToolResult:
        """代码格式门控失败。"""
        return ToolResult(
            success=False,
            error=message,
            error_detail={
                "error_type": MooTDXErrorType.INVALID_SYMBOL.value,
                "message": message,
                "recoverable": False,
            },
        )

    def _custom_sector_error_result(self, error_type: str, message: str) -> ToolResult:
        """自定义板块的环境/业务状态 (tdxdir 缺失 / 目录缺失 / 板块未找到)。

        与 no_data 不同, YAML 合同把这三类列为错误 (success=False), 但它们是
        本机环境状态而非上游故障, 不进 error_counts / 不降级 status。
        """
        return ToolResult(
            success=False,
            error=message,
            error_detail={
                "error_type": error_type,
                "message": message,
                "recoverable": True,
            },
        )

    def _build_daily_tool_result(
        self,
        *,
        code: str,
        data: list[dict] | None,
        meta: dict,
        parsed_for_key,
    ) -> ToolResult:
        """Build ToolResult from `_get_daily_sync` output.

        - `data` truthy -> success ToolResult; attaches `meta["warn"]` if present
        - `data` falsy -> no_data ToolResult with kind-aware message
        - 指数代码 (kind=index) -> "指数 X 本地无 .day 文件 (可能未下载完整本地数据)"
        - 其他 -> "未找到 X 的日线数据"
        """
        if data:
            result = ToolResult(success=True, data=data, source="mootdx2")
            if meta.get("warn"):
                result.error_detail = {"warn": meta["warn"], "recoverable": True}
            return result
        # 无数据
        if meta.get("kind") == INDEX_KIND:
            if parsed_for_key is not None:
                msg = (
                    f"指数 {parsed_for_key.code} 本地无 .day 文件"
                    f"（可能未下载完整本地数据）"
                )
            else:
                try:
                    parsed = parse_symbol(code)
                    msg = (
                        f"指数 {parsed.code} 本地无 .day 文件"
                        f"（可能未下载完整本地数据）"
                    )
                except ValueError:
                    msg = "指数本地无 .day 文件（可能未下载完整本地数据）"
        else:
            msg = f"未找到 {code} 的日线数据"
            # 裸 000xxx 在 sz 下无文件时，提示指数入口，避免用户误判
            # 为"本地数据没下载"
            ref = parsed_for_key
            if ref is None:
                try:
                    ref = parse_symbol(code)
                except ValueError:
                    ref = None
            if ref is not None and _is_bare_000_stock(ref):
                msg += (
                    f"；若要读上证指数请显式传 sh{ref.code} "
                    f"或使用 get_index_kline_local"
                )
        return self._no_data_result(msg)

    def _get_ttl_until_tomorrow_9am(self) -> float:
        """计算到次日 9:00 的剩余秒数（动态 TTL）"""
        now = datetime.datetime.now()
        tomorrow_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now.hour >= 9:
            tomorrow_9am += datetime.timedelta(days=1)
        return (tomorrow_9am - now).total_seconds()

    def _current_server(self) -> dict | None:
        """当前实际连接的行情服务器 (ServerManager.current 优先, 退回 StdQuotes)。"""
        quotes = self._quotes
        if quotes is None:
            return None
        current = None
        manager = getattr(quotes, "manager", None)
        if manager is not None and getattr(manager, "current", None):
            current = manager.current
        elif getattr(quotes, "server", None):
            current = quotes.server
        if not current:
            return None
        return {"host": current[0], "port": int(current[1])}

    def get_metrics(self) -> dict:
        """获取客户端指标 — 含 L1 缓存运行时统计 (2026-09-15).

        2026-09-28 Round-3 Batch 2: 重构为 ``_L1Slot`` 协议; ``top_board_l1``
        / ``unusual_l1`` / 8 个新单 symbol 工具 L1 全部走 ``slot.metrics_block()``
        统一口径。
        """
        # 2026-09-15 P0 (H2-a): stock_top_board / stock_unusual L1 metrics
        # 阻塞线程池 + Reader 闸门: 实时快照 + 累计/峰值计数
        # (历史K线突发 <1s, 10s 轮询抓不到瞬时值, 靠累计/峰值留存事实)。
        from ..core.blocking_pool import mootdx2_executor_snapshot
        instant_pool = mootdx2_executor_snapshot()
        pc = self._pool_counters
        pool_snapshot = {
            **instant_pool,
            "total": pc["total"],
            "peak": pc["peak"],
        }
        semaphore = getattr(self, "_reader_semaphore", None)
        counters = getattr(self, "_gate_counters", None)
        if semaphore is not None and counters is not None:
            reader_gate = {
                "capacity": self._READER_SEMAPHORE_LIMIT,
                # 闸门实时占用 = 当前持有 permit 数 (由 _reader_gate 自持计数,
                # 不读 CPython Semaphore 私有字段, 跨版本脆弱)。
                "acquired": counters["current"],
                "waiters": counters["waiters_now"],
                "total": counters["total"],
                "peak": counters["peak"],
                "waiters_total": counters["waiters_total"],
            }
        else:
            reader_gate = None
        return {
            **self._metrics,
            "current_server": self._current_server(),
            "thread_pool": pool_snapshot,
            "reader_gate": reader_gate,
            "daily_l1": self._l1_metrics_block(
                self._l1_daily, ttl_override=self._get_ttl_until_tomorrow_9am(),
                reader_semaphore_limit=self._READER_SEMAPHORE_LIMIT,
            ),
            "top_board_l1": self._l1_metrics_block(
                self._l1_top_board, ttl_override=self._TOP_BOARD_L1_TTL,
            ),
            "unusual_l1": self._l1_metrics_block(
                self._l1_unusual, ttl_override=self._UNUSUAL_L1_TTL,
            ),
            # Round-3 Batch 2 H1-perf: 单 symbol 工具 L1 metrics
            # 2026-09-28 性能审视: 4 个分钟线槽已移除, 仅日线类 + 非 K 线工具
            "index_kline_l1": self._l1_metrics_block(self._l1_index_kline),
            "search_l1": self._l1_metrics_block(self._l1_search),
            "indicator_atr_l1": self._l1_metrics_block(self._l1_indicator_atr),
            "block_scan_l1": self._l1_metrics_block(self._l1_block_scan),
            "pool_stats": self.config.pool.get_stats() if self.config.pool else {},
        }

    def _l1_metrics_block(
        self, slot: _L1Slot, ttl_override: float | None = None,
        reader_semaphore_limit: int | None = None,
    ) -> dict:
        """生成 L1 metrics 子块 — 全部 L1Slot 共享同一结构 (Round-3 Batch 2).

        Args:
            slot: ``_L1Slot`` 实例
            ttl_override: 强制覆盖 slot.ttl (例如 daily L1 用动态计算到次日 9:00)
            reader_semaphore_limit: 仅 daily L1 暴露闸门容量
        """
        if not slot.enabled:
            return {"enabled": False, "slot": slot.name}
        total = slot.hits + slot.misses
        ttl_value = ttl_override if ttl_override is not None else (
            slot._cache.ttl if slot._cache is not None else None
        )
        maxsize = slot._cache.maxsize if slot._cache is not None else None
        block: dict = {
            "enabled": True,
            "slot": slot.name,
            "hits": slot.hits,
            "misses": slot.misses,
            "hit_rate": (slot.hits / total) if total > 0 else None,
            "size": len(slot),
            "maxsize": maxsize,
            "ttl_seconds": ttl_value,
        }
        if reader_semaphore_limit is not None:
            block["reader_semaphore_limit"] = reader_semaphore_limit
        return block

    def reset_l1_caches(self) -> dict:
        """手动清空全部 L1 内存缓存 (控制台 /l1-cache 管理端点调用)。

        命中计数保留 (累计指标), 只清条目。
        """
        if not self._DAILY_L1_ENABLED:
            return {"enabled": False, "cleared": []}
        cleared = []
        for slot in (
            self._l1_daily,
            self._l1_top_board,
            self._l1_unusual,
            # 2026-09-28 Round-3 Batch 2 H1-perf: 单 symbol 工具 L1
            # (4 个分钟线槽已移除)
            self._l1_index_kline,
            self._l1_search,
            self._l1_indicator_atr,
            self._l1_block_scan,
        ):
            if slot.enabled:
                slot.clear()
                cleared.append(slot.name)
        logger.info(f"All L1 caches manually reset via console: {cleared}")
        return {"enabled": True, "cleared": cleared}

    async def get_health(self) -> ToolResult:
        """健康检查接口（轻量 ping）"""
        return ToolResult(
            success=True,
            data={"status": "ok", "source": "mootdx2"},
            source="mootdx2",
        )

    async def get_server_status(self) -> ToolResult:
        """获取服务状态（供 MCP 网关管理台直接读取）"""
        pool_stats = self.config.pool.get_stats() if self.config.pool else {}

        current_server = self._current_server()

        configured_servers = (
            [{"host": s.host, "port": s.port} for s in self.config.settings.servers]
            if self.config.settings else []
        )

        return ToolResult(
            success=True,
            data={
                "version": self.config.settings.version if self.config.settings else "1.0.0",
                "status": self._status.value,
                "current_server": current_server,
                "servers": configured_servers,
                "pool_stats": pool_stats,
                "error_counts": self._metrics.get("error_counts", {}),
                "total_requests": self._metrics.get("total_requests", 0),
            },
            source="mootdx2",
        )

    def _pin_quotes_servers(self, quotes, servers) -> None:
        """把配置的多台行情服务器注入 mootdx2 ServerManager 候选池。

        mootdx2 StdQuotes 的 server= 参数只接受单台 (内部 candidates 固定为
        [server]), 多台顺序故障转移只能通过其 ServerManager 候选列表实现。
        factory 已用首台初始化 manager, 这里把其余服务器追加进同一个
        manager (StdQuotes 与 AutoSwitchClient 共享该对象引用), 运行期请求
        失败时 mootdx2 会自动切换候选并重连。

        auto_switch=1 (factory 处传入) 让阈值为 1: 一次失败即切下一台,
        否则默认阈值 3 会让第二台在单次请求的重试预算内根本轮不到。
        """
        manager = getattr(quotes, "manager", None)
        if manager is None:
            logger.warning(f"{self.name}: Quotes 实例无 manager, 额外服务器候选未注入")
            return
        candidates = [(s.host, int(s.port)) for s in servers]
        with manager.lock:
            for cand in candidates[1:]:
                if cand not in manager._candidates:
                    manager._candidates.append(cand)
        logger.info(
            f"{self.name}: pinned TDX servers (failover order): "
            f"{[f'{h}:{p}' for h, p in candidates]}"
        )

    def _get_quotes(self):
        """获取或创建 Quotes 实例（线程安全）。

        settings.servers 非空时固定连配置的 IP (bestip=False + server=首台,
        其余经 _pin_quotes_servers 注入 failover 候选); 为空时保持原行为
        bestip=True 全网测速自动选优。

        Round-12 簇H: stop() 已置 _stopped 并在 _quotes_lock 内摘除关闭
        Quotes; 晚到调用 (在途 worker / stop 后的误调) 必须直接失败, 不得
        重新 factory 出无人关闭的活 TDX 连接。锁内二次复检挡住 "过外层闸后
        stop 抢先摘除" 的竞态。start() 复位 _stopped 后恢复可用。
        """
        if self._stopped:
            raise RuntimeError(
                f"{self.name}: client has been stopped; call start() first"
            )
        if self._quotes is None:
            with self._quotes_lock:
                # 双重检查
                if self._stopped:
                    raise RuntimeError(
                        f"{self.name}: client has been stopped; call start() first"
                    )
                if self._quotes is None:
                    from mootdx2.quotes import Quotes
                    settings = self.config.settings
                    pinned = settings.servers if settings else []
                    # 从配置获取超时设置
                    timeout = 30  # 默认 30 秒
                    if settings:
                        timeout = max(
                            settings.connect_timeout_seconds,
                            settings.read_timeout_seconds,
                        ) * 3  # 给足够的重试时间
                    if pinned:
                        first = pinned[0]
                        # bestip=False: 跳过全网测速; auto_switch=1: 失败 1 次
                        # 即切换候选, 保证第二台在单次请求重试预算内能轮到。
                        quotes = Quotes.factory(
                            market=self.config.market,
                            multithread=True,
                            bestip=False,
                            server=(first.host, int(first.port)),
                            auto_switch=1,
                            timeout=timeout,
                        )
                        self._pin_quotes_servers(quotes, pinned)
                        self._quotes = quotes
                    else:
                        self._quotes = Quotes.factory(
                            market=self.config.market,
                            multithread=True,
                            bestip=True,
                            timeout=timeout,
                        )
        return self._quotes

    def _get_quote_sync(self, code: str):
        """同步获取实时行情。

        2026-09-19 合同修复: sh/sz 前缀直传 (旧实现无脑剥前缀, sh000001
        上证指数被裸码路由成深市平安银行); bj 不被 tdxpy 识别, 改发裸码
        单只请求; 报文行统一过 _annotate_quote_records 补 symbol/close/
        pct_chg/amplitude。
        """
        prefix, bare, token = _parse_quote_symbol(code)
        q = self._get_quotes()
        df = q.quotes(symbol=[token])
        if df is not None and len(df) > 0:
            rec = _drop_internal_quote_columns(df).iloc[-1].to_dict()
            _annotate_quote_records([rec])
            # 兼容合同键: 报文成交量字段是 vol
            rec["volume"] = float(rec.get("vol", 0) or 0)
            for k in ("open", "high", "low", "amount"):
                rec[k] = float(rec.get(k, 0) or 0)
            return rec
        return None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        # P1 (2026-09-09 7th-round audit): 与 UpstreamClient / OmniClient 对齐,
        # DEGRADED 仍可响应, router 不应直接跳过 fallback。
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def start(self):
        """MED-2 (2026-09-14 round 8 audit): 启动时跑一次轻量连通性探测,
        避免 TDX 不可达时仍报 HEALTHY, 首次请求才报错。探测失败设 DEGRADED
        (留 UNAVAILABLE 给"明确不可用"场景, DEGRADED 表示"探测未通但保留余地
        让健康检查后续重试")。探测 2.5s 超时, 不阻塞启动。

        H2 (round 8 audit): 探测最多重试 2 次 (间隔 1s), 避免瞬时网络抖动误判。
        C1 (round 8 audit): 复用 self._quotes 实例, 避免每次 probe 都触发
        Quotes.factory() 重新测速所有服务器, 启动延迟叠加。
        """
        # M2 (round 11): 同实例 stop/start 循环与 late recovery 都经此入口,
        # 先复位停止标记。
        self._stopped = False
        # C1: 提前创建一次 Quotes 实例 (factory 内部测速一次, 后续 probe 复用)
        # 2026-09-14 round 11 audit: tdxdir 探测 (best-effort, 不抛) —
        # 早于 Quotes init, 方便用户在日志里立刻看到本地数据目录错配。
        self._probe_tdxdir()
        try:
            # H1 (round 9 audit): _ensure_quotes_initialized 同步触发
            # Quotes.factory() 测速所有 TDX 服务器 (阻塞 IO)。直接同步调
            # 会冻结事件循环, gateway 的 asyncio.wait_for(client.start())
            # 在无 await 点的同步段上无法打断 → TDX 卡死时整个 boot 挂。
            # 包进 worker thread 后 factory 不冻结事件循环, wait_for 的超时/
            # 取消才能真正生效。L1 (round 10): 走专用 unihive-upstream 阻塞池
            # (_run_blocking), 不落 asyncio.to_thread 默认池 — factory 固定
            # 服务器失败时重试预算可达 90s, 不应与默认池任务互相挤占。
            await self._run_blocking(self._ensure_quotes_initialized)
        except Exception as e:
            logger.warning(
                f"{self.name}: Quotes init failed ({type(e).__name__}: {e}); "
                f"status=DEGRADED. TDX server may be unreachable."
            )
            # C5 (round 9 audit): best-effort 清理. 若 Quotes.factory() 抛
            # 异常前已部分初始化 (e.g. socket opened), Python GC 会回收;
            # 这里强制置 None 避免下游误用半初始化对象。
            self._quotes = None
            self._status = UpstreamStatus.DEGRADED
            # 2026-09-14 round 11 audit: init 失败时不该启动 health_loop —
            # 没有 quotes 的话, health_loop 会在每 30s tick 时早 continue
            # (因 self._quotes is None), 等于纯空转。等下次 start() 重新尝试
            # factory 后再启动 health_loop。
            return

        # H2: probe 最多重试 2 次 (总共 3 次尝试)
        for attempt in range(3):
            try:
                ok = await asyncio.wait_for(
                    self._probe_with_existing_quotes(),
                    timeout=2.5,
                )
                if ok:
                    self._status = UpstreamStatus.HEALTHY
                    logger.info(
                        f"{self.name}: startup probe OK (attempt {attempt + 1}/3)"
                    )
                    return
                logger.warning(
                    f"{self.name}: startup probe empty (attempt {attempt + 1}/3)"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"{self.name}: startup probe timed out (attempt {attempt + 1}/3)"
                )
            except Exception as e:
                logger.warning(
                    f"{self.name}: startup probe failed "
                    f"({type(e).__name__}: {e}) (attempt {attempt + 1}/3)"
                )
            if attempt < 2:
                await asyncio.sleep(1.0)

        # 3 次都失败 → DEGRADED
        self._status = UpstreamStatus.DEGRADED
        tdxdir = (
            self.config.settings.tdxdir
            if self.config.settings else "N/A"
        )
        logger.warning(
            f"{self.name}: startup probe failed 3 times; "
            f"status=DEGRADED. Check TDX server reachable at "
            f"tdxdir={tdxdir}."
        )

    def _ensure_quotes_initialized(self) -> None:
        """C1 (round 8 audit): 提前初始化 Quotes 实例 (factory 测速一次),
        后续 probe 复用, 避免每次都重新 factory 触发测速叠加延迟。

        2026-09-14 round 11 audit: 这里是同步调用 Quotes.factory(), factory
        内部要测速所有 TDX 服务器 (阻塞 IO)。

        H1 (round 9 audit): 此函数保持同步 (接口简单), 但 start() 必须
        经 _run_blocking 丢到 worker thread 调用。原因: gateway startup 循环
        是顺序 await _start_upstream_client → asyncio.wait_for(client.start()),
        而同步段没有 await 点, wait_for 无法打断 → TDX 卡死时冻结整个
        事件循环 (health loop / cache cleanup 全停), 而非仅推迟本上游
        ready。factory 跑在 worker thread 后 wait_for 的超时/取消才能真正生效。
        L1 (round 10): 走专用 unihive-upstream 池而非 asyncio.to_thread 默认池。
        """
        self._get_quotes()  # 触发 lazy init (可能阻塞 N 秒)

    async def _probe_with_existing_quotes(self) -> bool:
        """C1: 复用 self._quotes 实例跑 probe (不重新 factory)。

        2026-09-14 round 11 audit: 之前在 _do_probe 内部如果 self._quotes
        为 None 就调 _get_quotes() 重建。但 _do_probe 在 background thread
        跑, 与 event loop 里的 stop() 会产生 race — thread 拿到旧 quotes
        继续用, 而 event loop 已经把它设回 None, 连接泄漏到 GC 才释放。

        新策略: probe 路径不重建 quotes。None 就返回 False, 让 health_loop
        下次 tick 重试 (期间 start() 可重新触发 _get_quotes())。
        """
        def _do_probe():
            # Round-12 簇H: stop() 后不再碰 quotes —— close 与 probe 间仍有
            # q 快照竞态 (q.quotes 不在锁内), 但停止位先于摘除置位, 挡住
            # 绝大多数晚到 probe; 残留竞态由 mootdx multithread + 异常兜底。
            if self._stopped:
                return False
            q = self._quotes
            if q is None:
                return False
            df = q.quotes(symbol=["000001"])
            return df is not None and len(df) > 0

        # L1 (round 10): probe 同样是 TDX 同步阻塞调用, 走专用阻塞池。
        return await self._run_blocking(_do_probe)

    async def start_after_timeout(self) -> None:
        """M2 (round 11): 网关启动 ``wait_for`` 超时后的后台自愈入口。

        被取消的 start() 可能留下在途 factory 线程 (线程池不可中断);
        ``_get_quotes()`` 的锁+双重检查使重复进入不会二次 factory, 故这里
        重跑一遍 start() 即可: factory 秒回 (复用在途/已建实例), probe
        重测并更新状态, 让 UNKNOWN 孤儿 client 无需重启网关即恢复。
        """
        if self._stopped:
            return
        await self.start()

    async def stop(self):
        # M2 (round 11): 先置停止位, 挡住 late recovery 重入与晚到的健康
        # 循环复活 (与摘除/关闭 Quotes 分开, 无需持锁即可立即生效)。
        self._stopped = True
        # 2026-09-16: 清理 L1 缓存 (去掉了 single-flight)
        if self._DAILY_L1_ENABLED and hasattr(self, '_l1_daily'):
            for slot in (
                self._l1_daily,
                self._l1_top_board,
                self._l1_unusual,
                # 2026-09-28 Round-3 Batch 2 H1-perf: 单 symbol 工具 L1
                # (4 个分钟线槽已移除)
                self._l1_index_kline,
                self._l1_search,
                self._l1_indicator_atr,
                self._l1_block_scan,
            ):
                if hasattr(slot, "clear"):
                    slot.clear()

        # M4 (round 8 audit): 关闭 Quotes 实例防止连接泄漏。
        # L1 (round 11): start() 超时后在途 factory 线程 (预算可达 90s) 可能
        # 在 stop() 读到 None 跳过关闭之后才晚赋值, 留下无人关的活 TDX 连接。
        # 摘除+关闭整段在 _quotes_lock 内与 factory 赋值互斥: stop 先到时
        # 排队等在同一把锁上, factory 一释放就摘除并关闭晚赋值的实例,
        # stop 的 wait_for 超时后该动作仍在 daemon 阻塞线程上排队, 最终必执行。
        def _detach_and_close_quotes() -> None:
            with self._quotes_lock:
                q, self._quotes = self._quotes, None
            if q is None:
                return
            close_method = getattr(q, "close", None)
            if close_method:
                # Quotes.close 是 TDX 同步阻塞调用, 跑在专用 unihive-upstream
                # 阻塞池 (L1 round 10), 不占 asyncio 默认池。
                close_method()

        try:
            await self._run_blocking(_detach_and_close_quotes)
        except Exception as e:
            logger.debug(f"{self.name}: Quotes.close failed: {e}")
        self._status = UpstreamStatus.UNKNOWN

    async def health_check(self) -> bool:
        """健康检查 — 读取本地 _status, 不真打 TDX 服务 (round 8 决策).

        2026-09-14 round 11 audit: 这是 stale 缓存读, 与 is_available 同源.
        router 决策走 is_available (router.py:153), health_check 主要供
        console UI / /health 端点展示。不会做 fallback 路由决策。
        """
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """工具路由

        C3 (round 9 audit): 顶层 try/except, 失败时降级 status (HEALTHY →
        DEGRADED), 让 router 快速跳过此上游, 不必等 30s health_loop。
        """
        # H1 (2026-09-19): get_income 已下线 (mootdx2 无 income() 能力;
        # finance() 只是单股财务快照), 不在分发表内; 存量 config.db 残留
        # 实体走到这里返回 Unknown tool。
        method_name = _TOOL_METHOD_NAMES.get(tool_name)
        method = getattr(self, method_name, None) if method_name else None
        if not method:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        try:
            return await method(**params)
        except Exception as e:
            # C3 (round 9 audit): 顶层异常, 降级 status 让 router 快速跳过
            if self._status == UpstreamStatus.HEALTHY:
                self._status = UpstreamStatus.DEGRADED
                logger.warning(
                    f"{self.name}: call_tool({tool_name}) raised, "
                    f"status → DEGRADED: {type(e).__name__}: {e}"
                )
            # 2026-09-14 round 10 audit: 之前这里 `raise` 让异常逃出 router
            # 整个 fallback 链 — router 只 catch TimeoutError, 其它异常会直
            # 接打到 MCP 协议层, fallback 失效。改为 return ToolResult,
            # 让 router 看到 success=False 后串行走到下一上游。
            return self._error_result(e, f"call_tool({tool_name})")

    async def get_quote(self, code: str) -> ToolResult:
        """获取实时行情

        Args:
            code: 股票代码，如 '600000' 或 'sh600000'

        Returns:
            ToolResult: 包含实时行情数据或错误信息
        """
        request_id = self._new_request_id()
        self._metrics["total_requests"] += 1
        logger.info(f"[{request_id}] get_quote({code})")

        try:
            data = await self._run_blocking(self._get_quote_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"股票 {code} 无行情数据（可能停牌或非交易日）")
        except Exception as e:
            logger.error(f"[{request_id}] get_quote failed: {e}")
            return self._error_result(e, f"get_quote({code})")

    def _get_kline_sync(self, code: str, type: str = "day", limit: int = 100):
        """同步获取K线 (bars offset 即条数, 旧码再 tail(limit) 是重复截断)。"""
        q = self._get_quotes()
        sym = _strip_market_prefix(code)
        freq = FREQ_MAP.get(type, 9)
        df = q.bars(symbol=sym, frequency=freq, offset=limit)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_kline(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取K线

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000

        Returns:
            数据量超过限制时返回错误提示
        """
        # 数据量检查 (2026-09-19 LOW: 旧码只拦 >1000, 0/负数会带着非法
        # offset 打到 TDX)
        if not isinstance(limit, int) or not (1 <= limit <= MAX_LIMIT_KLINE):
            msg = f"limit 需为 1-{MAX_LIMIT_KLINE} 的整数, 收到 {limit!r}"
            return ToolResult(
                success=False,
                error=msg,
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": msg,
                    "recoverable": False,
                },
            )

        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_kline_sync, code, type, limit)
            if data:
                # 数据合理性校验
                valid_data = [r for r in data if _validate_kline_record(r)]
                if not valid_data:
                    return self._no_data_result("K线数据校验失败（数据异常）")
                return ToolResult(success=True, data=valid_data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_kline failed: {e}")
            return self._error_result(e, f"get_kline({code})")

    # ========== 批量行情 ==========
    def _get_batch_quote_sync(self, tokens: list[str]) -> list[dict] | None:
        """同步批量行情 (tokens 已是 tdxpy 可直接消费的形式)。

        返回报文行并就地补出 symbol/close/pct_chg/amplitude。调用方负责
        BJ 隔离: 批次里混入 market=2 时 tdxpy 整批返 None。
        """
        q = self._get_quotes()
        df = q.quotes(symbol=list(tokens))
        if df is not None and len(df) > 0:
            rows = _drop_internal_quote_columns(df).to_dict(orient="records")
            _annotate_quote_records(rows)
            return rows
        return None

    async def get_batch_quote(self, codes: str) -> ToolResult:
        """获取批量行情 (最多 50 只)。

        2026-09-19 合同修复:
        - >50 / 空串 / 非法代码 → invalid_param / invalid_symbol, 不打 TDX;
        - 沪深合并一批前缀直传; 北交所每只独立一批裸码 (混批 tdxpy 整批
          返 None), BJ 批次失败不拖垮沪深, 在 error_detail.warn 说明。
        """
        self._metrics["total_requests"] += 1
        raw_parts = [p.strip() for p in str(codes or "").split(",") if p.strip()]
        if not raw_parts:
            return self._invalid_param_result("codes 不能为空")
        try:
            parsed = [_parse_quote_symbol(p) for p in raw_parts]
        except ValueError as e:
            return self._invalid_symbol_result(str(e))
        if len(parsed) > MAX_LIMIT_BATCH_QUOTE:
            return self._invalid_param_result(
                f"批量请求超过 {MAX_LIMIT_BATCH_QUOTE} 只股票限制"
            )

        try:
            main_tokens = [tok for pref, _, tok in parsed if pref in ("sh", "sz")]
            bj_tokens = [(pref, bare, tok) for pref, bare, tok in parsed if pref == "bj"]

            rows_by_key: dict[tuple[str, str], dict] = {}
            if main_tokens:
                rows = await self._run_blocking(self._get_batch_quote_sync, main_tokens)
                for rec in rows or []:
                    mkt, bare = _split_prefixed_symbol(rec.get("symbol", ""))
                    rows_by_key[(mkt, bare)] = rec

            failed_bj: list[str] = []
            for _, _, tok in bj_tokens:
                # BJ 只能单只裸码独立批次 (含 market=2 的混批整批返 None)
                rows = await self._run_blocking(self._get_batch_quote_sync, [tok])
                if rows:
                    mkt, bare = _split_prefixed_symbol(rows[0].get("symbol", ""))
                    rows_by_key[(mkt, bare)] = rows[0]
                else:
                    failed_bj.append(tok)

            # 沪深帧不保证逐码返回: 停牌/代码无效/服务端漏行会被静默丢弃,
            # 必须拿 parsed 全集对照 rows_by_key, 否则调用方以为少传了代码。
            missing_main = [
                f"{pref}{bare}"
                for pref, bare, _ in parsed
                if pref in ("sh", "sz") and (pref, bare) not in rows_by_key
            ]

            ordered = [
                rows_by_key[(pref, bare)]
                for pref, bare, _ in parsed
                if (pref, bare) in rows_by_key
            ]
            if not ordered:
                return self._no_data_result()
            result = ToolResult(success=True, data=ordered, source="mootdx2")
            warn_parts: list[str] = []
            if missing_main:
                warn_parts.append(f"沪深行情无返回: {','.join(missing_main)}")
            if failed_bj:
                warn_parts.append(f"北交所行情批次无返回: {','.join(failed_bj)}")
            if warn_parts:
                warn_parts.append(f"已返回其余 {len(ordered)} 只")
                result.error_detail = {
                    "warn": "；".join(warn_parts),
                    "recoverable": True,
                }
            return result
        except Exception as e:
            logger.error(f"get_batch_quote failed: {e}")
            return self._error_result(e, f"get_batch_quote({codes})")

    # ========== 批量K线 ==========
    # H4 (2026-09-19): batch_bars 内部 BatchDailyFetcher 循环调
    # quotes.bars(frequency=...), 与单股 K 线共用同一套 TDX category 码,
    # 必须用模块级 FREQ_MAP (8/0/1/2/3), 旧 FREQ_MAP_BATCH 的 minute 码
    # (1/5/15/30/60) 实际取到的是 15min/周线/非法类别。
    def _get_batch_bars_sync(self, bare_codes: list[str], type: str = "day", limit: int = 100):
        """同步批量K线 (bare_codes 已剥前缀并校验)。"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        # batch_bars 的条数入参是 offset (内部透传给 bars), 旧代码传 count
        # 会落进 **kwargs 被忽略, 每只恒取 800 根; bars offset 硬上限 800。
        result = q.batch_bars(
            symbols=bare_codes, frequency=freq, start=0, offset=min(limit, 800)
        )
        if result and isinstance(result, dict):
            return {k: v.to_dict(orient="records") if v is not None else [] for k, v in result.items()}
        return None

    async def batch_bars(self, codes: str, type: str = "day", limit: int = 100) -> ToolResult:
        """批量获取K线 (≤20 只, 每只 1-500 根; 违约 invalid_param, 不打 TDX)。"""
        parts = [p.strip() for p in str(codes or "").split(",") if p.strip()]
        if not parts:
            return self._invalid_param_result("codes 不能为空")
        if len(parts) > MAX_LIMIT_BATCH_BARS_CODES:
            return self._invalid_param_result(
                f"批量K线一次最多 {MAX_LIMIT_BATCH_BARS_CODES} 只, 收到 {len(parts)} 只"
            )
        if not isinstance(limit, int) or not (1 <= limit <= MAX_LIMIT_BATCH_BARS):
            return self._invalid_param_result(
                f"limit 需为 1-{MAX_LIMIT_BATCH_BARS} 的整数, 收到 {limit!r}"
            )
        bare_codes: list[str] = []
        for part in parts:
            bare = _strip_market_prefix(part)
            if not (len(bare) == 6 and bare.isdigit()):
                return self._invalid_symbol_result(
                    f"代码格式错误: {part!r} (需 6 位数字, 可带 sh/sz/bj 前缀)"
                )
            bare_codes.append(bare)

        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(
                self._get_batch_bars_sync, bare_codes, type, limit
            )
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"batch_bars failed: {e}")
            return self._error_result(e, f"batch_bars({codes})")

    # ========== 今日分时 ==========
    def _get_minute_data_sync(self, code: str):
        """同步今日分时"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.minute(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_minute_data(self, code: str) -> ToolResult:
        """获取今日分时"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_minute_data_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_minute_data failed: {e}")
            return self._error_result(e, f"get_minute_data({code})")

    # ========== 逐笔成交 ==========
    def _get_trade_sync(self, code: str):
        """同步逐笔成交"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transaction(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_trade(self, code: str) -> ToolResult:
        """获取逐笔成交"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_trade_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_trade failed: {e}")
            return self._error_result(e, f"get_trade({code})")

    # ========== 历史逐笔 ==========
    def _get_trade_history_sync(self, code: str, date: str, start: int, count: int):
        """同步历史逐笔"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transactions(symbol=sym, date=date, start=start, offset=count)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_trade_history(self, code: str, date: str, start: int = 0, count: int = 100) -> ToolResult:
        """获取历史逐笔"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_trade_history_sync, code, date, start, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_trade_history failed: {e}")
            return self._error_result(e, f"get_trade_history({code})")

    # ========== 指数K线 ==========
    def _get_index_kline_sync(self, code: str, type: str):
        """同步指数K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_index_kline(self, code: str, type: str = "day") -> ToolResult:
        """获取指数K线"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_index_kline_sync, code, type)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_kline failed: {e}")
            return self._error_result(e, f"get_index_kline({code})")

    # ========== 全量代码列表 ==========
    # mootdx2 Quotes.stocks(market) 只支持沪深 (TDX 协议 market: 1=SH/0=SZ),
    # market=2(BJ) 库会直接抛 MootdxValidationException; 全市场走 stock_all()。
    _CODE_LIST_MARKETS = {"sh": 1, "sz": 0}

    def _get_code_list_sync(self, exchange: str) -> list[dict] | None:
        """同步全量代码列表。返回合同字段 [{"code", "name"}, ...]。

        mootdx2 不支持北交所 (bj), 该入参返回 None 由上层转 no_data。
        """
        q = self._get_quotes()
        exchange = (exchange or "").strip().lower()
        if exchange:
            market = self._CODE_LIST_MARKETS.get(exchange)
            if market is None:
                logger.info(
                    "get_code_list: mootdx2 全量代码列表不支持交易所 %r (仅 sh/sz)",
                    exchange,
                )
                return None
            df = q.stocks(market=market)
        else:
            df = q.stock_all()
        if df is None or len(df) == 0:
            return None
        # 解析器行含 volunit/decimal_point 等内部列, 只保留合同承诺的 code/name
        return [
            {"code": str(row["code"]), "name": row.get("name", "")}
            for row in df.to_dict(orient="records")
            if row.get("code")
        ]

    async def get_code_list(self, exchange: str = "") -> ToolResult:
        """获取全量代码列表

        Args:
            exchange: 交易所, sh=上海 / sz=深圳 / bj=北京(库不支持);
                      "" 或 None 返回沪深全部
        """
        self._metrics["total_requests"] += 1
        exchange_val = (exchange or "").strip().lower()
        try:
            data = await self._run_blocking(self._get_code_list_sync, exchange_val
            )
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            if exchange_val == "bj":
                return self._no_data_result(
                    "mootdx2 全量代码列表仅支持沪深 (sh/sz),暂不支持北京证券交易所"
                )
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_code_list failed: {e}")
            return self._error_result(e, f"get_code_list({exchange})")

    # ========== 股票/ETF 代码列表 ==========
    # H1 (2026-09-19): mootdx2 没有 stock()/etf() 方法 (旧实现生产必抛
    # AttributeError), 真实方法面是 stocks(market=0|1) + stock_all(),
    # 返回该市场全部证券 (含基金/债券/指数), 必须按代码前缀过滤。
    def _list_market_securities_sync(self, market: int) -> list[dict]:
        """拉取单市场证券列表原始行 (code/name/decimal_point/...)。"""
        q = self._get_quotes()
        df = q.stocks(market=market)
        if df is None or len(df) == 0:
            return []
        return df.to_dict(orient="records")

    def _list_prefixed_codes_sync(self, market: int, code_prefixes: tuple) -> list[tuple[str, str]]:
        """返回 [(market_prefix, code), ...], 仅保留匹配前缀的证券, 顺序稳定。"""
        prefix = _MARKET_ID_TO_PREFIX[market]
        pairs = []
        for row in self._list_market_securities_sync(market):
            code = str(row.get("code") or "")
            if code.startswith(code_prefixes):
                pairs.append((prefix, code))
        return pairs

    def _list_a_share_codes_sync(self) -> list[tuple[str, str]]:
        """沪深 A 股 (沪先深后): 沪 60/68, 深 00/30。"""
        sh = self._list_prefixed_codes_sync(1, _A_SHARE_PREFIXES_SH)
        sz = self._list_prefixed_codes_sync(0, _A_SHARE_PREFIXES_SZ)
        return sh + sz

    def _get_stock_codes_sync(self, limit: int, prefix: bool):
        """同步股票代码列表 (沪深 A 股, 沪先深后)"""
        pairs = self._list_a_share_codes_sync()[: max(int(limit), 0)]
        if not pairs:
            return None
        if prefix:
            return [f"{mkt}{code}" for mkt, code in pairs]
        return [code for _, code in pairs]

    async def get_stock_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取股票代码列表"""
        if not isinstance(limit, int) or limit <= 0:
            return self._invalid_param_result(f"limit 需为正整数, 收到 {limit!r}")
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_stock_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_stock_codes failed: {e}")
            return self._error_result(e, f"get_stock_codes(limit={limit})")

    # ========== ETF代码列表 ==========
    def _list_etf_codes_sync(self) -> list[tuple[str, str]]:
        """沪深 ETF (沪先深后): 沪 51/56/58, 深 15。16 开头为 LOF 不计。"""
        sh = self._list_prefixed_codes_sync(1, _ETF_PREFIXES_BY_MARKET[1])
        sz = self._list_prefixed_codes_sync(0, _ETF_PREFIXES_BY_MARKET[0])
        return sh + sz

    def _get_etf_codes_sync(self, limit: int, prefix: bool):
        """同步ETF代码列表"""
        pairs = self._list_etf_codes_sync()[: max(int(limit), 0)]
        if not pairs:
            return None
        if prefix:
            return [f"{mkt}{code}" for mkt, code in pairs]
        return [code for _, code in pairs]

    async def get_etf_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取ETF代码列表"""
        if not isinstance(limit, int) or limit <= 0:
            return self._invalid_param_result(f"limit 需为正整数, 收到 {limit!r}")
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_etf_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_etf_codes failed: {e}")
            return self._error_result(e, f"get_etf_codes(limit={limit})")

    # ========== ETF列表 ==========
    def _get_etf_list_sync(self, exchange: str, limit: int):
        """同步ETF列表。返回合同字段 [{"code","name","exchange"}, ...]。

        mootdx2 stocks(market) 仅支持沪深 (1=SH/0=SZ), bj 返回 None
        由上层转 no_data。
        """
        exchange_val = (exchange or "").strip().lower()
        market = self._CODE_LIST_MARKETS.get(exchange_val)
        if market is None:
            return None
        result = []
        for row in self._list_market_securities_sync(market):
            code = str(row.get("code") or "")
            if not code.startswith(_ETF_PREFIXES_BY_MARKET[market]):
                continue
            result.append(
                {"code": code, "name": str(row.get("name", "")), "exchange": exchange_val.upper()}
            )
            if len(result) >= limit:
                break
        return result or None

    async def get_etf_list(self, exchange: str = "sz", limit: int = 100) -> ToolResult:
        """获取ETF列表"""
        if not isinstance(limit, int) or limit <= 0:
            return self._invalid_param_result(f"limit 需为正整数, 收到 {limit!r}")
        self._metrics["total_requests"] += 1
        exchange_val = (exchange or "").strip().lower()
        try:
            data = await self._run_blocking(self._get_etf_list_sync, exchange_val, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            if exchange_val not in self._CODE_LIST_MARKETS:
                return self._no_data_result(
                    "mootdx2 ETF 列表仅支持沪深 (sh/sz),暂不支持该交易所"
                )
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_etf_list failed: {e}")
            return self._error_result(e, f"get_etf_list({exchange})")

    # ========== 市场证券数量 ==========
    def _get_market_count_sync(self, market: int):
        """同步市场证券数量"""
        q = self._get_quotes()
        count = q.stock_count(market=market)
        return {"market": market, "count": count}

    async def get_market_count(self, market: int = 0) -> ToolResult:
        """获取市场证券数量"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_market_count_sync, market)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_market_count failed: {e}")
            return self._error_result(e, f"get_market_count({market})")

    # ========== 交易日查询 ==========
    # 注意: 纯周末数学, **不剔除法定节假日** (网关不内置节假日历, 调休/休市
    # 日会被计作交易日), YAML 合同已诚实声明。
    def _get_workday_sync(self, date: str | None, count: int) -> list[str]:
        """同步交易日查询: 从 target (默认今天) 往前找 count 个周一~周五。"""
        target = _parse_compact_date(date) if date else datetime.datetime.now()
        results = []
        current = target
        while len(results) < count:
            current -= datetime.timedelta(days=1)
            if current.weekday() < 5:  # 周一到周五
                results.append(current.strftime("%Y%m%d"))
        results.reverse()
        return results

    async def get_workday(self, date: str | None = None, count: int = 1) -> ToolResult:
        """获取交易日 (date 省略默认今天; 纯周末推算, 未剔除法定节假日)。"""
        self._metrics["total_requests"] += 1
        if not isinstance(count, int) or not (1 <= count <= MAX_WORKDAY_COUNT):
            return self._invalid_param_result(
                f"count 需为 1-{MAX_WORKDAY_COUNT} 的整数, 收到 {count!r}"
            )
        if date:
            try:
                _parse_compact_date(date)
            except ValueError as e:
                return self._invalid_param_result(str(e))
        try:
            data = await self._run_blocking(self._get_workday_sync, date, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_workday failed: {e}")
            return self._error_result(e, f"get_workday({date})")

    # ========== 交易日范围 ==========
    def _get_workday_range_sync(
        self, start: str | None, end: str | None
    ) -> list[str]:
        """同步交易日范围 (start/end 省略默认今天)。"""
        start_dt = _parse_compact_date(start) if start else datetime.datetime.now()
        end_dt = _parse_compact_date(end) if end else datetime.datetime.now()
        results = []
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:
                results.append(current.strftime("%Y%m%d"))
            current += datetime.timedelta(days=1)
        return results

    async def get_workday_range(
        self, start: str | None = None, end: str | None = None
    ) -> ToolResult:
        """获取交易日范围 (纯周末推算, 未剔除法定节假日)。"""
        self._metrics["total_requests"] += 1
        try:
            start_dt = _parse_compact_date(start) if start else datetime.datetime.now()
            end_dt = _parse_compact_date(end) if end else datetime.datetime.now()
        except ValueError as e:
            return self._invalid_param_result(str(e))
        if start_dt > end_dt:
            return self._invalid_param_result(
                f"start 不能晚于 end: {start} > {end}"
            )
        try:
            data = await self._run_blocking(self._get_workday_range_sync, start, end)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_workday_range failed: {e}")
            return self._error_result(e, f"get_workday_range({start}-{end})")

    # ========== 全指K线 ==========
    def _get_index_all_sync(self, code: str, type: str, limit: int):
        """同步全指K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and len(df) > 0:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_index_all(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取全指K线"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_index_all_sync, code, type, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_all failed: {e}")
            return self._error_result(e, f"get_index_all({code})")

    # H1 (2026-09-19): get_income 已删除 — mootdx2 无盘后收益序列能力
    # (q.income 不存在; q.finance 只是单股财务快照, 语义不符不能顶替)。

    # ========== 自定义板块 ==========
    @staticmethod
    def _parse_blk_content(content: str) -> list[dict]:
        """.blk 行格式: market_id(1=SH / 0=SZ / 2=BJ) + 6 位代码。"""
        market_prefix_map = {"1": "sh", "0": "sz", "2": "bj"}
        result = []
        for line in content.splitlines():
            line = line.strip()
            if len(line) < 7 or not line[0].isdigit():
                continue
            code = line[1:]
            if not (code.isdigit() and len(code) == 6):
                continue
            result.append({"code": code, "market": market_prefix_map.get(line[0], "sz")})
        return result

    def _scan_blk_files_sync(self) -> list[dict]:
        """扫描 T0002/blocknew/*.blk 列出全部自定义板块。

        适用于 V7.73+ 通达信：cfg 索引格式可能更新，但 .blk 文件本身仍可读。
        先经 Customize.search DataFrame 拿 block_type(stem)→blockname(中文名)
        映射; cfg 解析失败时退化为用 .blk 文件名作为 sector_name。

        .blk 文件格式：每行 = market_id(1=SH / 0=SZ / 2=BJ) + 6位股票代码
        例如：'1600000' 表示 SH 600000，'0000001' 表示 SZ 000001。
        """
        blocknew_dir = self._require_blocknew_dir()
        tdxdir = self._get_tdxdir()
        name_by_stem = self._load_custom_sector_name_map(tdxdir)

        results = []
        for blk_path in sorted(blocknew_dir.glob("*.blk")):
            blk_name = blk_path.stem
            display_name = name_by_stem.get(blk_name, blk_name)
            try:
                content = blk_path.read_text(encoding="gbk", errors="ignore")
            except Exception:
                content = blk_path.read_text(encoding="utf-8", errors="ignore")
            results.append({
                "sector_name": display_name,
                "sector_type": "custom",
                "stock_count": len(self._parse_blk_content(content)),
                "blk_file": blk_name,
            })
        return results

    async def get_custom_sector_list(self) -> ToolResult:
        """获取自定义板块列表（用户在 TDX 客户端手动维护）

        数据源：{tdxdir}/T0002/blocknew/blocknew.cfg + *.blk。
        tdxdir 缺失 → tdx_not_installed; blocknew 目录缺失 →
        tdx_custom_sector_unavailable。
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._scan_blk_files_sync)
            return ToolResult(success=True, data=data, source="mootdx2")
        except _CustomSectorError as e:
            return self._custom_sector_error_result(e.error_type, str(e))
        except Exception as e:
            logger.error(f"get_custom_sector_list failed: {e}")
            return self._error_result(e, "get_custom_sector_list()")

    def _get_custom_sector_stocks_sync(self, sector_code: str, market: str = "auto") -> list:
        """同步读取自定义板块成分股

        Args:
            sector_code: 板块中文名（DataFrame blockname）或 .blk 文件名 stem;
              中文名经 Customize.search(group=True) DataFrame 解析为 stem。
            market: 保留参数 (对齐其它工具签名; 市场按 .blk 行 market_id 推断)。

        2026-09-19 H2 修复: 旧码 ``for entry in grouped`` 直接迭代 DataFrame
        得到的是列名字符串, isinstance tuple 恒 False → 中文名解析整条链路
        是死的, 传中文板块名必然落空。
        """
        sector_code = str(sector_code or "").strip()
        blocknew_dir = self._require_blocknew_dir()
        tdxdir = self._get_tdxdir()
        name_by_stem = self._load_custom_sector_name_map(tdxdir)

        # 1. 解析 sector_code → .blk stem:
        #    a) 入参本身就是 stem (block_type 命中或文件存在, 向后兼容)
        #    b) 入参是中文名 (blockname 命中)
        blk_stem = sector_code
        if sector_code not in name_by_stem:
            for stem, name in name_by_stem.items():
                if name == sector_code:
                    blk_stem = stem
                    break

        blk_path = blocknew_dir / f"{blk_stem}.blk"
        if not blk_path.exists():
            raise _CustomSectorError(
                MooTDXErrorType.SECTOR_NOT_FOUND.value,
                f"自定义板块未找到: {sector_code!r} "
                f"(既未匹配板块中文名, 也无 {blk_stem}.blk 文件)",
            )

        try:
            content = blk_path.read_text(encoding="gbk", errors="ignore")
        except Exception:
            content = blk_path.read_text(encoding="utf-8", errors="ignore")
        return self._parse_blk_content(content)

    async def get_custom_sector_stocks(
        self, sector_code: str, market: str = "auto"
    ) -> ToolResult:
        """获取自定义板块的成分股

        Args:
            sector_code: 自定义板块中文名（TDX 客户端命名）或 .blk 文件名 stem
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(
                self._get_custom_sector_stocks_sync, sector_code, market
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except _CustomSectorError as e:
            return self._custom_sector_error_result(e.error_type, str(e))
        except Exception as e:
            logger.error(f"get_custom_sector_stocks failed: {e}")
            return self._error_result(e, f"get_custom_sector_stocks({sector_code})")

    # ========== 指数概览 ==========
    # (code, name, market_prefix, market_id): 前缀直传给 quotes,
    # get_stock_markets 显式识别 sh/sz (裸码 "000001" 会被数字推断成深市股票
    # 平安银行, 与上证指数撞码)。
    _INDEX_SPECS = [
        ("000001", "上证指数", "sh", 1),
        ("399001", "深证成指", "sz", 0),
        ("399006", "创业板", "sz", 0),
        ("000688", "科创50", "sh", 1),
        ("889999", "北证50", "bj", 2),
        ("000300", "沪深300", "sh", 1),
    ]

    def _index_overview_sync(self) -> list:
        """同步获取主要指数概览。

        H3 (2026-09-19):
        - sh/sz 指数带前缀直传 quotes, 杜绝裸码数字推断把上证指数路由成
          深市股票; 回包按 (market_id, code) 匹配, 绝不按位置 zipper
          (上游丢行会让后续名称整体错位)。
        - 北证 889999 必须单独一批: tdxpy get_security_quotes 批次中只要
          含 market=2 就整批返回 None; 当前协议通常仍取不到, 缺则省略,
          不伪造 0 值。
        """
        q = self._get_quotes()
        sh_sz_symbols = [
            f"{prefix}{code}"
            for code, _, prefix, market_id in self._INDEX_SPECS
            if market_id in (0, 1)
        ]
        quotes_by_key: dict[tuple[int, str], dict] = {}
        df = q.quotes(symbol=sh_sz_symbols)
        if df is not None and len(df) > 0:
            for rec in _drop_internal_quote_columns(df).to_dict(orient="records"):
                try:
                    key = (int(rec.get("market")), str(rec.get("code", "")))
                except (TypeError, ValueError):
                    continue
                quotes_by_key[key] = rec

        # 北交所单独 best-effort 批次 (tdxpy 现阶段多返回空)
        try:
            df_bj = q.quotes(symbol=["889999"])
            if df_bj is not None and len(df_bj) > 0:
                for rec in _drop_internal_quote_columns(df_bj).to_dict(orient="records"):
                    try:
                        market_id = int(rec.get("market"))
                        key = (market_id, str(rec.get("code", "")))
                    except (TypeError, ValueError):
                        continue
                    if market_id == 2:
                        quotes_by_key[key] = rec
        except Exception as e:  # 北交所缺失不影响沪深指数
            logger.debug(f"index overview: BJ quote skipped: {e}")

        result = []
        for code, name, market, market_id in self._INDEX_SPECS:
            rec = quotes_by_key.get((market_id, code))
            if rec is None:
                continue
            result.append({
                "code": code,
                "name": name,
                "market": market,
                "close": float(rec.get("price", 0) or 0),
                "change_pct": _quote_pct_chg(rec),
            })
        return result

    async def get_index_overview(self) -> ToolResult:
        """获取主要指数概览（上证、深证、创业板、科创50、北证50、沪深300；
        北交所协议不支持时北证50 缺省, 返回其余可用指数）"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._index_overview_sync)
            if not data:
                return self._no_data_result("指数数据获取失败")
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_index_overview failed: {e}")
            return self._error_result(e, "get_index_overview")

    # ========== F10 基础数据 ==========
    def _get_f10_sync(self, symbol: str, name: str = ""):
        """同步F10基础数据"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.F10(symbol=sym, name=name)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_f10(self, symbol: str, name: str = "") -> ToolResult:
        """获取F10基础数据

        Args:
            symbol: 股票代码
            name: F10数据类型名称（可选）
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_f10_sync, symbol, name)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_f10 failed: {e}")
            return self._error_result(e, f"get_f10({symbol})")

    # ========== F10 公司概况 ==========
    def _get_f10_company_sync(self, symbol: str):
        """同步F10公司概况"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.F10C(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_f10_company(self, symbol: str) -> ToolResult:
        """获取F10公司概况

        Args:
            symbol: 股票代码
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_f10_company_sync, symbol)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_f10_company failed: {e}")
            return self._error_result(e, f"get_f10_company({symbol})")

    # ========== 分钟K线 ==========
    def _get_minutes_sync(self, symbol: str, date: str | None = None):
        """同步分钟K线 (date 省略默认当天; 旧默认值 '20191023' 是演示脏值)。"""
        q = self._get_quotes()
        sym = _strip_market_prefix(symbol)
        target = date or datetime.date.today().strftime("%Y%m%d")
        df = q.minutes(symbol=sym, date=target)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_minutes(self, symbol: str, date: str | None = None) -> ToolResult:
        """获取分钟K线

        Args:
            symbol: 股票代码
            date: 日期 YYYYMMDD, 省略默认当天
        """
        if date:
            try:
                _parse_compact_date(date)
            except ValueError as e:
                return self._invalid_param_result(str(e))
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_minutes_sync, symbol, date)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_minutes failed: {e}")
            return self._error_result(e, f"get_minutes({symbol})")

    # ========== 离线日线（Reader，TDX 本地 .day 文件） ==========
    def _get_daily_sync(
        self, code: str, adjust: str = "none", start_date: str = "", end_date: str = ""
    ) -> tuple[list[dict], dict]:
        """同步读取离线日线数据, 返回 **(filtered_records, meta)**。

        2026-09-19 LOW: meta 随返回值带出 (旧实现写 self._last_daily_meta
        实例属性, 并发调用互相覆盖, 且要求 wrapper 用 getattr 回读)。

        2026-09-28 Round-3 Batch 2 H2-perf: 内部不再应用 start/end 过滤,
        由调用方 (``_get_daily`` async + ``_get_daily_batch_local_sync``)
        统一走 ``_filter_records_by_date``, 与 L1 命中路径共用同一份过滤实现,
        避免缓存双份同 symbol 不同窗口的全量拷贝。

        Args:
            code: 6 位代码，可带 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀
            adjust: 复权方式 none / qfq / hfq（默认不复权）
            start_date: 起始日期 yyyy-MM-dd（可选, 已废弃保留兼容, 不再过滤）
            end_date: 结束日期 yyyy-MM-dd（可选, 已废弃保留兼容, 不再过滤）

        代码解析规则见 ``index_registry``（2026-09-20 移除旧
        INDEX_MARKET_MAP 枚举表）：
            - 显式前缀/后缀始终信任；市场已知时按前缀约定判类型
            - 裸 000xxx 是跨市场歧义码，本入口按深市股票解析并在
              meta.warn 带消歧提示，读上证指数请显式传 ``sh000001``
            - kind=index 时 adjust 强制覆盖为 'none' (meta.warn)，
              否则指数无 XDXR 会抛 ReversionError
        """
        # 入口解析; ValueError 由调用方 _get_daily 入口转 invalid_symbol
        parsed = parse_symbol(code)
        meta = {"kind": parsed.kind, "warn": None}

        # 成功路径提示（裸 000xxx 消歧 / 指数复权强制 none），
        # 必须在 adjust 被覆盖之前用原始入参计算
        meta["warn"] = _daily_request_warn(parsed, adjust)

        # 指数强制无复权（坑 #3）
        if parsed.kind == INDEX_KIND and adjust in ("qfq", "hfq"):
            adjust = "none"

        # Reader 调用符号：市场已知（指数 / 显式后缀股票）→ 带市场前缀；
        # 裸码股票 → 交 reader.find_path 按前缀推断
        reader_symbol = to_reader_symbol(parsed)

        reader = self._get_reader()
        df = reader.daily(symbol=reader_symbol, adjust=adjust)
        if df is None or getattr(df, "empty", True):
            # kind 信息随 meta 返回, 便于 _get_daily 给更明确的 no_data 文案
            return [], meta

        # H2-perf (Round-3 Batch 2): 返回 FULL records, 由调用方按需切窗,
        # L1 缓存也存 FULL records 避免 (start, end) 维度爆炸 (3125 组合).
        records = self._records_from_indexed_df(df)
        return records, meta

    def _get_daily_batch_local_sync(
        self,
        code_list: list[str],
        adjust: str,
        start_date: str,
        end_date: str,
        limit: int = 0,
    ) -> tuple[dict[str, list[dict]], list[str], bool]:
        """同步批量获取离线日线数据。

        ⚠ 测试专用 / 生产勿调 (2026-09-28 性能审视): 生产路径
        ``get_daily_batch_local`` 走 ``_get_daily_batch_async`` 逐 symbol
        async 调度 (受 reader_gate + 批量子闸门约束, 命中 L1 不出线程)。本 sync
        实现仅供单测在无 event-loop 场景直接调用。

        2026-09-28 拉通: 缓存/取数逻辑收进 ``_get_daily_records_sync`` 单点,
        本方法只保留批量编排 (key 归组 / limit 截断 / 异常分类)。

        Args:
            code_list: 股票代码列表
            adjust: 复权类型 none/qfq/hfq
            start_date: 起始日期 yyyy-MM-dd
            end_date: 结束日期 yyyy-MM-dd
            limit: 每只股票最多返回根数 (0/负数 = 不截断, 默认 0)

        Returns:
            (成功结果字典, 警告列表, 是否有结构性失败)

        Raises:
            Exception: 未知异常直接上抛, 不吞进 warnings.
        """
        import struct as _struct
        from .index_registry import parse_symbol

        results: dict[str, list[dict]] = {}
        warnings: list[str] = []
        has_structural_errors = False

        for code in code_list:
            try:
                parsed = parse_symbol(code)
                key = f"{parsed.market}{parsed.code}" if parsed.market else parsed.code
                records, meta, _hit = self._get_daily_records_sync(
                    code, adjust, start_date, end_date
                )
                if limit and limit > 0 and len(records) > limit:
                    records = records[-limit:]
                results[key] = records
                # 000xxx 消歧提示不论 records 空否都拼入 warnings (H4-func)
                if meta.get("warn"):
                    warnings.append(f"{code}: {meta['warn']}")
            except ValueError as e:
                self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
                warnings.append(f"{code}: invalid_symbol - {e}")
            except (OSError, _struct.error) as e:
                self._record_error(MooTDXErrorType.INTERNAL_ERROR.value)
                has_structural_errors = True
                warnings.append(f"{code}: file_error - {type(e).__name__}: {e}")
            except Exception:
                # 未知异常直接上抛 — 吞进 warnings 会造成批结果对账失败
                raise

        return results, warnings, has_structural_errors

    # ========== 日线取数核心 (get_daily_local / get_daily_batch_local 拉通) ==========
    # 2026-09-28 简化: 旧实现把 L1 预检/写回/切窗在 get_daily、
    # _get_daily_batch_one、_get_daily_batch_local_sync 三处各写一遍, 已两次
    # 漂移出细微不一致。现收敛为单点: key 构造 (_daily_l1_get/_daily_l1_put)
    # + 取数 (_get_daily_records_sync) + 闸门调度 (_get_daily_records)。
    #
    # 缓存机制 (刻意保持一层 L1, SQLite cache_ttl_key 对本工具为空):
    #   key = (today, reader_symbol, adjust) — 日期在 key 里, 跨天自动 miss,
    #   这是"当日有效"的正确性来源; TTL (到次日 9:00) 只是内存回收上限。
    #   槽里存 FULL records (key 不含 start/end, 防窗口组合爆炸), 命中后
    #   _filter_records_by_date 切窗。仅 adjust=none 且非空才写。
    def _daily_l1_get(self, parsed, adjust: str):
        """按 (today, symbol, adjust) 查 L1; 非 adjust=none 不查 → records | None。"""
        actual_adjust = (
            "none" if parsed.kind == INDEX_KIND and adjust in ("qfq", "hfq") else adjust
        )
        if not (self._DAILY_L1_ENABLED and actual_adjust == "none"):
            return None
        key = (datetime.date.today().isoformat(), to_reader_symbol(parsed), actual_adjust)
        return self._l1_daily.get(key)

    def _daily_l1_put(self, parsed, adjust: str, full: list) -> None:
        """写 L1: 仅 adjust=none + 非空 (门槛看 FULL, 空窗口也应入缓存)。"""
        actual_adjust = (
            "none" if parsed.kind == INDEX_KIND and adjust in ("qfq", "hfq") else adjust
        )
        if self._DAILY_L1_ENABLED and actual_adjust == "none" and full:
            key = (datetime.date.today().isoformat(), to_reader_symbol(parsed), actual_adjust)
            self._l1_daily.put(key, full)

    def _get_daily_records_sync(
        self, code: str, adjust: str, start_date: str, end_date: str
    ) -> tuple[list[dict], dict, bool]:
        """单 symbol 日线取数单点 (阻塞, 可能读 .day 文件)。

        L1 命中 → 切窗返回; miss → ``_get_daily_sync`` 单次读盘 → 切窗 → 写 L1。
        返回 ``(records_after_window, meta, cache_hit)``; parse_symbol 的
        ValueError 直接上抛由入口分类。
        """
        parsed = parse_symbol(code)
        cached = self._daily_l1_get(parsed, adjust)
        if cached is not None:
            meta = {"kind": parsed.kind, "warn": _daily_request_warn(parsed, adjust)}
            return self._filter_records_by_date(cached, start_date, end_date), meta, True
        full, meta = self._get_daily_sync(code, adjust, start_date, end_date)
        records = self._filter_records_by_date(full, start_date, end_date)
        self._daily_l1_put(parsed, adjust, full)
        return records, meta, False

    async def _get_daily_records(
        self, code: str, adjust: str, start_date: str, end_date: str
    ) -> tuple[list[dict], dict, bool]:
        """async 调度核心: reader_gate 内经线程池执行 ``_get_daily_records_sync``。

        L1 判定全部在 sync 核心单点完成 — 不在 async 层做快速预检, 否则
        每次冷调用 get() 计 2 次 miss, 污染命中率指标; 命中走线程池的代价
        (~百µs) 可忽略, 也不再占独立闸门语义。返回值同 ``_get_daily_records_sync``。
        """
        async with self._reader_gate():
            return await self._run_blocking(
                self._get_daily_records_sync, code, adjust, start_date, end_date
            )

    async def get_daily(
        self, code: str, adjust: str = "none", start_date: str = "", end_date: str = ""
    ) -> ToolResult:
        """获取股票日线数据（离线，TDX 本地 .day 文件）

        Args:
            code: 股票代码（6 位数字，可带 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀）
            adjust: 复权方式
                - 'none': 不复权（默认）
                - 'qfq': 前复权
                - 'hfq': 后复权
                - 指数代码传 qfq/hfq 会被静默覆盖为 none（warn 字段说明）
            start_date: 起始日期 yyyy-MM-dd（可选）
            end_date: 结束日期 yyyy-MM-dd（可选）

        数据源：{tdxdir}/vipdoc/sh/lday/sh600036.day 等本地二进制文件

        2026-09-20 重构（移除 INDEX_MARKET_MAP 枚举表）:
            - 类型/市场由显式后缀 + 通达信前缀约定决定（见 index_registry）
            - 裸码 000xxx 歧义码在本入口默认按深市股票；读上证指数请显式
              传 ``sh000001``；非法后缀 / 非 6 位主体 → ``invalid_symbol``
            - 指数 .day 本地不存在 → 更明确的 no_data 文案
        """
        self._metrics["total_requests"] += 1

        # 日期归一化 (支持 YYYYMMDD 和 YYYY-MM-DD)
        start_date, end_date, date_error = _normalize_date_window(start_date, end_date)
        if date_error is not None:
            return self._invalid_param_result(date_error)

        try:
            # 2026-09-28 拉通: 缓存/取数/切窗全部收进 _get_daily_records
            # (与 get_daily_batch_local 同一核心); hit/miss 统一走
            # _build_daily_tool_result 组装, 消除旧 hit 分支手工拼 ToolResult
            # 与 miss 分支的重复。
            records, meta, _hit = await self._get_daily_records(
                code, adjust, start_date, end_date
            )
            return self._build_daily_tool_result(
                code=code, data=records, meta=meta, parsed_for_key=None,
            )
        except ValueError as e:
            # parse_symbol 抛: 后缀非法 / 主体非 6 位数字 / intent 非法
            self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
            return ToolResult(
                success=False,
                error=str(e),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_SYMBOL.value,
                    "message": str(e),
                    "recoverable": False,
                },
            )
        except Exception as e:
            logger.error(f"get_daily failed: {e}")
            return self._error_result(e, f"get_daily({code})")

    async def _get_daily_batch_one(
        self,
        code: str,
        adjust: str,
        start_date: str,
        end_date: str,
        limit: int,
    ) -> tuple[str | None, list[dict] | None, str | None, bool]:
        """批量内部: 取单只离线日线。复用 get_daily 同一取数核心 (含 L1)。

        Returns:
            (key, records, warning, is_structural_error)
            - key/records 为 None 表示该只失败 (warning 填原因)
            - is_structural_error=True 表示文件级错误 (OSError/struct.error),
              供上层累计 has_structural_errors
        """
        import struct as _struct
        from .index_registry import parse_symbol

        try:
            parsed = parse_symbol(code)
            key = f"{parsed.market}{parsed.code}" if parsed.market else parsed.code
            # 取数走与 get_daily 同一核心 (_get_daily_records); 外面套批量子闸门
            # (让出余量给单 symbol), 核心内部再过 reader_gate — 双闸门语义不变。
            # L1 判定在核心内单点完成, 命中与 miss 同路, 批层不自建缓存。
            async with self._batch_reader_semaphore:
                records, meta, _hit = await self._get_daily_records(
                    code, adjust, start_date, end_date
                )
            if limit and limit > 0 and len(records) > limit:
                records = records[-limit:]
            warn = meta.get("warn")
            return key, records, (f"{code}: {warn}" if warn else None), False
        except ValueError as e:
            self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
            return None, None, f"{code}: invalid_symbol - {e}", False
        except (OSError, _struct.error) as e:
            self._record_error(MooTDXErrorType.INTERNAL_ERROR.value)
            return None, None, f"{code}: file_error - {type(e).__name__}: {e}", True
        except Exception:
            # 未知异常上抛, 不吞进 warnings (与 _get_daily_batch_local_sync 契约一致)
            raise

    async def _get_daily_batch_async(
        self,
        code_parts: list[str],
        adjust: str,
        start_date: str,
        end_date: str,
        limit: int,
    ) -> tuple[dict[str, list[dict]], list[str], bool]:
        """async 逐 symbol 调度批量日线。reader_limit 约束生效, 全命中不出线程。

        与 _get_daily_batch_local_sync (保留供测试同步调用) 行为对齐:
        返回 (results, warnings, has_structural_errors)。
        """
        import asyncio

        outcomes = await asyncio.gather(
            *(
                self._get_daily_batch_one(c, adjust, start_date, end_date, limit)
                for c in code_parts
            ),
            return_exceptions=True,
        )

        results: dict[str, list[dict]] = {}
        warnings: list[str] = []
        has_structural_errors = False

        for code, outcome in zip(code_parts, outcomes):
            # gather(return_exceptions=True): _get_daily_batch_one 已捕获
            # ValueError / OSError / struct.error 转元组返回, 能以异常形式
            # 到这里的只有真正未知异常 —— 统一归为结构性失败标签 (旧行为更稳:
            # 不让单只未知异常炸掉整个批量)。
            if isinstance(outcome, BaseException):
                self._record_error(MooTDXErrorType.INTERNAL_ERROR.value)
                has_structural_errors = True
                warnings.append(
                    f"{code}: file_error - {type(outcome).__name__}: {outcome}"
                )
                continue

            key, records, warning, is_struct = outcome
            if warning:
                warnings.append(warning)
            if is_struct:
                has_structural_errors = True
            if key is not None and records is not None:
                results[key] = records

        return results, warnings, has_structural_errors

    async def get_daily_batch_local(
        self,
        codes: str,
        adjust: str = "none",
        start_date: str = "",
        end_date: str = "",
        limit: int = 0,
    ) -> ToolResult:
        """批量获取股票日线数据（离线，TDX 本地 .day 文件）

        2026-09-28 Round-3 修订:
          - H5-func: 新增 ``limit`` 参数, 单 symbol 最多返回根数
            (默认 0 = 不截断; ≤0 不抛错, 仅不截断)
          - C1: 全部结构性失败时返 ``success=False`` 而非静默 ``success=True data={}``

        Args:
            codes: 逗号分隔的股票代码，如 "sh600036,sh600519"（最多20只）
            adjust: 复权类型
                - 'none': 不复权（默认）
                - 'qfq': 前复权
                - 'hfq': 后复权
            start_date: 起始日期 yyyy-MM-dd（可选）
            end_date: 结束日期 yyyy-MM-dd（可选）
            limit: 每只股票最多返回根数 (默认 0 = 不截断; >0 截最近 N 条)

        Returns:
            ToolResult with data as dict {symbol: [records...]}
        """
        MAX_BATCH_CODES = 20

        self._metrics["total_requests"] += 1

        # 参数校验
        if not codes or not codes.strip():
            return self._invalid_param_result("codes 不能为空")

        code_parts = [p.strip() for p in str(codes).split(",") if p.strip()]
        if not code_parts:
            return self._invalid_param_result("codes 不能为空")

        if len(code_parts) > MAX_BATCH_CODES:
            return self._invalid_param_result(
                f"批量请求超过 {MAX_BATCH_CODES} 只股票限制"
            )

        # 校验每只代码格式（6位数字，可带前缀）
        for code in code_parts:
            bare = _strip_market_prefix(code)
            if not (len(bare) == 6 and bare.isdigit()):
                return self._invalid_symbol_result(
                    f"代码格式错误: {code!r} (需6位数字，可带 sh/sz/bj 前缀)"
                )

        # 校验 adjust 参数
        if adjust not in ("none", "qfq", "hfq"):
            return self._invalid_param_result(
                f"adjust 需为 none/qfq/hfq，当前值: {adjust!r}"
            )

        # 日期归一化 (与 get_daily_local 对齐)
        start_date, end_date, date_error = _normalize_date_window(start_date, end_date)
        if date_error is not None:
            return self._invalid_param_result(date_error)

        try:
            # 2026-09-28 性能审视 (H-perf):
            # 旧实现把整个 _get_daily_batch_local_sync 一次性丢进线程池,
            # 循环串行读 20 只 → 既无并行收益, 又绕过 async 层的 _reader_gate
            # (reader_limit 失控) 与单 symbol L1 (批层自写 L1 也违背
            # feedback_no_l1_on_batch_interfaces 裁决: 批入口自身不加 L1,
            # 应让底层单 symbol L1 去重)。
            #
            # 改为 async 逐 symbol 调度 + asyncio.gather: 每只走与
            # get_daily 相同的 _get_daily_records 核心 (L1 判定单点在核心内,
            # reader_gate + 单次读盘 + 写 L1), reader_limit 约束生效。
            results, warnings, has_structural_errors = await self._get_daily_batch_async(
                code_parts, adjust, start_date, end_date, limit
            )

            # C1: 全结构性失败 → success=False 而非 success=True data={}
            if has_structural_errors and not results:
                return ToolResult(
                    success=False,
                    error=f"批内全部失败: {'; '.join(warnings)}",
                    error_detail={
                        "error_type": MooTDXErrorType.INTERNAL_ERROR.value,
                        "message": "batch all structurally failed",
                        "warn": "; ".join(warnings),
                        "recoverable": False,
                    },
                )

            # 部分失败或全成功 → success=True, 失败进 warn
            result = ToolResult(success=True, data=results, source="mootdx2")

            if warnings:
                result.error_detail = {
                    "warn": "; ".join(warnings),
                    "recoverable": True,
                }

            return result

        except Exception as e:
            logger.error(f"get_daily_batch_local failed: {e}")
            return self._error_result(e, f"get_daily_batch_local({codes})")

    # ========== 离线分钟线（Reader，TDX 本地 .lc1/.lc5 文件） ==========
    def _get_minute_sync(
        self, code: str, suffix: str = "1", start_date: str = "", end_date: str = ""
    ) -> list:
        """同步读取离线分钟线数据。

        2026-09-28 Round-3 修订:
          - C2: parse_symbol ValueError 自然透传 (旧码吞掉转 no_data 是 contract violation,
            用户传 'abc' 应拿到 INVALID_PARAM 而非 NO_DATA).
          - H2-func: suffix 白名单 (旧码 suffix='2'/'0'/'' 静默映射 5 分钟线).
          - H3-func: 日期窗归一化 (YYYYMMDD 紧凑串 + 非法日历日 → ValueError).
            与 get_index_minute_local 对齐, 通过 _normalize_date_window 校验.

        Args:
            code: 6 位股票代码
            suffix: '1' = 1 分钟线（minline/.lc1）/ '5' = 5 分钟线（fzline/.lc5）
            start_date: 起始日期（可选，YYYY-MM-DD 或 YYYYMMDD）
            end_date: 结束日期（可选，同上）

        Raises:
            ValueError: suffix 非 '1'/'5'、code 解析失败、日期格式非法
        """
        # H2-func: suffix 白名单校验 (与 get_index_minute_local 一致)
        suffix_str = str(suffix)
        if suffix_str not in ("1", "5"):
            raise ValueError(
                f"suffix 仅支持 '1'/'5'，收到 {suffix!r}"
            )
        suffix_int = 1 if suffix_str == "1" else 5

        # H3-func: 日期窗归一化, 非法格式 / 不可解析 → ValueError
        start_date, end_date, date_error = _normalize_date_window(
            start_date, end_date,
        )
        if date_error is not None:
            raise ValueError(date_error)

        # C2: parse_symbol ValueError 自然透传
        sym = to_reader_symbol(parse_symbol(code))

        reader = self._get_reader()
        df = reader.minute(symbol=sym, suffix=suffix_int)
        if df is None or df.empty:
            return []

        # 用索引日期串按字典序过滤（与 _get_index_minute_sync 同法；pandas 2
        # 下 DatetimeIndex 与裸 str 直接 >=/<= 会静默得到空集）
        if start_date:
            df = df[df.index.strftime("%Y-%m-%d") >= start_date]
        if end_date and not getattr(df, "empty", True):
            df = df[df.index.strftime("%Y-%m-%d") <= end_date]

        records = self._records_from_indexed_df(df, out_key="datetime")
        return records

    async def get_minute(
        self, code: str, suffix: str = "1", start_date: str = "", end_date: str = ""
    ) -> ToolResult:
        """获取股票分钟线数据（离线，TDX 本地 .lc1/.lc5 文件）

        Args:
            code: 股票代码（6 位数字）
            suffix: '1' = 1 分钟线（默认）/ '5' = 5 分钟线
            start_date: 起始日期（可选）
            end_date: 结束日期（可选）

        数据源：{tdxdir}/vipdoc/sh/minline/sh600036.lc1
              或 {tdxdir}/vipdoc/sh/fzline/sh600036.lc5

        2026-09-28 Round-3 Batch 2b: 分钟线暂不做 L1 缓存 (用户决策: 只有日线
        数据需要 CACHE, 分钟线暂不需要)。每次直读 Reader, 无内存驻留。
        """
        self._metrics["total_requests"] += 1

        try:
            data = await self._run_blocking(
                self._get_minute_sync, code, suffix, start_date, end_date,
            )
            # 窗口切分已在 _get_minute_sync 内用归一化日期完成 (含 YYYYMMDD
            # 紧凑串); 此处不能再走 _filter_records_by_date —— 它只认 "date"
            # 字段, 分钟 records 是 "datetime", 二滤会把结果整体丢空。
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的{suffix}分钟数据")
        except Exception as e:
            logger.error(f"get_minute failed: {e}")
            return self._error_result(e, f"get_minute({code})")

    # ========== 离线 5 分钟K线（Reader.fzline，本地 .lc5 文件） ==========
    # 注意：fzline 是通达信目录名（5 分钟K线存 fzline/*.lc5），不是"分时
    # 图"。Reader.fzline() 等价于 Reader.minute(suffix=5)，返回跨多日的
    # 5 分钟 OHLC K线；当日实时分时请用在线 get_minute_data。
    def _get_fzline_sync(self, code: str) -> list:
        """同步读取离线 5 分钟K线数据。

        2026-09-28 Round-3 C2 修订:
          parse_symbol ValueError 自然透传 (旧码 try/except ValueError → []
          把 'abc' 等非法代码伪装成 no_data, 误导用户去查本地数据目录)。
          外层 ``get_fzline`` 的 ``except Exception`` 经 ``_error_result``
          正确归类为 INVALID_PARAM。

        Args:
            code: 6 位股票代码

        Raises:
            ValueError: code 解析失败
        """
        reader = self._get_reader()
        sym = to_reader_symbol(parse_symbol(code))
        df = reader.fzline(symbol=sym)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        if isinstance(df, bool):
            return []

        records = self._records_from_indexed_df(df, out_key="datetime")
        return records

    async def get_fzline(self, code: str) -> ToolResult:
        """获取股票 5 分钟K线数据（离线，TDX 本地 .lc5 文件）

        Args:
            code: 股票代码（6 位数字）

        数据源：{tdxdir}/vipdoc/sh/fzline/sh600036.lc5

        2026-09-28 Round-3 Batch 2b: 分钟线暂不做 L1 缓存 (用户决策)。
        """
        self._metrics["total_requests"] += 1

        try:
            data = await self._run_blocking(self._get_fzline_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的 5 分钟K线数据")
        except Exception as e:
            logger.error(f"get_fzline failed: {e}")
            return self._error_result(e, f"get_fzline({code})")

    # ========== 离线指数K线（Reader，TDX 本地 .day 文件） ==========
    # mootdx2 ``Reader`` 没有专门的 ``index()`` 方法，离线读指数本质上
    # 仍走 ``Reader.daily(symbol=...)``。指数工具入口以 intent='index'
    # 解析：显式后缀 + 前缀约定决定市场（裸 000xxx 歧义码按 sh 指数），
    # 强制无复权（指数无 XDXR），本地文件缺失给 no_data 提示，
    # limit 截断打 truncated 标记，分钟级不支持。
    _INDEX_LOCAL_TYPES: tuple[str, ...] = ("day", "week", "month")
    # Reader.daily 只产日线；week/month 在本地按规则重采样
    _INDEX_RESAMPLE_RULES: dict[str, str] = {"week": "W-FRI", "month": "ME"}

    def _parse_index_entry(
        self, code: str, *, stock_hint: str,
    ) -> tuple:
        """指数离线工具统一入口校验。

        3 个 *_local 指数入口（日K/分钟/5分钟）共用同一套消歧规则，
        防止守卫块复制漂移（kind 分支曾漏记 error_counts）。

        Args:
            stock_hint: 传入股票代码时的走错工具指引（如"股票分钟线请用
                get_minute_local"），拼在"不是指数"之后。

        Returns:
            ``(parsed, None)`` 或 ``(None, error ToolResult)``。
        """
        try:
            parsed = parse_symbol(code, intent=INDEX_KIND)
        except ValueError as e:
            self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
            return None, self._invalid_symbol_result(str(e))

        if parsed.kind != INDEX_KIND:
            self._record_error(MooTDXErrorType.INVALID_SYMBOL.value)
            return None, self._invalid_symbol_result(
                f"代码 {code} 不是指数（kind={parsed.kind}），{stock_hint}"
            )
        return parsed, None

    @staticmethod
    def _tail_with_warn(
        records: list, limit: int, *, scope_label: str = "文件内",
    ) -> tuple[list, str | None]:
        """超长离线分钟结果截最近 limit 条，返回 (records, warn)。

        Reader 一次性读全文件，截断只防超大 JSON 响应（SSE 1MiB 上限
        的老坑），不省本地解析。scope_label 标注 total 的口径：无日期窗
        时 total 是文件总数；带日期窗时 total 只是窗内条数，措辞必须区分。
        """
        if limit and len(records) > limit:
            total = len(records)
            return records[-limit:], (
                f"结果已截断为最近 {limit} 条（{scope_label}共 {total} 条），"
                f"可用 limit 参数调整（最大 {MAX_LIMIT_MINUTE} 条）"
            )
        return records, None

    def _validate_minute_limit(self, limit) -> ToolResult | None:
        """分钟线 limit 门控：正整数且 ≤ MAX_LIMIT_MINUTE。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return self._invalid_param_result(
                f"limit 需为正整数，收到 {limit!r}"
            )
        if limit > MAX_LIMIT_MINUTE:
            return self._invalid_param_result(
                DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_MINUTE)
            )
        return None

    def _get_index_kline_local_sync(
        self, code: str, limit: int, period: str = "day",
    ) -> tuple[list, dict]:
        """同步读取离线指数 K 线（强制 adjust='none'）。

        week/month 由日线本地重采样（Reader 只产日线）：
        open=first / high=max / low=min / close=last / vol,amount=sum；
        周线锚定周五（W-FRI），月线月末（ME）。

        Returns:
            ``(records, meta)``，meta 含 ``truncated`` 等字段。
        """
        parsed = parse_symbol(code, intent=INDEX_KIND)
        reader = self._get_reader()
        reader_symbol = to_reader_symbol(parsed)
        df = reader.daily(symbol=reader_symbol, adjust="none")
        meta: dict = {"kind": parsed.kind, "truncated": False}
        if df is None or getattr(df, "empty", True):
            return [], meta

        if period in self._INDEX_RESAMPLE_RULES:
            agg = {
                "open": "first", "high": "max", "low": "min", "close": "last",
                "vol": "sum", "amount": "sum",
            }
            cols = [c for c in agg if c in df.columns]
            df = (
                df[cols]
                .resample(self._INDEX_RESAMPLE_RULES[period])
                .agg({c: agg[c] for c in cols})
                .dropna(subset=["open"])
            )

        records = self._records_from_indexed_df(df)

        # 坑 #5 防御: limit 截断时显式标记 truncated
        if records and limit and limit > 0 and len(records) > limit:
            records = records[-limit:]
            meta["truncated"] = True
        return records, meta

    async def get_index_kline_local(
        self, code: str, type: str = "day", limit: int = 100,
    ) -> ToolResult:
        """离线指数 K 线（Reader.daily，强制无复权）。

        与在线 ``get_index_kline`` 的区别:
          - 不依赖 TDX 网络，走本地 .day 文件
          - ``type`` 支持 day/week/month（week/month 由本地日线重采样；
            分钟级请用 get_minute_local）
          - 本入口按指数意图解析（intent='index'）：裸 000xxx 歧义码按
            sh 指数；传入股票代码 → invalid_symbol

        Args:
            code: 指数代码，6 位数字或带 .SH/.SZ/.BJ 后缀
            type: K线类型，仅 day/week/month（默认 day）
            limit: 返回条数，默认 100，最大 1000

        Returns:
            ToolResult: 数据 / no_data / invalid_symbol / invalid_param / tdx_not_installed
        """
        # type 校验（坑 #7 防御）
        if type not in self._INDEX_LOCAL_TYPES:
            return ToolResult(
                success=False,
                error=(
                    f"get_index_kline_local 仅支持 "
                    f"{'/'.join(self._INDEX_LOCAL_TYPES)}，{type} 请用 get_minute_local"
                ),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": f"type={type} 不支持",
                    "recoverable": False,
                },
            )
        if limit > MAX_LIMIT_KLINE:
            return ToolResult(
                success=False,
                error=DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                    "recoverable": False,
                },
            )
        # M-silent4 (Round-3 2026-09-28): limit 必须正整数, limit=0 旧逻辑
        # 静默走"不截断"返回全集, 与用户期望 0 条矛盾; 与 _validate_minute_limit 对齐
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return self._invalid_param_result(
                f"limit 需为正整数，收到 {limit!r}"
            )

        self._metrics["total_requests"] += 1
        # 入口解析（指数意图）: 非法后缀 / 非 6 位主体 → invalid_symbol；
        # kind=stock 走错工具: 明确告知用户
        parsed, guard_error = self._parse_index_entry(
            code, stock_hint="请用 get_daily_local / get_kline",
        )
        if guard_error is not None:
            return guard_error

        # 2026-09-28 Round-3 Batch 2b H1-perf: L1 缓存
        # Round-4 Batch 1 H1: cache_key 用 parse_symbol 归一化, 'sh000001' 与
        # '000001' 同 symbol 共享缓存 (消除等效调用不同 key 的浪费)
        # limit 不入键 — 同一指数同一频率, limit 越大裁得越少, cache 只存
        # 全量 records, 命中后按 limit 现切窗 (与 get_daily_local 同策略)。
        cache_key = (to_reader_symbol(parsed), type)

        # 1. L1 命中 (records 全量, 现按 limit 切窗)
        # Round-4 Batch 1 CR2: list 复制防下游污染, 与 get_index_minute_local
        # / get_index_fzline_local 一致 (此 wrapper 原实现直接返回缓存原引用)
        cached_records = self._l1_index_kline.get(cache_key)
        if cached_records is not None:
            sliced = (
                list(cached_records[-limit:]) if limit and len(cached_records) > limit
                else list(cached_records)
            )
            result = ToolResult(success=True, data=sliced, source="mootdx2")
            if limit and len(cached_records) > limit:
                result.error_detail = {
                    "warn": f"结果已截断为最近 {len(sliced)} 条（缓存全量 {len(cached_records)} 条，请求 {limit} 条）",
                    "returned": len(sliced),
                    "requested": limit,
                    "recoverable": True,
                }
            return result

        # 2. miss
        try:
            records, meta = await self._run_blocking(
                self._get_index_kline_local_sync, code, limit, type,
            )
            if records:
                # Round-4 Batch 1 M1: 写入前 list 复制, 与 index_minute 防御一致
                self._l1_index_kline.put(cache_key, list(records))
                result = ToolResult(success=True, data=records, source="mootdx2")
                if meta.get("truncated"):
                    # 坑 #5 防御: offset 截断时显式告警（warn 会透传到 HTTP 信封顶层）
                    result.error_detail = {
                        "warn": f"结果已截断为最近 {len(records)} 条（请求 {limit} 条）",
                        "returned": len(records),
                        "requested": limit,
                        "recoverable": True,
                    }
                return result
            # 坑 #4 防御: 本地 .day 缺失给明确 no_data 文案
            return self._no_data_result(
                f"指数 {to_reader_symbol(parsed)} 本地无日线文件"
                f"（vipdoc/{parsed.market}/lday/{to_reader_symbol(parsed)}.day "
                f"不存在，可能该标的本地数据未下载）"
            )
        except Exception as e:
            logger.error(f"get_index_kline_local failed: {e}")
            return self._error_result(e, f"get_index_kline_local({code})")

    # ========== 离线指数分钟线（Reader.minute，本地 .lc1/.lc5） ==========
    # 与 get_index_kline_local 同一套入口约定（_parse_index_entry 守卫）：
    # intent='index' 解析（裸 000xxx 按 sh 指数）、股票代码走错入口给
    # invalid_symbol。suffix=1 → minline/.lc1，5 → fzline/.lc5。
    # 默认截最近 DEFAULT_LIMIT_MINUTE 根，防多年文件打出超大 JSON。
    _INDEX_MINUTE_SUBDIR: dict[int, tuple[str, str]] = {
        1: ("minline", "lc1"),
        5: ("fzline", "lc5"),
    }

    @staticmethod
    def _minute_records(df) -> list:
        """分钟 DataFrame → records，DatetimeIndex 统一抽成字符串 ``datetime``。

        Reader 返回的 df 自带字符串 ``datetime`` 列、索引名为 ``date``；
        reset_index 后会混进不可 JSON 序列化的 Timestamp，这里以索引为准
        覆盖（值与自带列一致），并移除索引列。
        """
        records = df.reset_index().to_dict(orient="records")
        for r in records:
            idx = r.pop("date", None)
            if idx is None:
                idx = r.pop("index", None)
            # Reader 自带字符串 datetime 列（"YYYY-MM-DD HH:MM"）时保留；
            # mock/无该列时按同一格式从索引生成，避免尾缀 ":00" 秒
            if not isinstance(r.get("datetime"), str):
                # pandas.Timestamp 自带 strftime，无需引入 pandas 依赖
                r["datetime"] = (
                    idx.strftime("%Y-%m-%d %H:%M") if idx is not None else None
                )
        return records

    def _get_index_minute_sync(
        self, code: str, suffix_int: int,
        start_date: str = "", end_date: str = "",
    ) -> tuple[list, bool]:
        """同步读离线指数分钟线。

        Returns:
            ``(records, had_rows)``；had_rows=过滤前文件确实读出 K 线，
            供入口区分"文件缺失/空文件"与"日期窗内无数据"两种 no_data。
        """
        parsed = parse_symbol(code, intent=INDEX_KIND)
        reader = self._get_reader()
        df = reader.minute(symbol=to_reader_symbol(parsed), suffix=suffix_int)
        if df is None or getattr(df, "empty", True):
            return [], False
        # 用索引的日期串按字典序过滤（pandas 2 下 DatetimeIndex 与裸 str
        # 比较不再隐式转换，直接 >= 会静默得到空集）；入参契约为 YYYY-MM-DD
        if start_date:
            df = df[df.index.strftime("%Y-%m-%d") >= start_date]
        if end_date and not getattr(df, "empty", True):
            df = df[df.index.strftime("%Y-%m-%d") <= end_date]
        if getattr(df, "empty", True):
            return [], True
        return self._minute_records(df), True

    def _index_minute_file_message(self, parsed, suffix_int: int) -> str:
        """文件缺失/空文件的 no_data 文案（Reader 对两种情况都返回空 df，
        无法区分，故措辞为"不存在或为空"，不谎称一定不存在）。"""
        subdir, ext = self._INDEX_MINUTE_SUBDIR[suffix_int]
        sym = to_reader_symbol(parsed)
        return (
            f"指数 {sym} 本地无 {suffix_int} 分钟线数据"
            f"（vipdoc/{parsed.market}/{subdir}/{sym}.{ext} "
            f"不存在或为空，可能该标的本地分钟数据未下载）"
        )

    async def get_index_minute_local(
        self, code: str, suffix: str = "1",
        start_date: str = "", end_date: str = "",
        limit: int = DEFAULT_LIMIT_MINUTE,
    ) -> ToolResult:
        """离线指数分钟线（Reader.minute，本地 .lc1/.lc5 文件）。

        入口按 ``intent='index'`` 解析：裸 000xxx 按 sh 指数；股票代码
        （如 600000 / sz000001）→ invalid_symbol 并指引 get_minute_local。

        Args:
            code: 指数代码，6 位数字或带 sh/sz/bj 前缀 / .SH/.SZ/.BJ 后缀
            suffix: '1' = 1 分钟线（默认，minline/.lc1）/ '5' = 5 分钟线
                    （fzline/.lc5）；JSON 整数 1/5 亦可
            start_date: 起始日期（可选，YYYY-MM-DD，兼容 YYYYMMDD，含当日）
            end_date: 结束日期（可选，同上，含当日全天）
            limit: 返回最近 N 条，默认 1000，最大 20000；超限截断并在
                   信封 warn 说明
        """
        if str(suffix) not in ("1", "5"):
            return self._invalid_param_result(
                f"get_index_minute_local suffix 仅支持 '1'/'5'，收到 {suffix!r}"
            )
        limit_error = self._validate_minute_limit(limit)
        if limit_error is not None:
            return limit_error
        start_date, end_date, date_error = _normalize_date_window(
            start_date, end_date,
        )
        if date_error is not None:
            return self._invalid_param_result(date_error)

        suffix_int = 1 if str(suffix) == "1" else 5
        self._metrics["total_requests"] += 1
        parsed, guard_error = self._parse_index_entry(
            code, stock_hint="股票分钟线请用 get_minute_local",
        )
        if guard_error is not None:
            return guard_error

        # 2026-09-28 性能审视: 分钟线暂不做 L1 缓存 (用户决策), 直读 Reader。
        try:
            records, had_rows = await self._run_blocking(
                self._get_index_minute_sync,
                code, suffix_int, start_date, end_date,
            )
            if records:
                scope_label = "所选日期范围内" if (start_date or end_date) else "文件内"
                records, warn_text = self._tail_with_warn(
                    records, limit, scope_label=scope_label,
                )
                result = ToolResult(success=True, data=records, source="mootdx2")
                if warn_text is not None:
                    result.error_detail = {
                        "warn": warn_text,
                        "returned": len(records),
                        "requested": limit,
                        "recoverable": True,
                    }
                return result
            if had_rows:
                # 文件有数据但日期窗切空：不能沿用"文件不存在"文案误导
                sym = to_reader_symbol(parsed)
                return self._no_data_result(
                    f"指数 {sym} 本地{suffix_int}分钟文件存在，"
                    f"但 {start_date or '最早'}~{end_date or '最新'} "
                    f"日期范围内无数据（请检查/放宽 start_date、end_date）"
                )
            return self._no_data_result(
                self._index_minute_file_message(parsed, suffix_int)
            )
        except Exception as e:
            logger.error(f"get_index_minute_local failed: {e}")
            return self._error_result(e, f"get_index_minute_local({code})")

    # ========== 离线指数 5 分钟线（Reader.fzline，本地 .lc5） ==========
    def _get_index_fzline_sync(self, code: str) -> list:
        parsed = parse_symbol(code, intent=INDEX_KIND)
        reader = self._get_reader()
        df = reader.fzline(symbol=to_reader_symbol(parsed))
        if df is None or isinstance(df, bool) or getattr(df, "empty", True):
            return []
        return self._minute_records(df)

    async def get_index_fzline_local(
        self, code: str, limit: int = DEFAULT_LIMIT_MINUTE,
    ) -> ToolResult:
        """离线指数 5 分钟线（Reader.fzline，本地 fzline/.lc5 文件）。

        等价于 ``get_index_minute_local(code, suffix='5')`` 的独立入口；
        股票代码 → invalid_symbol 并指引 get_fzline_local。

        Args:
            code: 指数代码（前缀/歧义码约定同 get_index_minute_local）
            limit: 返回最近 N 条，默认 1000，最大 20000；超限截断带 warn
        """
        limit_error = self._validate_minute_limit(limit)
        if limit_error is not None:
            return limit_error

        self._metrics["total_requests"] += 1
        parsed, guard_error = self._parse_index_entry(
            code, stock_hint="股票 5 分钟线请用 get_fzline_local",
        )
        if guard_error is not None:
            return guard_error

        # 2026-09-28 性能审视: 分钟线暂不做 L1 缓存 (用户决策), 直读 Reader。
        try:
            records = await self._run_blocking(self._get_index_fzline_sync, code)
            if records:
                records, warn_text = self._tail_with_warn(records, limit)
                result = ToolResult(success=True, data=records, source="mootdx2")
                if warn_text is not None:
                    result.error_detail = {
                        "warn": warn_text,
                        "returned": len(records),
                        "requested": limit,
                        "recoverable": True,
                    }
                return result
            return self._no_data_result(
                self._index_minute_file_message(parsed, 5)
            )
        except Exception as e:
            logger.error(f"get_index_fzline_local failed: {e}")
            return self._error_result(e, f"get_index_fzline_local({code})")

# ========== 指数K线（指定起止） ==========
    def _get_index_bars_sync(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800):
        """同步指数K线（指定起止位置）"""
        q = self._get_quotes()
        df = q.index_bars(symbol=symbol, frequency=frequency, start=start, offset=offset)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_index_bars(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800) -> ToolResult:
        """获取指数K线（指定起止位置）

        Args:
            symbol: 指数代码，如 '000001'（上证指数）
            frequency: K线频率，默认9（日线），其他：5（周线）、6（月线）
            start: 起始位置
            offset: 偏移量
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_index_bars_sync, symbol, frequency, start, offset)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_bars failed: {e}")
            return self._error_result(e, f"get_index_bars({symbol})")

    # ========== 除权除息数据 ==========
    def _get_xdxr_sync(self, symbol: str):
        """同步除权除息数据"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.xdxr(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_xdxr(self, symbol: str) -> ToolResult:
        """获取除权除息数据

        Args:
            symbol: 股票代码
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_xdxr_sync, symbol)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_xdxr failed: {e}")
            return self._error_result(e, f"get_xdxr({symbol})")

    # ========== 全部股票列表 ==========
    def _get_stock_all_sync(self):
        """同步全部股票列表"""
        q = self._get_quotes()
        df = q.stock_all()
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_stock_all(self) -> ToolResult:
        """获取全部股票列表"""
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_stock_all_sync)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_stock_all failed: {e}")
            return self._error_result(e, "get_stock_all")

    # ========== K线数据（指定日期范围） ==========
    def _get_k_data_sync(self, code: str, start_date: str, end_date: str):
        """同步K线数据（指定日期范围）"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.get_k_data(code=sym, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_k_data(self, code: str, start_date: str = "", end_date: str = "") -> ToolResult:
        """获取K线数据（指定日期范围）

        Args:
            code: 股票代码
            start_date: 开始日期，格式 YYYYMMDD 或 YYYY-MM-DD
            end_date: 结束日期，格式 YYYYMMDD 或 YYYY-MM-DD
        """
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_k_data_sync, code, start_date, end_date)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_k_data failed: {e}")
            return self._error_result(e, f"get_k_data({code})")

    # ========== 搜索股票 ==========
    def _search_stock_sync(self, keyword: str):
        """同步搜索股票（优先本地 block 文件，fallback 到 SQLite）

        V7.73-64 注意事项：mootdx2 `reader.block()` 接口在新版通达信已废弃
        （官方 dat 文件迁到 infoharbor_block.dat / spblock.dat，mootdx2 未适配)。
        当 block 为空时，fallback 到 SQLite (board.db) 搜索。

        2026-09-28 Round-3 H1-silent 修订:
          - 双源 (tdxdir + board.db) 都不可用 → 抛 ``_SearchDataUnavailable``
            让 async 入口显式包装为 no_data (旧码静默 success=True data=[])。
          - ``reader.block()`` 失败 ``logger.debug`` → ``logger.warning``
            (运维可见, 不依赖默认 INFO 级别以下被吞)。
        """
        # H1-silent 前置守卫: 双源不可用显式抛错, 不再静默
        has_tdx = bool(self._get_tdxdir())
        db_path = resolve_project_path(None, "data/board.db")
        has_db = db_path.exists()
        if not has_tdx and not has_db:
            raise _SearchDataUnavailable(
                f"search_stock: tdxdir 与 board.db 均不可用"
                f" (tdxdir={has_tdx}, board.db={db_path})"
            )

        # 优先尝试本地 block 文件
        try:
            reader = self._get_reader()
            if has_tdx:
                df_block = reader.block()
                if df_block is not None and len(df_block) > 0:
                    kw = keyword.lower()
                    # 2026-09-15 P0 (MOOTDX2 性能审视 M3): 改用 pandas
                    # str.contains 向量化代替 iterrows, 5000 行从 ~1s 降到
                    # ~5ms。name 也走 str.contains, case=False 覆盖原
                    # name.lower() 比对。
                    code_col = df_block["code"].astype(str)
                    name_col = df_block["name"].astype(str) if "name" in df_block.columns else None
                    mask = code_col.str.contains(kw, case=False, na=False)
                    if name_col is not None:
                        mask = mask | name_col.str.contains(kw, case=False, na=False)
                    matches = df_block[mask].head(20)
                    result = []
                    for code, name in zip(
                        code_col[matches.index].tolist(),
                        (name_col[matches.index].tolist() if name_col is not None else [None] * len(matches)),
                    ):
                        if not code:
                            continue
                        code = str(code)
                        # FLAT block 行为 7 位 "市场位+6 位裸码", 归一化成裸码
                        bare = code[1:] if len(code) == 7 and code[0].isdigit() else code
                        result.append({
                            "code": bare,
                            "market": _classify_search_code(code),
                            "name": name,
                        })
                    return result
        except Exception as e:
            # H1-silent: debug → warning, 运维不再依赖 INFO 级别以下被吞
            logger.warning(f"search_stock reader.block() failed: {e}")

        # Fallback: 从 SQLite (board.db) 搜索
        return self._search_stock_from_sqlite(keyword)

    def _search_stock_from_sqlite(self, keyword: str) -> list[dict]:
        """从 board.db.sector_stocks 搜索股票 (名称/代码 LIKE, 无拼音列)。"""
        import sqlite3

        # 2026-09-19 M4: 锚定项目根, 旧码 Path("data/board.db") 随 CWD
        # 漂移, 非项目根启动时永远搜不到。
        db_path = resolve_project_path(None, "data/board.db")
        if not db_path.exists():
            logger.warning(f"search_stock: board.db not found: {db_path}")
            return []

        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            kw = f"%{keyword}%"
            cursor = conn.execute("""
                SELECT DISTINCT stock_code, stock_name
                FROM sector_stocks
                WHERE stock_name LIKE ? OR stock_code LIKE ?
                LIMIT 20
            """, (kw, kw))
            result = []
            for stock_code, stock_name in cursor.fetchall():
                result.append({
                    "code": stock_code,
                    # 共用分类器: 6 位裸码, 覆盖 920 北交所新号段
                    "market": _classify_search_code(stock_code),
                    "name": stock_name,
                })
            return result
        except Exception as e:
            logger.warning(f"search_stock SQLite fallback failed: {e}")
            return []
        finally:
            # 查询异常也要释放连接 (旧码异常路径泄漏 conn)
            if conn is not None:
                conn.close()

    async def search_stock(self, keyword: str) -> ToolResult:
        """搜索股票（名称/代码模糊匹配; board.db 无拼音列, 不支持拼音）

        2026-09-28 Round-3 H1-silent: 双源不可用 → no_data 状态 (业务), 与
        单源无结果 (data=[]) 区分。
        2026-09-28 Round-3 Batch 2b H1-perf: L1 缓存 (key=(keyword.lower(),)),
        keyword 归一化小写, 同义大小写共享缓存。仅缓存"找到结果"分支, 不可用
        (双源失败) 与单源无结果不写 L1, 避免错误状态被锁在缓存里误导后续请求。

        Args:
            keyword: 搜索关键词（股票名称或代码）
        """
        self._metrics["total_requests"] += 1
        # H5: 空白 keyword 入口校验, 返 no_data 不污染 L1
        if not keyword or not keyword.strip():
            return self._no_data_result("搜索关键字不能为空")
        # keyword.lower() 为 L1 key, 原 keyword 用于 error 文案保持用户入参
        cache_key = (keyword.lower(),)

        # 1. L1 命中: 命中即成功, 现包 ToolResult (单源无结果分支不入 L1)
        cached_records = self._l1_search.get(cache_key)
        if cached_records is not None:
            return ToolResult(success=True, data=cached_records, source="mootdx2")

        try:
            data = await self._run_blocking(self._search_stock_sync, keyword)
            if data:
                # 仅缓存"找到结果"分支 (data 非空), 单源无结果与双源不可用
                # 均不写 L1 — 否则下次同 keyword 会拿到错误的"找到"响应
                self._l1_search.put(cache_key, data)
                return ToolResult(success=True, data=data, source="mootdx2")
            # 单源无结果 (keyword 不匹配), 与双源不可用区分
            return ToolResult(
                success=True,
                data=None,
                source="mootdx2",
                error=f"未找到匹配 {keyword!r}",
                error_detail={
                    "error_type": MooTDXErrorType.NO_DATA.value,
                    "message": f"未找到匹配 {keyword!r}",
                    "recoverable": True,
                },
            )
        except _SearchDataUnavailable as e:
            # 双源不可用显式化 (运维可基于 error_type=no_data 分流)
            return self._no_data_result(str(e))
        except Exception as e:
            logger.error(f"search_stock failed: {e}")
            return self._error_result(e, f"search_stock({keyword})")

    # ========== 聚合股票信息 ==========
    def _get_stock_info_sync(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100):
        """同步获取聚合股票信息（快照在线 + K线/分时离线）。

        2026-09-19 M3 修复:
        - 不再 try/except 吞掉所有异常伪装成功 —— 在线快照整体故障时
          调用方需要看到 internal_error, 而不是 quote=None 的空壳;
        - BJ 跳过在线快照: "bj" 前缀不被 tdxpy 识别 (错路由 SH), BJ 批次
          又会毒化混批, 离线 Reader K线/分时不受影响照常返回;
        - quotes 返 None (旧码 len(None) 崩) 与返空 DataFrame 等价处理。
        """
        bare = _strip_market_prefix(code)

        # 1. 实时快照（在线; BJ 无在线快照能力, 跳过）
        quote = None
        if market != "bj":
            _, _, token = _parse_quote_symbol(f"{market}{bare}")
            q = self._get_quotes()
            quote_df = q.quotes(symbol=[token])
            if quote_df is not None and len(quote_df) > 0:
                rec = _drop_internal_quote_columns(quote_df).iloc[0].to_dict()
                _annotate_quote_records([rec])
                quote = rec

        # 2. K线（离线 - Reader）
        reader = self._get_reader()
        daily_df = reader.daily(symbol=bare)
        kline_list = daily_df.tail(kline_count).to_dict(orient="records") if daily_df is not None and len(daily_df) > 0 else []

        # 3. 当日分时（离线 - Reader minute）
        minute_df = reader.minute(symbol=bare)
        minute_list = minute_df.to_dict(orient="records") if minute_df is not None and len(minute_df) > 0 else []

        return {
            "code": bare,
            "market": market,
            "quote": quote,
            "kline": kline_list,
            "minute": minute_list,
        }

    async def get_stock_info(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100) -> ToolResult:
        """获取聚合股票信息（快照 + K线 + 分时）

        Args:
            code: 股票代码
            market: 市场前缀 (sh/sz/bj; bj 仅返回离线 K线/分时)
            kline_period: K线周期 (day/week/month/minute1/5/15/30/60)
            kline_count: K线返回条数
        """
        if market not in ("sh", "sz", "bj"):
            return self._invalid_param_result(
                f"market 仅支持 sh/sz/bj, 收到 {market!r}"
            )
        self._metrics["total_requests"] += 1
        try:
            data = await self._run_blocking(self._get_stock_info_sync, code, market, kline_period, kline_count)
            if data.get("quote") is None and not data.get("kline") and not data.get("minute"):
                return self._no_data_result(f"股票 {market}{code} 无数据")
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_stock_info failed: {e}")
            return self._error_result(e, f"get_stock_info({market}{code})")

    # ========== ATR 指标 ==========
    def _indicator_atr_sync(self, code: str, type: str = "day", limit: int = 100) -> dict | None:
        """同步计算ATR指标; K 线为空返回 None (由 wrapper 转 no_data)。"""
        import pandas as pd
        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return None
        df = pd.DataFrame(kline)
        if df.empty or "close" not in df.columns:
            return None
        highs = df["high"].astype(float).tolist() if "high" in df.columns else df["close"].astype(float).tolist()
        lows = df["low"].astype(float).tolist() if "low" in df.columns else df["close"].astype(float).tolist()
        closes = df["close"].astype(float).tolist()
        dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]

        n = 14
        trs = []
        for i in range(len(closes)):
            if i == 0:
                trs.append(highs[0] - lows[0])
            else:
                hl = highs[i] - lows[i]
                hpc = abs(highs[i] - closes[i - 1])
                lpc = abs(lows[i] - closes[i - 1])
                trs.append(max(hl, hpc, lpc))

        atr = []
        for i in range(len(trs)):
            if i < n - 1:
                atr.append(None)
            elif i == n - 1:
                atr.append(round(sum(trs[:n]) / n, 3))
            else:
                atr.append(round((atr[-1] * (n - 1) + trs[i]) / n, 3))

        return {"atr": atr, "dates": dates}

    async def indicator_atr(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """计算ATR指标（平均真实波幅）

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000

        2026-09-28 Round-3 Batch 2b H1-perf: L1 缓存 (key=(code, type, limit)),
        单 symbol 接口走 records-only 缓存。仅缓存"成功计算"分支。
        """
        if not isinstance(limit, int) or not (1 <= limit <= MAX_LIMIT_KLINE):
            return self._invalid_param_result(
                f"limit 需为 1-{MAX_LIMIT_KLINE} 的整数, 收到 {limit!r}"
            )
        self._metrics["total_requests"] += 1
        # H3: cache_key 移除 limit, 避免 (code,type,limit) ∈ [1,1000] 基数爆炸
        # 旧: (code,type,limit) → maxsize 64 失效 (800万组合)
        # 新: (code,type), 命中后按 limit 切片 atr/dates 两列表
        cache_key = (code, type)

        # 1. L1 命中 (ATR dict 含 atr/dates 两个等长列表)
        cached_data = self._l1_indicator_atr.get(cache_key)
        if cached_data is not None:
            # 按 limit 切片: 保持 dict 结构, 只截取两列表
            sliced = {
                "atr": cached_data.get("atr", [])[-limit:],
                "dates": cached_data.get("dates", [])[-limit:],
            }
            return ToolResult(success=True, data=sliced, source="mootdx2")

        try:
            data = await self._run_blocking(self._indicator_atr_sync, code, type, limit)
            if not data:
                return self._no_data_result(f"股票 {code} 无 K 线数据, 无法计算 ATR")
            self._l1_indicator_atr.put(cache_key, data)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_atr failed: {e}")
            return self._error_result(e, f"indicator_atr({code})")

    # ========== 市场异动（主力监控精灵） ==========
    def _stock_unusual_sync(self, event_type: str = "all") -> list:
        """同步获取市场异动数据。

        H2 (2026-09-19): q.block() 固定拉系统 block.dat, tdxpy 以
        BlockReader FLAT 解析, 每行一个成分股: blockname / block_type /
        code_index / code (code 为 7 位 "市场位 + 6位代码")。旧代码找的
        code_list 列只存在于 GROUP 模式, FLAT 下永远缺失 → 恒返回 [];
        block_type 入参也会被 block(tofile, **kwargs) 静默吞掉。
        """
        import pandas as pd

        q = self._get_quotes()

        blocks_df = q.block()
        if blocks_df is None or blocks_df.empty or "code" not in blocks_df.columns:
            return []

        # 收集去重后的沪深成分股 (北交所标的跳过: quotes 批次含 market=2
        # 时 tdxpy 整批返回 None)。sorted 保证结果顺序稳定, 最多扫 500 只。
        code_to_market: dict[str, str] = {}
        for raw in blocks_df["code"].astype(str).tolist():
            bare = raw[-6:]
            if len(bare) != 6 or not bare.isdigit():
                continue
            market = _BLOCK_MARKET_CHAR_TO_PREFIX.get(raw[:1])
            if market in ("sh", "sz"):
                code_to_market.setdefault(bare, market)
        stock_list = sorted(code_to_market)[:500]
        if not stock_list:
            return []

        # Batch quote (带前缀直传, get_stock_markets 显式识别 sh/sz)
        results = []
        for i in range(0, len(stock_list), MAX_LIMIT_BATCH_QUOTE):
            chunk = stock_list[i:i + MAX_LIMIT_BATCH_QUOTE]
            symbols = [f"{code_to_market[c]}{c}" for c in chunk]
            batch_df = q.quotes(symbol=symbols)
            if batch_df is not None and len(batch_df) > 0:
                results.extend(
                    _drop_internal_quote_columns(batch_df).to_dict(orient="records")
                )

        if not results:
            return []

        # 报文无 close/pct_chg/symbol 列, 统一归一化
        _annotate_quote_records(results)
        df = pd.DataFrame(results)
        if df.empty or "pct_chg" not in df.columns:
            return []

        # 异动宇宙: |涨跌幅| >= 5% 才算异动, 所有事件过滤 (含 涨/跌) 都在
        # 宇宙内进行 — 否则 +0.5% 的票会以 "上涨异动" 名义漏出。
        df = df[df["pct_chg"].abs() >= 5]
        if df.empty:
            return []

        # Filter by event type
        if event_type == "涨":
            df = df[df["pct_chg"] > 0]
        elif event_type == "跌":
            df = df[df["pct_chg"] < 0]
        elif event_type == "放量":
            df = df[df["vol"] > df["vol"].quantile(0.75)] if "vol" in df.columns else df
        elif event_type == "缩量":
            df = df[df["vol"] < df["vol"].quantile(0.25)] if "vol" in df.columns else df
        elif event_type == "炸板":
            # 大涨 (>=5%) 但未封板 (<9%): 触板后回落的近似信号
            df = df[(df["pct_chg"] > 0) & (df["pct_chg"] < 9)]
        elif event_type == "天地板":
            df = df[(df["pct_chg"] <= -9.5) | (df["pct_chg"] >= 9.5)]
        # else: all → 整个异动宇宙

        # Build result with event label
        result = []
        for _, row in df.head(100).iterrows():
            sym = str(row.get("symbol", ""))
            if sym.startswith("sh"):
                mkt, code = "sh", sym[2:]
            elif sym.startswith("sz"):
                mkt, code = "sz", sym[2:]
            elif sym.startswith("bj"):
                mkt, code = "bj", sym[2:]
            else:
                mkt, code = "sz", sym
            pct = float(row.get("pct_chg", 0))
            # 事件标签与 event_type 过滤一致: 放量/缩量事件才标量能,
            # all/涨/跌 只标方向 (旧码无条件标 "放量上涨/放量下跌",
            # 缩量榜和 all 榜的标签都是错的)。涨停/跌停事实优先。
            if pct >= 9.9:
                event = "涨停"
            elif pct <= -9.9:
                event = "跌停"
            elif event_type in ("放量", "缩量"):
                event = f"{event_type}{'上涨' if pct > 0 else '下跌'}"
            else:
                event = "上涨" if pct > 0 else "下跌"
            result.append({
                "code": code,
                "market": mkt,
                "time": "",
                "event": event,
                "change_pct": pct,
            })
        return result

    def _stock_top_board_sync(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> list:
        """同步获取市场排行榜"""
        import pandas as pd

        VALID_SORT = {"change_pct", "amplitude", "turnover", "volume_ratio", "amount"}
        VALID_MARKET = {"all", "sh", "sz", "bj"}
        if sort_by not in VALID_SORT:
            sort_by = "change_pct"
        if market not in VALID_MARKET:
            market = "all"
        limit = min(limit, 200)

        # H1 (2026-09-19): mootdx2 没有 stock() 方法, 真实接口是
        # stocks(market=1=SH/0=SZ) (market=2 会抛 MootdxValidationException,
        # 故 bj 榜单无数据源 → 诚实地返回空)。列表混有基金/债券, 按 A 股
        # 前缀过滤后带交易所前缀直传 quotes, 不再靠数字猜市场。
        MARKET_PLAN = (
            (1, "sh", _A_SHARE_PREFIXES_SH),
            (0, "sz", _A_SHARE_PREFIXES_SZ),
        )
        q = self._get_quotes()
        all_quotes = []

        for market_id, exchange, stock_prefixes in MARKET_PLAN:
            df_stock = q.stocks(market=market_id)
            if df_stock is None or len(df_stock) == 0:
                continue
            codes = [
                str(r.get("code"))
                for r in df_stock.to_dict(orient="records")
                if str(r.get("code") or "").startswith(stock_prefixes)
            ]
            prefixed = [f"{exchange}{c}" for c in codes]
            for i in range(0, len(prefixed), MAX_LIMIT_BATCH_QUOTE):
                chunk = prefixed[i:i + MAX_LIMIT_BATCH_QUOTE]
                batch_df = q.quotes(symbol=chunk)
                if batch_df is not None and len(batch_df) > 0:
                    all_quotes.extend(
                        _drop_internal_quote_columns(batch_df).to_dict(orient="records")
                    )

        if not all_quotes:
            return []

        # 报文无 symbol/close/pct_chg 列, 统一归一化
        _annotate_quote_records(all_quotes)
        df_quotes = pd.DataFrame(all_quotes)
        if df_quotes.empty:
            return []

        if market != "all":
            df_quotes = df_quotes[df_quotes["symbol"].str.startswith(market)]

        # 可排序列: change_pct(报文列名 pct_chg)/amplitude/amount 网关算得出;
        # turnover(换手率)/volume_ratio(量比) 不在行情报文里, wrapper 负责
        # 给 warn, 这里统一回落 pct_chg。
        sort_column_map = {"change_pct": "pct_chg", "amplitude": "amplitude", "amount": "amount"}
        sort_col = sort_column_map.get(sort_by, "pct_chg")
        if sort_col not in df_quotes.columns:
            sort_col = "pct_chg"
        ascending = direction == "asc"
        df_quotes = df_quotes.sort_values(sort_col, ascending=ascending).head(limit)

        result = []
        for _, row in df_quotes.iterrows():
            sym = str(row.get("symbol", ""))
            if sym.startswith("sh"):
                mkt, code = "sh", sym[2:]
            elif sym.startswith("sz"):
                mkt, code = "sz", sym[2:]
            elif sym.startswith("bj"):
                mkt, code = "bj", sym[2:]
            else:
                mkt, code = "sz", sym
            result.append({
                "code": code,
                "market": mkt,
                "close": float(row.get("close", 0)),
                "change_pct": float(row.get("pct_chg", 0)),
                "volume": float(row.get("vol", 0)),
                "amount": float(row.get("amount", 0)),
                # 报文无换手率/量比, 保留合同键但诚实置 0
                "turnover": 0.0,
                "volume_ratio": 0.0,
            })
        return result

    _TOP_BOARD_SORT_UNAVAILABLE = {
        "turnover": "换手率",
        "volume_ratio": "量比",
    }
    _TOP_BOARD_SORT_VALID = {
        "change_pct", "amplitude", "amount", "turnover", "volume_ratio",
    }

    def _top_board_sort_warn(self, sort_by: str) -> str | None:
        """sort_by 的诚实降级提示 (报文无换手率/量比; 未知字段回落涨跌幅)。"""
        if sort_by in self._TOP_BOARD_SORT_UNAVAILABLE:
            label = self._TOP_BOARD_SORT_UNAVAILABLE[sort_by]
            return (
                f"实时行情报文不含{label}数据, sort_by={sort_by} 已回落为按"
                "涨跌幅排序"
            )
        if sort_by not in self._TOP_BOARD_SORT_VALID:
            return f"不支持的排序字段 {sort_by!r}, 已回落为按涨跌幅排序"
        return None

    @staticmethod
    def _with_warn(result: ToolResult, warn: str | None) -> ToolResult:
        """成功结果附带 warn (返回新对象, 不改动 L1 缓存里的 ToolResult)。

        2026-09-28 Round-3 Batch 2b H4-silent: data 列表复制防下游修改污染
        L1 缓存里的同一引用, 同时确保每次响应是独立 ToolResult (避免跨请求
        error_detail/warn 字段互串)。
        """
        if not warn or not result.success:
            return result
        detail = dict(result.error_detail or {})
        detail["warn"] = warn
        # data None/非 list 直传, list 才浅复制防 L1 命中响应与下游共用引用
        # Round-4 Batch 1 CR1: data=None 时 list(None) 抛 TypeError 修复
        if result.data is None:
            safe_data = None
        elif isinstance(result.data, list):
            safe_data = list(result.data)
        else:
            safe_data = result.data  # dict/generator 等透传
        return ToolResult(
            success=True,
            data=safe_data,
            source=result.source,
            error=result.error,
            error_detail=detail,
            duration_ms=result.duration_ms,
        )

    async def stock_top_board(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> ToolResult:
        """获取市场排行榜

        2026-09-16: 移除 single-flight，改用 L1 缓存 + Reader 并发控制。
        行情 10s 内变化 < 0.5%, 用户感知不到差异。
        2026-09-19 LOW: bj 显式 no_data (旧实现静默空列表); 换手率/量比
        排序在 error_detail.warn 诚实说明降级。
        2026-09-28 Round-3 Batch 2b H4-silent: 改用 _L1Slot, 只缓存 records,
        ToolResult 每次现包, 杜绝跨请求引用共享与 stale error_detail。
        """
        self._metrics["total_requests"] += 1
        if market == "bj":
            # stocks(market=2) 库内即抛 MootdxValidationException, BJ 无榜数据源
            return self._no_data_result(
                "北交所(bj)无排行榜数据源 (stocks(market=2) 不支持), 榜单仅支持 all/sh/sz"
            )
        warn = self._top_board_sort_warn(sort_by)
        cache_key = (sort_by, direction, limit, market)

        # 1. L1 命中: 直接返回缓存里的 records, 现包 ToolResult
        cached_records = self._l1_top_board.get(cache_key)
        if cached_records is not None:
            return self._with_warn(
                ToolResult(success=True, data=cached_records, source="mootdx2"),
                warn,
            )

        # 2. 旁路 (cachetools 缺失 / 槽 disabled)
        if not self._l1_top_board.enabled:
            try:
                data = await self._run_blocking(
                    self._stock_top_board_sync, sort_by, direction, limit, market
                )
                return self._with_warn(
                    ToolResult(success=True, data=data, source="mootdx2"), warn
                )
            except Exception as e:
                logger.error(f"stock_top_board failed: {e}")
                return self._error_result(e, "stock_top_board")

        # 3. miss + Reader 闸门 + 写 records (而非整 ToolResult)
        async with self._reader_gate():
            try:
                data = await self._run_blocking(
                    self._stock_top_board_sync, sort_by, direction, limit, market
                )
                self._l1_top_board.put(cache_key, data or [])
                return self._with_warn(
                    ToolResult(success=True, data=data, source="mootdx2"), warn
                )
            except Exception as e:
                logger.error(f"stock_top_board failed: {e}")
                return self._error_result(e, "stock_top_board")

    async def stock_unusual(self, event_type: str = "all") -> ToolResult:
        """获取市场异动数据（主力监控精灵）

        2026-09-16: 移除 single-flight，改用 L1 缓存 + Reader 并发控制。
        2026-09-28 Round-3 Batch 2b H4-silent: 改用 _L1Slot, 只缓存 records,
        ToolResult 每次现包 (records-only)。
        """
        self._metrics["total_requests"] += 1
        cache_key = (event_type,)

        # 1. L1 命中: 现包 ToolResult (无 warn, 但保持每次响应独立)
        cached_records = self._l1_unusual.get(cache_key)
        if cached_records is not None:
            return ToolResult(success=True, data=cached_records, source="mootdx2")

        # 2. 旁路 (cachetools 缺失 / 槽 disabled)
        if not self._l1_unusual.enabled:
            try:
                data = await self._run_blocking(self._stock_unusual_sync, event_type)
                return ToolResult(success=True, data=data, source="mootdx2")
            except Exception as e:
                logger.error(f"stock_unusual failed: {e}")
                return self._error_result(e, "stock_unusual")

        # 3. miss + Reader 闸门 + 写 records (而非整 ToolResult)
        async with self._reader_gate():
            try:
                data = await self._run_blocking(self._stock_unusual_sync, event_type)
                self._l1_unusual.put(cache_key, data or [])
                return ToolResult(success=True, data=data, source="mootdx2")
            except Exception as e:
                logger.error(f"stock_unusual failed: {e}")
                return self._error_result(e, "stock_unusual")
