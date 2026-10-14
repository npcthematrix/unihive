"""Mootdx2 → TDX_QUANT transparent adapter for 行情类 tools.

6 tools covered. Writes the mapping/period/param conventions as code-level
constants — these are protocol-level and not user-editable.

Usage:
    adapter = Mootdx2ToTdxQuantAdapter()
    if adapter.supports(gateway_tool):
        tdx_tool, params, normalizer = adapter.adapt(gateway_tool, params)
        result = await tdx_client.call_tool(tdx_tool, params)
        if result.success:
            return normalizer(result.data), "tdx_quant"

Mapping caveat (2026-09-14 audit):
    Initial commit c83090b mapped `get_batch_quote` → `get_security_snapshot`
    and `get_minute_data` → `get_minute_time_data`. Neither method exists
    in `skills/SKILL.md` (tqcenter contract v1.0.12) or
    `config/tools_tdx_quant.yaml` (54 tools, generated from SKILL.md).
    SKILL.md only declares:
      - single-stock snapshot:  get_market_snapshot(stock_code: str)
      - K-line / minute:        get_market_data(stock_list, period, count)
                                with period ∈ {1m, 5m, 15m, 30m, 1h, 1d, 1w}
    Fix: reuse those two methods.

2026-09-21 实测 D:/new_tdx_mock/PYPlugins/user/tqcenter.py:
    get_market_snapshot(stock_code: str, field_list=[]) — 只收单只代码,
    没有多代码 stock_list 形态。因此 get_batch_quote 从代理映射中移除
    (旧映射每次调用必抛 TypeError 并刷两条 ERROR 再回退), 批量行情直接
    走路由链上的 mootdx2。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

ResponseNormalizer = Callable[[Any], Any]

# 600519.SH / 600519.sh (THS 后缀格式, tdx_quant 合同要求)
_CODE_SUFFIX_RE = re.compile(r"^(\d{6})\.(sh|sz|bj)$", re.IGNORECASE)
# sh600519 / SH600519 (TDX 前缀格式, registry Normalizer 的产物)
_CODE_PREFIX_RE = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)
_BARE_DIGITS_RE = re.compile(r"^\d{6}$")
# 裸 6 位代码的号段 → 交易所 (best-effort; 权威路径是代码自带的前缀/后缀)。
# 3 位号段排在前面优先匹配 (200=SZ B股 vs 204=SH 逆回购 前两位冲突)。
# 覆盖 core.normalizer.Normalizer 首字启发式之外的基金/可转债/B股/北交 920 号段。
_BARE_EXCHANGE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("204", "SH"),  # 沪国债逆回购 (204001 等)
    ("131", "SZ"),  # 深国债逆回购 (131810 等)
    ("200", "SZ"),  # 深 B 股 (200002 等)
    # SH: 主板 60 / 科创板 68 / B股 90 / ETF·基金 50·51·52·56·58 / 可转债 11
    ("60", "SH"), ("68", "SH"), ("90", "SH"),
    ("50", "SH"), ("51", "SH"), ("52", "SH"), ("56", "SH"), ("58", "SH"),
    ("11", "SH"),
    # SZ: 主板 00 / 创业板 30 / ETF·LOF 15·16·18 / 可转债 12·13
    ("00", "SZ"), ("30", "SZ"),
    ("15", "SZ"), ("16", "SZ"), ("18", "SZ"),
    ("12", "SZ"), ("13", "SZ"),
    # BJ: 老三板/北交 43·83·87·88 + 2024 年起新号段 920
    ("43", "BJ"), ("83", "BJ"), ("87", "BJ"), ("88", "BJ"), ("92", "BJ"),
)


class ProxyAdapterError(Exception):
    """Raised when adapter cannot translate mootdx2 → tdx_quant."""


def _has_exchange_marker(code: str) -> bool:
    """代码是否显式携带交易所信息 (sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀)。

    裸指数代码无法可靠判交易所：按 spec 裸 ``000001`` 是上证指数,
    裸码号段启发式却会判成 SZ, 代理只接带有显式交易所标记的指数代码。
    """
    return bool(_CODE_SUFFIX_RE.match(code) or _CODE_PREFIX_RE.match(code))


def _normalize_stock_code_for_tdxquant(code: str | None) -> str | None:
    """将 mootdx2/TDX 格式的股票代码转换为 tdx_quant 需要的 THS 后缀格式。

    接受 (adapter 边界两种格式都会出现 — registry Normalizer 把裸代码转成
    ``sh600519``, 客户端直传的 ``600519.SH`` 则原样放行):

    - ``600519.SH`` / ``600519.sh`` → ``600519.SH`` (幂等, 后缀大小写归一)
    - ``sh600519`` / ``SH600519``    → ``600519.SH``
    - ``600519`` (裸 6 位)            → ``600519.SH`` (按真实号段推断)

    前缀/后缀是交易所的权威来源: ``sh000001`` 是上证指数 → ``000001.SH``,
    不会被裸代码启发式误判成 SZ。裸代码号段推断扩展 (而非等同)
    ``core.normalizer.Normalizer`` 的首字启发式, 额外覆盖 B 股/基金/可转债/
    逆回购/北交 920 号段; 空白处理与 Normalizer 一致 (内部空白一并剔除)。
    无法识别的格式原样返回, 不臆造后缀。
    """
    if not code:
        return code
    raw = re.sub(r"\s+", "", str(code))
    if not raw:
        return raw

    m = _CODE_SUFFIX_RE.match(raw)
    if m:
        digits, exchange = m.groups()
        return f"{digits}.{exchange.upper()}"

    m = _CODE_PREFIX_RE.match(raw)
    if m:
        exchange, digits = m.groups()
        return f"{digits}.{exchange.upper()}"

    if _BARE_DIGITS_RE.match(raw):
        for prefix, exchange in _BARE_EXCHANGE_PREFIXES:
            if raw.startswith(prefix):
                return f"{raw}.{exchange}"

    return raw


def _field_series(value: Any) -> Any:
    """从 tdx_quant 的字段值里取出单列序列 (DataFrame → 第 0 列; Series/dict 原样)。

    tqcenter ``get_market_data`` 返回 ``{字段: DataFrame}``: 每张表是
    DatetimeIndex × 股票列 (本次只查一只票 → 单列)。单列 DataFrame 与
    Series 都走 ``.items()`` 迭代 (时间, 值), 所以这里统一收敛成可迭代对象。
    """
    if value is None:
        return None
    if hasattr(value, "columns"):  # pandas DataFrame
        if not len(value.columns):
            return None
        return value.iloc[:, 0]
    if hasattr(value, "items"):  # pandas Series / dict
        return value
    return None


def _normalize_minute_records(payload: Any) -> Any:
    """tdx_quant ``get_market_data`` 的 ``{字段: DataFrame}`` → 分时记录列表。

    网关 ``get_minute_data`` 的对外合同是
    ``[{"time": "09:30:00", "price": 10.5, "volume": 1000}, ...]``
    (见 config/tools_mootdx2.yaml), 而 tqcenter 返回的是按字段分列的 DataFrame
    字典。这里逐分钟取最新价 (Close) 与该分钟成交量 (Volume), 时间只留
    HH:MM:SS —— 取数窗口已钉死为当日, 无需带日期。

    - 已是记录列表 → 幂等透传 (形状变了也不会二次转换);
    - 缺 Close 列 / 空字典 → ``[]``, 上层按 no_data 业务态处理;
    - Close 为 NaN 的点跳过 (tqcenter 用 NaN 填充无成交的分钟);
    - 成交量 ÷100: tqcenter 给「股」, 而分时的另一条源 (mootdx2/tdxpy 兜底)
      与日线口径都是「手」(2026-10-14 实测: TQ 1 分钟 1,063,000 == 本地
      .lc1 1,063,000 = 股; 全天合计必须与日线 436,815 手 对得上)。
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return payload

    fields = {str(key).lower(): value for key, value in payload.items()}
    prices = _field_series(fields.get("close"))
    if prices is None:
        return []

    volumes = _field_series(fields.get("volume"))
    volume_map: dict[Any, Any] = dict(volumes.items()) if volumes is not None else {}

    records = []
    for ts, price in prices.items():
        if price is None or price != price:  # NaN != NaN
            continue
        vol = volume_map.get(ts)
        records.append({
            "time": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts),
            "price": float(price),
            "volume": (
                None
                if vol is None or vol != vol
                else float(vol) / _TQ_VOL_SHARES_PER_LOT
            ),
        })
    return records


