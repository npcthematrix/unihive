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

    def test_indicator_macd(self):
        """Test indicator_macd returns dif, dea, macd, dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        close_prices = [10.0 + i * 0.1 for i in range(60)]
        mock_kline = [{"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
                      for d, p in zip(dates, close_prices)]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_macd_sync("600000", "day", 60)
        assert "dif" in result
        assert "dea" in result
        assert "macd" in result
        assert "dates" in result
        assert len(result["dif"]) == len(result["dates"])
        # Verify DIF calculation: dif = ema12 - ema26
        import pandas as pd
        closes = [r["close"] for r in mock_kline]
        ema12_series = pd.Series(closes).ewm(span=12, adjust=False).mean()
        ema26_series = pd.Series(closes).ewm(span=26, adjust=False).mean()
        expected_dif_last = round(ema12_series.iloc[-1] - ema26_series.iloc[-1], 3)
        assert result["dif"][-1] == expected_dif_last

    def test_indicator_rsi(self):
        """Test indicator_rsi returns rsi6, rsi12, rsi24, dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        close_prices = [10.0 + i * 0.1 for i in range(60)]
        mock_kline = [{"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
                      for d, p in zip(dates, close_prices)]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_rsi_sync("600000", "day", 60)
        assert "rsi6" in result
        assert "rsi12" in result
        assert "rsi24" in result
        assert "dates" in result
        # RSI values should be between 0 and 100
        for rsi_name in ["rsi6", "rsi12", "rsi24"]:
            for v in result[rsi_name]:
                if v is not None:
                    assert 0 <= v <= 100, f"{rsi_name} value {v} out of range"

    def test_indicator_boll(self):
        """Test indicator_boll returns boll_upper, boll_mid, boll_lower, dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        close_prices = [10.0 + i * 0.1 for i in range(60)]
        mock_kline = [{"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
                      for d, p in zip(dates, close_prices)]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_boll_sync("600000", "day", 60)
        assert "boll_upper" in result
        assert "boll_mid" in result
        assert "boll_lower" in result
        assert "dates" in result
        # Verify upper > mid > lower for computed values
        for i in range(20, len(result["boll_upper"])):
            if result["boll_upper"][i] is not None:
                assert result["boll_upper"][i] > result["boll_mid"][i] > result["boll_lower"][i]
