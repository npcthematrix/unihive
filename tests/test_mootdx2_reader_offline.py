"""Reader-based offline tools tests — mock mootdx.reader.Reader to avoid TDX file dependency."""
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
    """tdxdir empty → tdx_not_installed."""
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(
            client.get_daily("600036", start_date="2024-01-03", end_date="2024-01-04")
        )

    assert r.success is True
    assert len(r.data) == 2
    assert r.data[0]["date"] == "2024-01-03"
    assert r.data[1]["date"] == "2024-01-04"


# ========== get_minute ==========

def test_get_minute_tdxdir_empty(client):
    """tdxdir empty → tdx_not_installed."""
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_minute("600036", suffix="5"))

    assert r.success is True
    assert mock_reader.minute.call_args.kwargs.get("suffix") == 5


# ========== get_fzline ==========

def test_get_fzline_tdxdir_empty(client):
    """tdxdir empty → tdx_not_installed."""
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
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

    with patch("mootdx.reader.Reader.factory", return_value=mock_reader):
        r = asyncio.run(client.get_fzline("600036"))

    assert r.success is True
    assert r.data is None
    assert "未找到" in r.error


# ========== method_map 注册验证 ==========

def test_new_tools_registered_in_method_map(client):
    """call_tool 必须能路由到 3 个新离线接口。"""
    import inspect

    expected = {"get_daily", "get_minute", "get_fzline"}
    src = inspect.getsource(client.call_tool)
    for name in expected:
        assert name in src, f"{name} 未在 method_map 注册"


# ========== YAML 配置验证 ==========

def test_yaml_has_offline_reader_tools():
    """3 个新工具都在 tools_mootdx2.yaml 中声明为 offline。"""
    import yaml
    from pathlib import Path

    with open("config/tools_mootdx2.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    by_name = {t["name"]: t for t in data["tools"]}

    assert by_name["get_daily"]["data_source_type"] == "offline"
    assert by_name["get_minute"]["data_source_type"] == "offline"
    assert by_name["get_fzline"]["data_source_type"] == "offline"

    # verify params
    daily_params = [p["name"] for p in by_name["get_daily"]["params"]]
    assert "code" in daily_params
    assert "adjust" in daily_params
    assert by_name["get_daily"]["params"][1]["enum"] == ["none", "qfq", "hfq"]

    minute_params = [p["name"] for p in by_name["get_minute"]["params"]]
    assert "suffix" in minute_params
    assert by_name["get_minute"]["params"][1]["enum"] == ["1", "5"]