#: K 线字段别名: 网关合同字段 → tqcenter 可能给的列名 (小写比较)。
#: vol 优先取 tqcenter 的 Volume (mootdx2 的 to_data 也会补一个 volume 别名)。
_KLINE_FIELD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("open", ("open",)),
    ("high", ("high",)),
    ("low", ("low",)),
    ("close", ("close",)),
    ("vol", ("volume", "vol")),
    ("amount", ("amount",)),
)

#: tqcenter 与 mootdx2 的量纲差异 (2026-10-14 实测, 比值精确):
#:   - 成交量: tqcenter 一律「股」; mootdx2 日线及以上是「手」(比值 100:
#:     002273 43,681,520 vs 436,815 / 000001 99,493,688 vs 994,936 /
#:     600519 2,657,276 vs 26,572), 而分钟线是「股」(×1: 002273 9/18
#:     09:31 两边都是 1,063,000);
#:   - 成交额: tqcenter 是「万元」(×10000 得元: 91,615.2 vs 916,152,064),
#:     mootdx2 是「元」。
#: 网关内既有消费方 (以及 fuyao/快照) 都按 mootdx2 的日线口径 (手/元) 理解,
#: 故代理侧统一换算成 mootdx2 口径, 避免同一工具换源后数值差 100 倍。
_TQ_DAILY_PERIODS = frozenset({"1d", "1w", "1mon", "45d", "1q", "1y"})
_TQ_VOL_SHARES_PER_LOT = 100.0
_TQ_AMOUNT_WAN_PER_YUAN = 10000.0


