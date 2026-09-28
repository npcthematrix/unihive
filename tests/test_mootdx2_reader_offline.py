"""Reader-based offline tools tests — mock mootdx2.reader.Reader to avoid TDX file dependency."""
import asyncio
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


@pytest.fixture
def client():
    from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


# ========== get_daily ==========

def test_get_daily_tdxdir_empty(client):
    """tdxdir 目录不存在 → tdx_not_installed。

    显式指向不存在的目录而非依赖 settings=None + mootdx2 DEFAULT_TDXDIR
    (本机装过通达信时 C:/new_tdx 存在, 旧写法会退化为 no_data)。
    """
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "Z:/__no_such_tdx__"
    r = asyncio.run(client.get_daily("600036"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_not_installed"


def test_get_daily_returns_records(client):
    """Reader.daily returns DataFrame → list of {date, open, ...} records."""
    mock_df = pd.DataFrame({
        "open": [10.0, 10.5],
        "high": [10.8, 10.9],
        "low": [9.9, 10.3],
        "close": [10.6, 10.7],
        "vol": [1000.0, 2000.0],
    }, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"

    mock_reader = MagicMock()
    mock_reader.daily.return_value = mock_df

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_daily("600036", adjust="qfq"))

    assert r.success is True
    # reader.daily called with symbol='600036' (stripped of prefixes), adjust='qfq'
    assert mock_reader.daily.call_args.kwargs.get("adjust") == "qfq"
    assert mock_reader.daily.call_args.kwargs.get("symbol") == "600036"
    assert len(r.data) == 2
    assert r.data[0]["date"] == "2024-01-02"
    assert r.data[0]["close"] == 10.6


def test_get_daily_empty_result(client):
    """Reader.daily returns None → success=True, data=None（业务无数据，非错误）."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.daily.return_value = None

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_daily("600036"))

    assert r.success is True
    assert r.data is None
    assert "未找到" in r.error


def test_get_daily_date_filter(client):
    """start_date/end_date are passed but Reader doesn't filter → records still returned."""
    mock_df = pd.DataFrame(
        {"close": [10.0, 11.0, 12.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.daily.return_value = mock_df

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(
            client.get_daily("600036", start_date="2024-01-03", end_date="2024-01-04")
        )

    assert r.success is True
    assert len(r.data) == 2
    assert r.data[0]["date"] == "2024-01-03"
    assert r.data[1]["date"] == "2024-01-04"


# ========== get_daily_batch_local ==========

async def test_get_daily_batch_local_validates_limit(client):
    """测试超过20只返回invalid_param"""
    codes = ",".join([f"sh60000{i}" for i in range(25)])
    result = await client.get_daily_batch_local(codes=codes)
    assert result.success is False
    assert "20" in result.error


# ========== get_minute ==========

def test_get_minute_tdxdir_empty(client):
    """tdxdir 目录不存在 → tdx_not_installed (显式配置, 见上)。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "Z:/__no_such_tdx__"
    r = asyncio.run(client.get_minute("600036"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_not_installed"


def test_get_minute_suffix_1(client):
    """suffix='1' → Reader.minute(symbol, suffix=1)."""
    mock_df = pd.DataFrame(
        {"price": [10.5, 10.7], "vol": [100, 200]},
        index=pd.to_datetime(["2024-01-02 09:31", "2024-01-02 09:32"]),
    )
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.minute.return_value = mock_df

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_minute("600036", suffix="1"))

    assert r.success is True
    assert mock_reader.minute.call_args.kwargs.get("suffix") == 1
    assert r.data[0]["datetime"].startswith("2024-01-02")


def test_get_minute_suffix_5(client):
    """suffix='5' → Reader.minute(symbol, suffix=5)."""
    mock_df = pd.DataFrame({"price": [10.5]}, index=pd.to_datetime(["2024-01-02 09:35"]))
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.minute.return_value = mock_df

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_minute("600036", suffix="5"))

    assert r.success is True
    assert mock_reader.minute.call_args.kwargs.get("suffix") == 5


# ========== get_fzline ==========

def test_get_fzline_tdxdir_empty(client):
    """tdxdir 目录不存在 → tdx_not_installed (显式配置, 见上)。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "Z:/__no_such_tdx__"
    r = asyncio.run(client.get_fzline("600036"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_not_installed"


def test_get_fzline_returns_records(client):
    """Reader.fzline returns DataFrame → list of records."""
    mock_df = pd.DataFrame(
        {"price": [10.0, 10.5], "vol": [100, 200]},
        index=pd.to_datetime(["2024-01-02 09:30", "2024-01-02 09:35"]),
    )
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.fzline.return_value = mock_df

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_fzline("600036"))

    assert r.success is True
    assert mock_reader.fzline.call_args.kwargs.get("symbol") == "600036"
    assert len(r.data) == 2


def test_get_fzline_bool_result(client):
    """Reader.fzline sometimes returns False → no_data_result (don't crash on bool)."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.fzline.return_value = False

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_fzline("600036"))

    assert r.success is True
    assert r.data is None
    assert "未找到" in r.error


# ========== method_map 注册验证 ==========

def test_new_tools_registered_in_method_map(client):
    """call_tool 必须能路由到 3 个新离线接口。

    2026-09-19 LOW: 分发表从 call_tool 内联 dict 改为模块级
    ``_TOOL_METHOD_NAMES`` (避免每次调用重建 ~40 项 dict),
    断言改为查表 + 方法可解析。
    """
    from src.unihive.api.mootdx2_client import _TOOL_METHOD_NAMES

    expected = {"get_daily", "get_minute", "get_fzline"}
    for name in expected:
        assert name in _TOOL_METHOD_NAMES, f"{name} 未在 method_map 注册"
        assert callable(getattr(client, _TOOL_METHOD_NAMES[name], None))


# ========== YAML 配置验证 ==========

def test_yaml_has_offline_reader_tools():
    """3 个新工具都在 tools_mootdx2.yaml 中声明为 offline。"""
    import yaml
    from pathlib import Path

    with open("config/tools_mootdx2.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    by_name = {t["name"]: t for t in data["tools"]}

    assert by_name["get_daily_local"]["data_source_type"] == "offline"
    assert by_name["get_minute_local"]["data_source_type"] == "offline"
    assert by_name["get_fzline_local"]["data_source_type"] == "offline"

    # verify params
    daily_params = [p["name"] for p in by_name["get_daily_local"]["params"]]
    assert "code" in daily_params
    assert "adjust" in daily_params
    assert by_name["get_daily_local"]["params"][1]["enum"] == ["none", "qfq", "hfq"]

    minute_params = [p["name"] for p in by_name["get_minute_local"]["params"]]
    assert "suffix" in minute_params
    assert by_name["get_minute_local"]["params"][1]["enum"] == ["1", "5"]

# ========== get_index_kline_local：周/月重采样 + 歧义 warn ==========

def _daily_df(dates, close_start=10.0):
    """构造 Reader.daily 风格的 OHLCV DataFrame。"""
    n = len(dates)
    return pd.DataFrame({
        "open": [close_start + i for i in range(n)],
        "high": [close_start + i + 0.5 for i in range(n)],
        "low": [close_start + i - 0.5 for i in range(n)],
        "close": [close_start + i for i in range(n)],
        "vol": [1000.0] * n,
        "amount": [1e7] * n,
    }, index=pd.to_datetime(dates))


def test_index_kline_week_resample_aggregates_to_friday(client):
    """两周以上日线 → W-FRI 重采样，OHLCV 聚合正确，日期锚定周五。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    dates = pd.bdate_range("2026-09-01", periods=10)  # 9/1-9/4, 9/7-9/11, 9/14
    mock_reader = MagicMock()
    mock_reader.daily.return_value = _daily_df([str(d.date()) for d in dates])

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        records, meta = client._get_index_kline_local_sync("000001", 100, "week")

    assert mock_reader.daily.call_args.kwargs["symbol"] == "sh000001"
    assert mock_reader.daily.call_args.kwargs["adjust"] == "none"
    # W-FRI：周一 9/14 归入截至周五 9/18 的周桶
    assert [r["date"] for r in records] == ["2026-09-04", "2026-09-11", "2026-09-18"]
    # 第一周 4 个交易日：vol 求和 / high 取 max / close 取最后
    first = records[0]
    assert first["vol"] == 4000.0
    assert first["high"] == 13.5
    assert first["close"] == 13.0
    assert meta["truncated"] is False


def test_index_kline_month_resample_labels_month_end(client):
    """跨 3 个自然月的日线 → 月末标签（含当月未结束月，label 落在月末日期）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    dates = pd.bdate_range("2026-06-01", periods=65)
    mock_reader = MagicMock()
    mock_reader.daily.return_value = _daily_df([str(d.date()) for d in dates])

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        records, _ = client._get_index_kline_local_sync("399001", 100, "month")

    labels = [r["date"] for r in records]
    assert labels[0] == "2026-06-30"
    assert "2026-08-31" in labels
    assert all(set(r) == {"date", "open", "high", "low", "close", "vol", "amount"}
               for r in records)


def test_index_kline_limit_truncation_sets_meta(client):
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    dates = pd.bdate_range("2026-09-01", periods=10)
    mock_reader = MagicMock()
    mock_reader.daily.return_value = _daily_df([str(d.date()) for d in dates])

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        records, meta = client._get_index_kline_local_sync("000001", 3, "day")

    assert len(records) == 3
    assert meta["truncated"] is True
    assert [r["date"] for r in records] == ["2026-09-10", "2026-09-11", "2026-09-14"]


def test_index_kline_no_data_message_names_actual_file(client):
    """指数文件缺失时 no_data 文案给出实际查找的市场前缀路径（932000 → sh）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.daily.return_value = None

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_kline_local("932000", type="day", limit=100))

    assert r.success is True
    assert r.data is None
    assert "sh932000" in r.error and ".day" in r.error


# ========== get_daily 裸 000xxx 歧义码 warn ==========

def test_bare_000_ambiguous_code_carries_warn(client):
    """000001 按深市股票返回时必须带消歧 warn（防止静默拿到另一标的数据）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_df = pd.DataFrame(
        {"close": [11.7]}, index=pd.to_datetime(["2026-09-18"])
    )
    mock_reader = MagicMock()
    mock_reader.daily.return_value = mock_df

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_daily("000001"))

    assert r.success is True
    warn = r.error_detail["warn"]
    assert "歧义" in warn and "sh000001" in warn and "get_index_kline_local" in warn


def test_bare_000_warn_survives_l1_cache_hit(client):
    """第二次命中 L1 缓存时 warn 不能丢。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.daily.return_value = pd.DataFrame(
        {"close": [11.7]}, index=pd.to_datetime(["2026-09-18"])
    )
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r1 = asyncio.run(client.get_daily("000001"))
        r2 = asyncio.run(client.get_daily("000001"))
    assert client._daily_l1_hits == 1
    assert "歧义" in r1.error_detail["warn"]
    assert "歧义" in r2.error_detail["warn"]


def test_explicit_sz000001_has_no_ambiguity_warn(client):
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.daily.return_value = pd.DataFrame(
        {"close": [11.7]}, index=pd.to_datetime(["2026-09-18"])
    )
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_daily("sz000001"))
    assert r.success is True
    assert r.error_detail is None


def test_bare_000_no_data_message_points_to_index_entry(client):
    """裸 000xxx 在 sz 下无文件时，no_data 文案给出指数入口指引。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.daily.return_value = None
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_daily("000999"))
    assert r.success is True
    assert r.data is None
    assert "sh000999" in r.error and "get_index_kline_local" in r.error


def test_yaml_has_get_index_kline_local_offline():
    """get_index_kline_local 在 YAML 声明为 offline，type 仅 day/week/month。"""
    import yaml
    with open("config/tools_mootdx2.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    by_name = {t["name"]: t for t in data["tools"]}
    spec = by_name["get_index_kline_local"]
    assert spec["data_source_type"] == "offline"
    params = {p["name"]: p for p in spec["params"]}
    assert params["type"]["enum"] == ["day", "week", "month"]
    assert params["code"]["required"] is True


# ========== get_index_minute_local / get_index_fzline_local ==========

def _minute_df(rows):
    """构造 Reader.minute 风格 DataFrame；rows: [(datetime, close, vol, amount), ...]。"""
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "vol": [r[2] for r in rows],
            "amount": [r[3] for r in rows],
        },
        index=idx,
    )


def _real_minute_df(rows):
    """构造与 mootdx2 Reader 真实输出同形的分钟 DataFrame。

    真实 Reader：DatetimeIndex 名为 ``date``，自带原生字符串 ``datetime``
    列（"YYYY-MM-DD HH:MM"）以及 year/month/day/hour/minute 拆分列。
    rows: [(datetime, close, vol, amount), ...]
    """
    idx = pd.to_datetime([r[0] for r in rows])
    idx.name = "date"
    ts = list(idx)
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "vol": [r[2] for r in rows],
            "amount": [r[3] for r in rows],
            "year": [t.year for t in ts],
            "month": [t.month for t in ts],
            "day": [t.day for t in ts],
            "hour": [t.hour for t in ts],
            "minute": [t.minute for t in ts],
            "datetime": [t.strftime("%Y-%m-%d %H:%M") for t in ts],
        },
        index=idx,
    )


def _patch_reader(**dfs):
    """生成 mock_reader（dfs: minute=/daily=/fzline= 返回值）。"""
    mock_reader = MagicMock()
    mock_reader.minute.return_value = dfs.get("minute")
    mock_reader.daily.return_value = dfs.get("daily")
    mock_reader.fzline.return_value = dfs.get("fzline")
    return mock_reader


def test_index_minute_bare_000001_resolves_as_sh_index(client):
    """裸 000001 在指数入口按 sh 解析，走 .lc1（suffix=1）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-18 09:31", 3900.0, 100.0, 390000.0),
        ("2026-09-18 09:32", 3901.0, 200.0, 780200.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("000001"))

    assert r.success is True
    assert mock_reader.minute.call_args.kwargs["symbol"] == "sh000001"
    assert mock_reader.minute.call_args.kwargs["suffix"] == 1
    assert r.data[0]["datetime"].startswith("2026-09-18")


def test_index_minute_explicit_markets_sz_bj(client):
    """399001 → sz399001；bj899050 显式前缀保持 bj（补丁后路由）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-18 09:31", 100.0, 1.0, 100.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r_sz = asyncio.run(client.get_index_minute_local("sz399001"))
        r_bj = asyncio.run(client.get_index_minute_local("bj899050"))

    assert r_sz.success and r_bj.success
    assert mock_reader.minute.call_args_list[0].kwargs["symbol"] == "sz399001"
    assert mock_reader.minute.call_args_list[1].kwargs["symbol"] == "bj899050"


def test_index_minute_suffix_5_reads_lc5(client):
    """suffix='5' → Reader.minute(suffix=5)（vipdoc/fzline/*.lc5）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-18 09:35", 3900.0, 500.0, 1_950_000.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("000001", suffix="5"))

    assert r.success is True
    assert mock_reader.minute.call_args.kwargs["suffix"] == 5


def test_index_minute_invalid_suffix_rejected(client):
    """suffix 仅 1/5，其余 → invalid_param（不静默落 5）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_index_minute_local("000001", suffix="15"))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_param"


def test_index_minute_rejects_stock_codes(client):
    """股票代码走指数入口 → invalid_symbol 并指引 get_minute_local。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    for code in ("600000", "sz000001"):
        r = asyncio.run(client.get_index_minute_local(code))
        assert r.success is False, f"{code} 必须拒绝"
        assert r.error_detail["error_type"] == "invalid_symbol"
        assert "get_minute_local" in r.error


def test_index_minute_bad_symbol_invalid(client):
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_index_minute_local("abc"))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_symbol"


def test_index_minute_no_data_names_lc1_path(client):
    """指数 .lc1 缺失 → no_data 文案给出实际市场路径。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=None)
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("932000"))
    assert r.success is True
    assert r.data is None
    assert "sh932000" in r.error and "minline" in r.error and ".lc1" in r.error


def test_index_minute_start_end_date_filter(client):
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-17 10:00", 3900.0, 1.0, 3900.0),
        ("2026-09-18 10:00", 3910.0, 1.0, 3910.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(
            client.get_index_minute_local(
                "000001", start_date="2026-09-18", end_date="2026-09-18"
            )
        )
    assert r.success is True
    assert len(r.data) == 1
    assert r.data[0]["datetime"].startswith("2026-09-18")


def test_index_fzline_reads_lc5(client):
    """get_index_fzline_local 走 Reader.fzline（= .lc5）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(fzline=_minute_df([
        ("2026-09-18 09:35", 3900.0, 500.0, 1_950_000.0),
        ("2026-09-18 09:40", 3902.0, 600.0, 2_341_200.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_fzline_local("000001"))

    assert r.success is True
    assert mock_reader.fzline.call_args.kwargs["symbol"] == "sh000001"
    assert len(r.data) == 2
    assert r.data[1]["datetime"].endswith("09:40")


def test_index_fzline_stock_rejected_and_no_data_path(client):
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r_stock = asyncio.run(client.get_index_fzline_local("600000"))
    assert r_stock.success is False
    assert r_stock.error_detail["error_type"] == "invalid_symbol"

    mock_reader = _patch_reader(fzline=False)
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_fzline_local("sz399001"))
    assert r.success is True and r.data is None
    # 文案与 _index_minute_file_message(parsed, 5) 共用（DRY）
    assert "sz399001" in r.error and "fzline" in r.error and ".lc5" in r.error
    assert "5 分钟线数据" in r.error and "不存在或为空" in r.error


def test_index_minute_real_reader_shape_preserves_native_datetime(client):
    """真实 Reader 形态（index.name='date' + 原生 datetime 列）：
    记录里不泄漏 date/index 键，原生字符串 datetime 原样保留（无 :00 尾缀）。
    """
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_real_minute_df([
        ("2026-09-18 09:31", 3900.0, 100.0, 390000.0),
        ("2026-09-18 15:00", 3910.0, 200.0, 782000.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("000001"))

    assert r.success is True
    assert len(r.data) == 2
    for rec in r.data:
        assert "date" not in rec and "index" not in rec
        assert rec["datetime"][-3] == ":"  # "HH:MM"，不含秒
    assert r.data[0]["datetime"] == "2026-09-18 09:31"
    assert r.data[1]["datetime"] == "2026-09-18 15:00"
    # 未截断：不附 warn
    assert r.error_detail is None


def test_index_minute_tail_limit_truncates_with_warn(client):
    """limit 截最近 N 条 + warn（含文件总数与上限提示）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    rows = [
        ("2026-09-18 09:3%d" % i, 3900.0 + i, 100.0, 390000.0)
        for i in range(1, 6)
    ]
    mock_reader = _patch_reader(minute=_minute_df(rows))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("000001", limit=2))

    assert r.success is True
    assert len(r.data) == 2
    # 截的是最近两条（尾部）
    assert r.data[0]["datetime"].endswith("09:34")
    assert r.data[1]["datetime"].endswith("09:35")
    detail = r.error_detail
    assert detail and detail["warn"]
    assert "2" in detail["warn"] and detail["returned"] == 2
    assert "5" in detail["warn"]  # 文件内总数
    assert "文件内" in detail["warn"]


def test_index_minute_truncation_warn_scope_with_date_window(client):
    """带日期窗时截断 warn 的 total 口径是"窗内条数"，不能谎称文件总数。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    rows = [
        ("2026-09-18 09:3%d" % i, 3900.0 + i, 100.0, 390000.0)
        for i in range(1, 6)
    ]
    mock_reader = _patch_reader(minute=_minute_df(rows))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local(
            "000001", start_date="2026-09-18", end_date="2026-09-18", limit=2,
        ))
    assert r.success is True and len(r.data) == 2
    warn = r.error_detail["warn"]
    assert "所选日期范围内" in warn
    assert "文件内" not in warn


def test_index_minute_non_string_date_rejected(client):
    """直调传 int 日期（HTTP 边界不会出现）→ invalid_param 而非 TypeError。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_index_minute_local("000001", start_date=20260918))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_param"


@pytest.mark.parametrize("bad_limit", [0, -1, 20001, 100000, "1000", 1.5, True])
def test_index_minute_bad_limit_rejected(client, bad_limit):
    """limit 门控：正整数且 ≤ 20000（bool 不等于 int）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_index_minute_local("000001", limit=bad_limit))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_param"


@pytest.mark.parametrize("start_date,end_date", [
    ("2026-9-1", ""),       # 非零补
    ("2026-13-01", ""),     # 非法日历日
    ("2026091", ""),        # 紧凑但只有 7 位
    ("2026-09-18", "2026-09-17"),  # 起晚于止
    ("20260918", "20260917"),      # 紧凑格式同样校验顺序
    ("", "not-a-date"),     # 完全不沾边
])
def test_index_minute_bad_date_window_rejected(client, start_date, end_date):
    """日期窗严格 YYYY-MM-DD 门控，非法 → invalid_param，且不碰 Reader。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(
        client.get_index_minute_local(
            "000001", start_date=start_date, end_date=end_date
        )
    )
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_param"


def test_index_minute_window_no_match_distinguishes_from_missing_file(client):
    """文件有数据但日期窗切空：warn 必须说"文件存在但范围内无数据"，
    不能沿用"文件不存在"文案。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-17 10:00", 3900.0, 1.0, 3900.0),
        ("2026-09-18 10:00", 3910.0, 1.0, 3910.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(
            client.get_index_minute_local(
                "000001", start_date="2026-09-10", end_date="2026-09-10"
            )
        )
    assert r.success is True and r.data is None
    assert "文件存在" in r.error
    assert "日期范围" in r.error
    assert "不存在或为空" not in r.error


def test_index_minute_end_date_inclusive_full_day(client):
    """end_date 含当日全天：09:31 与 15:00 两根都要保留（股票端曾有
    按午夜字符串比较丢掉 end 当日全天数据的坑，指数端防回归）。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-18 09:31", 3900.0, 1.0, 3900.0),
        ("2026-09-18 15:00", 3910.0, 1.0, 3910.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(
            client.get_index_minute_local(
                "000001", start_date="2026-09-18", end_date="2026-09-18"
            )
        )
    assert r.success is True
    assert len(r.data) == 2
    assert [x["datetime"][-5:] for x in r.data] == ["09:31", "15:00"]


def test_index_minute_compact_yyyymmdd_window_accepted(client):
    """YYYYMMDD（registry 在 HTTP 边界归一后的格式）与 YYYY-MM-DD 等价：
    两种写法对同一日期窗都只切出 9/18 当天的 bar。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    rows = [
        ("2026-09-17 15:00", 3900.0, 1.0, 3900.0),
        ("2026-09-18 09:31", 3910.0, 1.0, 3910.0),
        ("2026-09-18 15:00", 3911.0, 1.0, 3911.0),
    ]
    mock_reader = _patch_reader(minute=_minute_df(rows))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r_dash = asyncio.run(client.get_index_minute_local(
            "000001", start_date="2026-09-18", end_date="2026-09-18"))
        r_compact = asyncio.run(client.get_index_minute_local(
            "000001", start_date="20260918", end_date="20260918"))
        r_mixed = asyncio.run(client.get_index_minute_local(
            "000001", start_date="20260918"))
    for r in (r_dash, r_compact, r_mixed):
        assert r.success is True and len(r.data) == 2
        assert all(x["datetime"].startswith("2026-09-18") for x in r.data)


def test_index_minute_suffix_int_accepted(client):
    """MCP JSON 整数 suffix=1 与字符串 '1' 等价。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=_minute_df([
        ("2026-09-18 09:31", 3900.0, 100.0, 390000.0),
    ]))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("000001", suffix=1))
    assert r.success is True
    assert mock_reader.minute.call_args.kwargs["suffix"] == 1


def test_index_minute_zero_bar_empty_file_message(client):
    """空 DataFrame（文件存在但 0 根）与文件缺失同路：no_data 文案措辞
    为"不存在或为空"，不谎称一定不存在。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = _patch_reader(minute=pd.DataFrame())
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_minute_local("932000"))
    assert r.success is True and r.data is None
    assert "sh932000" in r.error and "minline" in r.error
    assert "不存在或为空" in r.error


def test_index_fzline_tail_limit_truncates_with_warn(client):
    """fzline 独立入口同样有 limit 截断 + warn。"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    rows = [
        ("2026-09-18 09:%02d" % (30 + 5 * i), 3900.0 + i, 100.0, 390000.0)
        for i in range(4)
    ]
    mock_reader = _patch_reader(fzline=_minute_df(rows))
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_index_fzline_local("000001", limit=2))
    assert r.success is True
    assert len(r.data) == 2
    assert r.data[-1]["datetime"].endswith("09:45")
    assert r.error_detail and "截断" in r.error_detail["warn"]


def test_index_local_tools_registered_in_method_map(client):
    """2 个指数离线工具注册进 _TOOL_METHOD_NAMES 且方法可调用。"""
    from src.unihive.api.mootdx2_client import _TOOL_METHOD_NAMES

    for name in (
        "get_index_minute_local",
        "get_index_fzline_local",
    ):
        assert name in _TOOL_METHOD_NAMES, f"{name} 未注册"
        assert callable(getattr(client, _TOOL_METHOD_NAMES[name], None))


def test_yaml_has_index_minute_fzline_offline():
    """2 个新指数工具在 YAML 声明为 offline，参数合同正确。"""
    import yaml
    with open("config/tools_mootdx2.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    by_name = {t["name"]: t for t in data["tools"]}

    minute = by_name["get_index_minute_local"]
    fzline = by_name["get_index_fzline_local"]
    for spec in (minute, fzline):
        assert spec["data_source_type"] == "offline"
        assert spec["params"][0]["name"] == "code"
        assert spec["params"][0]["required"] is True

    minute_params = {p["name"]: p for p in minute["params"]}
    assert minute_params["suffix"]["enum"] == ["1", "5"]
    # 分钟线参数合同：code/suffix/start_date/end_date/limit
    assert [p["name"] for p in minute["params"]] == [
        "code", "suffix", "start_date", "end_date", "limit",
    ]
    assert minute_params["limit"]["type"] == "int"
    # fzline: code + limit（默认 1000 / 最大 20000，截断带 warn）
    fzline_params = {p["name"]: p for p in fzline["params"]}
    assert list(fzline_params) == ["code", "limit"]
    assert fzline_params["limit"]["type"] == "int"

    # 离线分时图工具已下线（2026-09-21）：当日实时分时用 get_minute_data
    assert "get_index_timeshare_local" not in by_name
    assert "get_timeshare_local" not in by_name


# ========== _get_daily_batch_local_sync ==========

def test_get_daily_batch_local_two_codes(client):
    """测试批量获取2只股票"""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"

    # Mock two different stock DataFrames
    mock_df_600036 = pd.DataFrame({
        "open": [10.0],
        "high": [10.5],
        "low": [9.9],
        "close": [10.3],
        "vol": [1000.0],
    }, index=pd.to_datetime(["2024-01-02"]))

    mock_df_600519 = pd.DataFrame({
        "open": [1500.0],
        "high": [1520.0],
        "low": [1490.0],
        "close": [1510.0],
        "vol": [5000.0],
    }, index=pd.to_datetime(["2024-01-02"]))

    mock_reader = MagicMock()
    # Reader.daily receives bare symbols (600036, 600519)
    mock_reader.daily.side_effect = [mock_df_600036, mock_df_600519]

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        result = client._get_daily_batch_local_sync(
            code_list=["sh600036", "sh600519"],
            adjust="none",
            start_date="",
            end_date=""
        )

    results, warnings = result
    # key 保留输入的前缀格式
    assert "sh600036" in results
    assert "sh600519" in results
    assert isinstance(results["sh600036"], list)
    assert len(results["sh600036"]) == 1
    assert results["sh600036"][0]["close"] == 10.3
    assert results["sh600519"][0]["close"] == 1510.0
