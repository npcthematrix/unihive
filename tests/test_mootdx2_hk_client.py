"""港股离线取数的 client 层测试（mock ExHq reader，不依赖真实文件）。"""
import asyncio
from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def client():
    from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
    c = MooTDX2Client(MooTDX2Config(name="test", market="std"))
    c.config.settings = MagicMock()
    c.config.settings.tdxdir = "/fake/tdx"
    return c


def _ex_df() -> pd.DataFrame:
    return pd.DataFrame({
        "open": [430.0], "high": [431.0], "low": [424.4], "close": [429.4],
        "amount": [1.3e9], "volume": [130489.0],
        "jiesuan": [0.0], "hk_stock_amount": [0.0],
    }, index=pd.to_datetime(["2026-09-30"]))


def _patch_exhq(client, df=None, side_effect=None):
    mock = MagicMock()
    if side_effect is not None:
        mock.get_df.side_effect = side_effect
    else:
        mock.get_df.return_value = df if df is not None else _ex_df()
    client._get_exhq_reader = lambda: mock
    return mock


def test_hk_stock_daily_reads_ds_file(client):
    mock = _patch_exhq(client)
    r = asyncio.run(client.get_daily("00700.HK"))
    assert r.success is True
    assert len(r.data) == 1
    rec = r.data[0]
    assert rec["date"] == "2026-09-30"
    assert rec["vol"] == 130489.0          # volume 已对齐为 std 的 vol
    assert "jiesuan" not in rec and "hk_stock_amount" not in rec
    path = str(mock.get_df.call_args.args[0])
    assert path.endswith("31#00700.day")


def test_hk_stock_adjust_forced_none_with_warn(client):
    _patch_exhq(client)
    r = asyncio.run(client.get_daily("hk00700", adjust="qfq"))
    assert r.success is True
    warn = r.error_detail["warn"]
    assert "none" in warn and "00700" in warn


def test_hk_stock_missing_file_is_no_data(client):
    _patch_exhq(client, side_effect=FileNotFoundError("nope"))
    r = asyncio.run(client.get_daily("hk99999"))
    assert r.success is True
    assert r.data is None
    assert r.error_detail["error_type"] == "no_data"


def test_hk_index_daily_reads_market27(client):
    mock = _patch_exhq(client)
    r = asyncio.run(client.get_index_kline_local("hkHSI", limit=10))
    assert r.success is True
    assert r.data[0]["close"] == 429.4
    assert str(mock.get_df.call_args.args[0]).endswith("27#HSI.day")


def test_hk_index_missing_file_is_no_data(client):
    _patch_exhq(client, side_effect=FileNotFoundError("nope"))
    r = asyncio.run(client.get_index_kline_local("hkNOPE"))
    assert r.success is True
    assert r.data is None
    assert r.error_detail["error_type"] == "no_data"


def test_hk_stock_letter_token_is_invalid_symbol(client):
    # hkHSI 是指数，错给股票入口
    r = asyncio.run(client.get_daily("hkHSI"))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_symbol"


def test_batch_mixed_a_share_and_hk(client, monkeypatch):
    # 港股 reader
    _patch_exhq(client)
    # A 股 std reader
    a_df = pd.DataFrame({
        "open": [40.0], "high": [40.5], "low": [39.8], "close": [40.2],
        "vol": [1e7], "amount": [4e8],
    }, index=pd.to_datetime(["2026-09-30"]))
    mock_std = MagicMock()
    mock_std.daily.return_value = a_df
    mock_factory = MagicMock(return_value=mock_std)
    monkeypatch.setattr("mootdx2.reader.Reader.factory", mock_factory)

    r = asyncio.run(
        client.get_daily_batch_local("sh600036,hk00700")
    )
    assert r.success is True
    assert set(r.data.keys()) == {"sh600036", "hk00700"}


def test_batch_rejects_unsupported_code(client):
    r = asyncio.run(client.get_daily_batch_local("hk00700,abc"))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_symbol"