def _normalize_kline_records(payload: Any, period: str = "") -> Any:
    """tdx_quant ``get_market_data`` 的 ``{字段: DataFrame}`` → K线记录列表。

    网关 K 线类工具 (get_kline / get_index_kline / get_index_all /
    get_index_bars) 的对外合同是
    ``[{"date", "open", "high", "low", "close", "volume"}, ...]``。
    tqcenter 返回按字段分列的 DataFrame 字典, 这里按时间索引逐根拼回记录。

    - 时间给 ``date`` (YYYY-MM-DD), 有完整时间戳时附 ``datetime``
      (分钟级 K 线需要它区分同一天的多根);
    - **量纲换算**: 成交量按周期换算成 mootdx2 口径 (日线及以上 ÷100 股→手,
      分钟线保持股), 成交额一律 ×10000 (万元→元); 详见 ``_TQ_DAILY_PERIODS``
      上方注释;
    - 同时补 ``year/month/day/hour/minute``, 与 mootdx2 在线 bars 路径的
      字段集对齐 (回落到 mootdx2 时字段不会变);
    - 缺 Close 列 / 空字典 → ``[]`` (上层按 no_data 业务态处理);
    - 任一根 close 为 NaN (无成交) 时跳过该根。
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return payload

    series_by_field = {
        str(key).lower(): _field_series(value) for key, value in payload.items()
    }
    close_series = series_by_field.get("close")
    if close_series is None:
        return []

    columns: dict[str, dict[Any, Any]] = {}
    for field, aliases in _KLINE_FIELD_ALIASES:
        source = next(
            (series_by_field[a] for a in aliases if series_by_field.get(a) is not None),
            None,
        )
        if source is not None:
            columns[field] = dict(source.items())

    vol_divisor = (
        _TQ_VOL_SHARES_PER_LOT if period in _TQ_DAILY_PERIODS else 1.0
    )
    records = []
    for ts, close in close_series.items():
        if close is None or close != close:  # NaN != NaN
            continue
        record: dict[str, Any] = {}
        if hasattr(ts, "strftime"):
            record["date"] = ts.strftime("%Y-%m-%d")
            record["datetime"] = ts.strftime("%Y-%m-%d %H:%M:%S")
            record["year"] = ts.year
            record["month"] = ts.month
            record["day"] = ts.day
            record["hour"] = ts.hour
            record["minute"] = ts.minute
        else:
            record["date"] = str(ts)[:10]
        for field, column in columns.items():
            value = column.get(ts)
            if value is None or value != value:
                continue
            if field == "vol":
                record[field] = float(value) / vol_divisor
            elif field == "amount":
                record[field] = float(value) * _TQ_AMOUNT_WAN_PER_YUAN
            else:
                record[field] = float(value)
        # mootdx2 的 to_data 由 vol 派生 volume 别名, 两条路径保持一致
        if "vol" in record:
            record.setdefault("volume", record["vol"])
        records.append(record)
    return records


def _kline_normalizer(period: str) -> ResponseNormalizer:
    """按周期绑定量纲换算的 K 线归一化器 (日线手 / 分钟线股)。"""
    return lambda payload: _normalize_kline_records(payload, period)


class Mootdx2ToTdxQuantAdapter:
    """Adapter that translates mootdx2 行情类 tool calls to tdx_quant equivalents.

    Mappings are HARDCODED — they reflect protocol-level contracts between
    mootdx2 and tdx_quant and are not user-configurable.
    """

    TOOL_MAP: dict[str, str] = {
        "get_quote": "get_market_snapshot",
        # get_batch_quote 不代理: tqcenter 快照仅支持单只 stock_code
        # (2026-09-21 实测), 批量需求直接走 mootdx2。
        "get_kline": "get_market_data",
        "get_index_kline": "get_market_data",
        "get_index_all": "get_market_data",
        "get_index_bars": "get_market_data",
        "get_minute_data": "get_market_data",       # period='1m', stock_list=[code]
    }

    PERIOD_MAP: dict[str, str] = {
        "day": "1d",
        "week": "1w",
        "month": "1mon",
        "minute1": "1m",
        "minute5": "5m",
        "minute15": "15m",
        "minute30": "30m",
        "minute60": "1h",
    }

    #: get_index_bars 的整数 frequency (9=日/5=周/6=月) → tqcenter period
    INDEX_FREQUENCY_MAP: dict[int, str] = {9: "1d", 5: "1w", 6: "1mon"}

    def supports(self, mootdx2_tool: str) -> bool:
        return mootdx2_tool in self.TOOL_MAP

    def adapt(
        self, mootdx2_tool: str, params: dict[str, Any]
    ) -> tuple[str, dict[str, Any], ResponseNormalizer]:
        """Translate mootdx2 call into tdx_quant call.

        Returns:
            (tdx_quant_tool_name, transformed_params, response_normalizer)

        Raises:
            ProxyAdapterError: tool not in TOOL_MAP
        """
        if not self.supports(mootdx2_tool):
            raise ProxyAdapterError(
                f"tool {mootdx2_tool!r} is not supported by proxy adapter"
            )

        tdx_tool = self.TOOL_MAP[mootdx2_tool]

        # get_market_snapshot is reused by both get_quote (single) and
        # get_batch_quote (multi-code via stock_list). Disambiguate by
        # source tool name, not just target.
        if tdx_tool == "get_market_snapshot":
            tdx_params = self._adapt_get_quote(params)
            normalizer = _identity
        elif tdx_tool == "get_market_data" and mootdx2_tool == "get_minute_data":
            tdx_params = self._adapt_get_minute_data_to_market_data(params)
            normalizer = _normalize_minute_records
        elif tdx_tool == "get_market_data" and mootdx2_tool == "get_index_bars":
            tdx_params = self._adapt_get_index_bars(params)
            normalizer = _kline_normalizer(tdx_params["period"])
        elif tdx_tool == "get_market_data":
            tdx_params = self._adapt_get_kline(mootdx2_tool, params)
            normalizer = _kline_normalizer(tdx_params["period"])
        else:
            raise ProxyAdapterError(
                f"internal: unknown (mootdx2_tool, tdx_tool)="
                f"({mootdx2_tool!r}, {tdx_tool!r})"
            )

        return tdx_tool, tdx_params, normalizer

    # --- private param transformers ---

    def _adapt_get_quote(self, params: dict) -> dict:
        """get_quote(code) → get_market_snapshot(stock_code)."""
        code = params["code"]
        stock_code = _normalize_stock_code_for_tdxquant(code)
        return {"stock_code": stock_code}

    def _adapt_get_kline(self, mootdx2_tool: str, params: dict) -> dict:
        """get_kline / get_index_kline / get_index_all → get_market_data.

        mootdx2_tool preserved for debugging — 参数约定随源工具不同:
        mootdx2 K线周期参数名是 ``type`` (不是 period), 根数参数 ``limit``
        缺省 100; count=0 会让 tqcenter 走 start/end 模式返回全历史。
        """
        is_index = mootdx2_tool in ("get_index_kline", "get_index_all")
        code = params.get("code") or params.get("codes", "")
        raw_codes = [c.strip() for c in str(code).split(",") if c.strip()]
        if is_index and not all(
            _has_exchange_marker(re.sub(r"\s+", "", c)) for c in raw_codes
        ):
            # 裸指数代码交给 mootdx2 链处理 (其 get_index 自行判交易所),
            # 避免裸 000001 被号段启发式误判成深市。
            raise ProxyAdapterError(
                f"proxy requires exchange-marked index code, got {code!r}"
            )
        stock_list = [_normalize_stock_code_for_tdxquant(c) for c in raw_codes]
        type_raw = params.get("type", "day")
        period = self.PERIOD_MAP.get(type_raw, type_raw)
        count = int(params.get("limit", 100) or 100)
        return {
            "stock_list": stock_list,
            "period": period,
            "count": count,
        }

    def _adapt_get_index_bars(self, params: dict) -> dict:
        """get_index_bars(symbol, frequency, start, offset) → get_market_data。

        - ``start`` 是位置偏移, tqcenter 的 count 只支持「最近 n 条」,
          指定 start>0 时不代理, 回落 mootdx2;
        - frequency: 9=日/5=周/6=月;
        - offset 缺省 800。
        """
        symbol = re.sub(r"\s+", "", str(params.get("symbol") or ""))
        if not symbol:
            raise ProxyAdapterError("get_index_bars requires symbol")
        if not _has_exchange_marker(symbol):
            raise ProxyAdapterError(
                f"proxy requires exchange-marked index symbol, got {symbol!r}"
            )
        if int(params.get("start", 0) or 0) > 0:
            raise ProxyAdapterError("get_index_bars start>0 unsupported by tqcenter")
        frequency = int(params.get("frequency", 9) or 9)
        period = self.INDEX_FREQUENCY_MAP.get(frequency)
        if period is None:
            raise ProxyAdapterError(f"unsupported index frequency {frequency}")
        count = int(params.get("offset", 800) or 800)
        return {
            "stock_list": [_normalize_stock_code_for_tdxquant(symbol)],
            "period": period,
            "count": count,
        }

    def _adapt_get_minute_data_to_market_data(self, params: dict) -> dict:
        """get_minute_data(code) → get_market_data(stock_list, period='1m', 当日窗口)。

        2026-10-13 修: 旧实现只传 ``count=240``, 那是「截止 end_time 的最近 240
        根 1 分钟K线」—— 盘前/盘中会跨到前一交易日, 拿到的并不是今日分时。
        本工具的语义是**今日分时**, 所以改用 start_time/end_time 钉死当天
        (``count<=0`` 才走区间模式), 当日无分钟数据时返回空 → 上层转 no_data
        业务态, 而不是悄悄给出别的日期的数据。

        tqcenter 的 ``_convert_time_format`` 按**字符串长度**判定格式, 只认
        ``YYYYMMDD`` / ``YYYYMMDDHHMMSS`` 两种, 传 "YYYY-MM-DD HH:MM:SS"
        会 ValueError, 故此处传紧凑串。
        """
        code = params["code"]
        stock_code = _normalize_stock_code_for_tdxquant(code)
        day = datetime.now().strftime("%Y%m%d")
        return {
            "stock_list": [stock_code],
            "period": "1m",
            "start_time": f"{day}000000",
            "end_time": f"{day}235959",
            "count": 0,  # <=0 → 区间模式 (count>0 是「最近 n 根」, 会跨日)
        }


def _identity(x: Any) -> Any:
    """Default normalizer — pass-through. Field rename can be added later
    if downstream consumers depend on mootdx2 field names like {close, open}
    vs tdx_quant's {Now, LastClose}. For now, identity is the safe default
    because the existing mootdx2 tool specs are the ones registered in
    tools_mootdx2.yaml — downstream consumers have already adapted to those
    field names. Switching to tdx_quant fields would break callers.
    """
    return x
