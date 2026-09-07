"""Sector tools tests — mock BlockReader and Customize to avoid TDX file dependency."""
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def client():
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


# ========== get_sector_list ==========

def test_get_sector_list_tdxdir_empty(client):
    """sector_type=industry + tdxdir empty → tdx_not_installed."""
    r = asyncio.run(client.get_sector_list("industry"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_not_installed"


def test_get_sector_list_invalid_param(client):
    """sector_type not in industry/concept/region → invalid_param."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_sector_list("bogus"))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_param"


def test_get_sector_list_file_missing(client):
    """sector file missing → tdx_sector_file_missing."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=False):
        r = asyncio.run(client.get_sector_list("industry"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_sector_file_missing"


def test_get_sector_list_industry_ok(client):
    """industry hits mock BlockReader → success, list of sectors."""
    import pandas as pd

    mock_df = pd.DataFrame({
        "blockname": ["银行板块", "钢铁板块"],
        "code_list": ["600000,600016", "600019"],
    })

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"

    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df

    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_list("industry"))

    assert r.success is True
    assert r.data == [
        {"sector_name": "银行板块", "sector_type": "industry", "stock_count": 2},
        {"sector_name": "钢铁板块", "sector_type": "industry", "stock_count": 1},
    ]


def test_get_sector_list_concept_ok(client):
    """concept path → success, different sector_type in output."""
    import pandas as pd

    mock_df = pd.DataFrame({"blockname": ["猪肉概念"], "code_list": ["002714"]})
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"

    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_list("concept"))

    assert r.success is True
    assert r.data[0]["sector_type"] == "concept"


# ========== get_sector_stocks ==========

def test_get_sector_stocks_hit(client):
    """sector name matches → return [{code, market}]."""
    import pandas as pd

    mock_df = pd.DataFrame({
        "blockname": ["银行板块", "钢铁板块"],
        "code_list": ["600000,600016", "600019"],
    })
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_stocks("银行板块", "industry"))

    assert r.success is True
    assert r.data == [
        {"code": "600000", "market": "sh"},
        {"code": "600016", "market": "sh"},
    ]


def test_get_sector_stocks_not_found(client):
    """sector name absent → sector_not_found error."""
    import pandas as pd

    mock_df = pd.DataFrame({"blockname": ["钢铁板块"], "code_list": ["600019"]})
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_stocks("不存在的板块", "industry"))

    assert r.success is False
    assert r.error_detail["error_type"] == "sector_not_found"


# ========== get_stock_sectors ==========

def test_get_stock_sectors_aggregates_three_types(client):
    """stock code matches across 3 files → returns 3 entries."""
    import pandas as pd

    industry_df = pd.DataFrame({"blockname": ["银行板块"], "code": ["600000"]})
    concept_df = pd.DataFrame({"blockname": ["大盘股"], "code": ["600000"]})
    region_df = pd.DataFrame({"blockname": ["上海"], "code": ["600000"]})

    mock_reader = MagicMock()
    mock_reader.get_df.side_effect = [industry_df, concept_df, region_df]

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_stock_sectors("600000"))

    assert r.success is True
    assert len(r.data) == 3
    assert {x["sector_type"] for x in r.data} == {"industry", "concept", "region"}


def test_get_stock_sectors_empty_when_no_match(client):
    """stock not in any file → success with empty list (NOT error)."""
    import pandas as pd

    mock_reader = MagicMock()
    mock_reader.get_df.return_value = pd.DataFrame(columns=["blockname", "code"])

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_stock_sectors("600000"))

    assert r.success is True
    assert r.data == []


def test_get_stock_sectors_bad_format(client):
    """non-6-digit code → stock_not_found error."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_stock_sectors("abc"))
    assert r.success is False
    assert r.error_detail["error_type"] == "stock_not_found"


def test_get_stock_sectors_strip_prefix(client):
    """sh600000 → normalized to 600000, matches rows with code='600000'."""
    import pandas as pd

    industry_df = pd.DataFrame({"blockname": ["银行板块"], "code": ["600000"]})
    mock_reader = MagicMock()
    mock_reader.get_df.side_effect = [industry_df, pd.DataFrame(), pd.DataFrame()]

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_stock_sectors("sh600000"))

    assert r.success is True
    assert r.data == [{"sector_name": "银行板块", "sector_type": "industry"}]


# ========== get_custom_sector_list ==========

def test_get_custom_sector_list_unavailable(client):
    """T0002/blocknew missing → tdx_custom_sector_unavailable."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    # TDX path exists, but T0002/blocknew does not
    def fake_exists(self):
        return "blocknew" not in str(self)
    with patch.object(Path, "exists", fake_exists):
        r = asyncio.run(client.get_custom_sector_list())
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_custom_sector_unavailable"


def test_get_custom_sector_list_ok(client):
    """Customize.search returns list → success."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_customize = MagicMock()
    mock_customize.search.return_value = [
        ("我的持仓", ["600000", "000001"]),
        ("观察", ["300750"]),
    ]
    with patch.object(Path, "exists", return_value=True), \
         patch("mootdx.tools.customize.Customize", return_value=mock_customize):
        r = asyncio.run(client.get_custom_sector_list())
    assert r.success is True
    assert r.data == [
        {"sector_name": "我的持仓", "sector_type": "custom", "stock_count": 2},
        {"sector_name": "观察", "sector_type": "custom", "stock_count": 1},
    ]


# ========== get_custom_sector_stocks ==========

def test_get_custom_sector_stocks_hit(client):
    """Customize.search(name=...) returns codes → success."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_customize = MagicMock()
    mock_customize.search.return_value = ["600000", "sh600016", "000001"]
    with patch.object(Path, "exists", return_value=True), \
         patch("mootdx.tools.customize.Customize", return_value=mock_customize):
        r = asyncio.run(client.get_custom_sector_stocks("我的持仓"))
    assert r.success is True
    assert r.data == [
        {"code": "600000", "market": "sh"},
        {"code": "600016", "market": "sh"},
        {"code": "000001", "market": "sz"},
    ]


def test_get_custom_sector_stocks_not_found(client):
    """Customize.search returns None → sector_not_found."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_customize = MagicMock()
    mock_customize.search.return_value = None
    with patch.object(Path, "exists", return_value=True), \
         patch("mootdx.tools.customize.Customize", return_value=mock_customize):
        r = asyncio.run(client.get_custom_sector_stocks("不存在的分组"))
    assert r.success is False
    assert r.error_detail["error_type"] == "sector_not_found"
