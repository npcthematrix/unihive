import pytest
import unittest.mock as mock
from datetime import datetime, timedelta
from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.api.upstream_client import UpstreamStatus


class TestMooTDX2Client:
    def test_config_creation(self):
        config = MooTDX2Config(name="test", market="std")
        assert config.name == "test"
        assert config.market == "std"

    def test_get_quote(self):
        """Test get_quote returns quote data"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        # Mock quotes attribute
        class MockQuotes:
            def quotes(self, symbols):
                import pandas as pd
                return pd.DataFrame([{"symbol": "600000", "close": 10.0, "open": 9.5, "high": 10.2, "low": 9.4, "vol": 1000000, "amount": 10000000}])
        client._quotes = MockQuotes()
        result = client._get_quote_sync("600000")
        assert result is not None
        assert result["close"] == 10.0

    def test_get_kline(self):
        """Test get_kline returns K-line data"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        # Mock quotes with bars method
        class MockQuotes:
            def bars(self, symbol, frequency, offset):
                import pandas as pd
                return pd.DataFrame([
                    {"date": "2026-01-01", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 1000000},
                    {"date": "2026-01-02", "open": 10.5, "close": 11.0, "high": 11.2, "low": 10.4, "volume": 1200000},
                ])
        client._quotes = MockQuotes()
        result = client._get_kline_sync("600000", type="day", limit=10)
        assert result is not None
        assert len(result) == 2

    def test_indicator_atr(self):
        """Test indicator_atr returns atr and dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        close_prices = [10.0 + i * 0.1 for i in range(60)]
        mock_kline = [{"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
                      for d, p in zip(dates, close_prices)]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_atr_sync("600000", "day", 60)
        assert "atr" in result
        assert "dates" in result
        # ATR values should be positive for computed entries
        for v in result["atr"]:
            if v is not None:
                assert v > 0

    def test_get_index_overview_returns_six_indices(self):
        """Test get_index_overview returns 6 major indices"""
        import pandas as pd
        from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        # Mock _get_quotes to return DataFrame with 6 index rows
        mock_df = pd.DataFrame([
            {"symbol": "000001", "close": 3100.0, "pct_chg": 0.5},
            {"symbol": "399001", "close": 10000.0, "pct_chg": -0.3},
            {"symbol": "399006", "close": 2000.0, "pct_chg": 1.2},
            {"symbol": "000688", "close": 900.0, "pct_chg": 0.8},
            {"symbol": "889999", "close": 1000.0, "pct_chg": -0.1},
            {"symbol": "000300", "close": 3800.0, "pct_chg": 0.2},
        ])
        mock_quotes = type('MockQuotes', (), {'quotes': lambda self, symbols: mock_df})()
        with mock.patch.object(client, '_get_quotes', return_value=mock_quotes):
            result = client._index_overview_sync()
        assert len(result) == 6
        assert result[0]["code"] == "000001"
        assert result[0]["name"] == "上证指数"
        assert result[0]["market"] == "sh"
        assert result[0]["close"] == 3100.0
        assert result[0]["change_pct"] == 0.5
        assert result[1]["code"] == "399001"
        assert result[1]["name"] == "深证成指"
        assert result[1]["market"] == "sz"
        assert result[5]["code"] == "000300"
        assert result[5]["name"] == "沪深300"
        assert result[5]["market"] == "sh"

    def test_stock_top_board_sorts_correctly(self):
        """Test stock_top_board returns sorted results"""
        import pandas as pd
        from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        mock_stock_df = pd.DataFrame({"code": ["600000", "600016", "600036"]})
        mock_quote_df = pd.DataFrame([
            {"symbol": "600000", "close": 10.5, "pct_chg": 9.9, "vol": 1000000, "amount": 10000000, "turnover": 2.5, "volume_ratio": 1.5},
            {"symbol": "600016", "close": 8.0, "pct_chg": 5.0, "vol": 500000, "amount": 4000000, "turnover": 1.2, "volume_ratio": 0.8},
            {"symbol": "600036", "close": 35.0, "pct_chg": 3.0, "vol": 800000, "amount": 28000000, "turnover": 1.8, "volume_ratio": 1.2},
        ])
        mock_q = type("MockQ", (), {
            "stock": lambda self, exchange: mock_stock_df,
            "quotes": lambda self, symbols: mock_quote_df,
        })()
        with mock.patch.object(client, "_get_quotes", return_value=mock_q):
            result = client._stock_top_board_sync(sort_by="change_pct", direction="desc", limit=10, market="all")
        assert len(result) >= 3
        assert result[0]["change_pct"] >= result[1]["change_pct"]  # desc sort

    def test_stock_unusual_filters_by_event_type(self):
        """Test stock_unusual filters stocks by event_type"""
        import pandas as pd
        from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        mock_block_df = pd.DataFrame([{"name": "沪股通", "code_list": "600000,600016,600036"}])
        mock_quote_df = pd.DataFrame([
            {"symbol": "600000", "close": 10.5, "pct_chg": 9.9, "vol": 1000000},
            {"symbol": "600016", "close": 8.0, "pct_chg": -9.5, "vol": 800000},
            {"symbol": "600036", "close": 35.0, "pct_chg": 3.0, "vol": 500000},
        ])
        mock_q = type('MockQ', (), {
            'block': lambda self, block_type: mock_block_df,
            'quotes': lambda self, symbols: mock_quote_df,
        })()
        with mock.patch.object(client, '_get_quotes', return_value=mock_q):
            result = client._stock_unusual_sync(event_type="all")
        assert len(result) >= 2  # 600000 and 600016 are unusual (pct_chg >= 5 or >= 9.5)
        # Test 涨 filter
        with mock.patch.object(client, '_get_quotes', return_value=mock_q):
            result_涨 = client._stock_unusual_sync(event_type="涨")
        assert all(r["change_pct"] > 0 for r in result_涨)
        # Verify event labels are assigned correctly
        for r in result:
            if r["change_pct"] >= 9.9:
                assert r["event"] == "涨停", f"Expected 涨停 but got {r['event']} for {r['code']}"
            elif r["change_pct"] <= -9.9:
                assert r["event"] == "跌停"
