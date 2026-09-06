import pytest
import unittest.mock as mock
from datetime import datetime, timedelta
from src.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.upstream_client import UpstreamStatus


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

    def test_indicator_vol_ma(self):
        """Test indicator_vol_ma returns vol_ma5, vol_ma10, and dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        mock_kline = [
            {"date": d, "open": 10.0 + i * 0.1 - 0.05, "high": 10.0 + i * 0.1 + 0.3,
             "low": 10.0 + i * 0.1 - 0.2, "close": 10.0 + i * 0.1, "vol": 1000000 + i * 10000}
            for i, d in zip(range(60), dates)
        ]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_vol_ma_sync("600000", "day", 60)
        assert "vol_ma5" in result
        assert "vol_ma10" in result
        assert "dates" in result
        # Check computed values are positive
        for v in result["vol_ma5"]:
            if v is not None:
                assert v > 0
        for v in result["vol_ma10"]:
            if v is not None:
                assert v > 0
