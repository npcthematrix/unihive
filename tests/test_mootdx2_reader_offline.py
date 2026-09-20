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
