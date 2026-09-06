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
        # Verify DIF = EMA12 - EMA26 and MACD = (DIF - DEA) * 2
        closes = [r["close"] for r in mock_kline]
        k = 2 / (12 + 1)
        ema12_val = closes[11]
        for i in range(12, len(closes)):
            ema12_val = round(closes[i] * k + ema12_val * (1 - k), 3)
        ema12_val = round(ema12_val, 3)
        k26 = 2 / (26 + 1)
        ema26_val = closes[25]
        for i in range(26, len(closes)):
            ema26_val = round(closes[i] * k26 + ema26_val * (1 - k26), 3)
        ema26_val = round(ema26_val, 3)
        expected_dif = round(ema12_val - ema26_val, 3)
        assert result["dif"][-1] == expected_dif, f"DIF mismatch: {result['dif'][-1]} != {expected_dif}"
        # Verify MACD = (DIF - DEA) * 2
        dif_last = result["dif"][-1]
        dea_last = result["dea"][-1]
        macd_last = result["macd"][-1]
        if dif_last is not None and dea_last is not None:
            assert macd_last == round((dif_last - dea_last) * 2, 3)

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

    def test_indicator_ema(self):
        """Test indicator_ema returns ema12, ema26, and dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        close_prices = [10.0 + i * 0.1 for i in range(60)]
        mock_kline = [{"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
                      for d, p in zip(dates, close_prices)]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_ema_sync("600000", "day", 60)
        assert "ema12" in result
        assert "ema26" in result
        assert "dates" in result
        assert len(result["ema12"]) == len(result["dates"])
        assert result["ema12"][11] is not None  # 12th value is first computed
        assert result["ema26"][25] is not None  # 26th value is first computed
        # None values before first computation
        assert all(v is None for v in result["ema12"][:11])
        assert all(v is None for v in result["ema26"][:25])

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
        from src.mootdx2_client import MooTDX2Client, MooTDX2Config
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

    def test_indicator_ma(self):
        """Test indicator_ma returns ma5, ma10, ma20, ma60, dates"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        close_prices = [10.0 + i * 0.1 + (i % 5) * 0.2 for i in range(60)]
        mock_kline = [
            {"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
            for d, p in zip(dates, close_prices)
        ]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_ma_sync("600000", "day", 60)
        assert "ma5" in result
        assert "ma10" in result
        assert "ma20" in result
        assert "ma60" in result
        assert "dates" in result
        assert len(result["ma5"]) == len(result["dates"])
        # First 4 values of ma5 should be None (need 5 data points)
        assert result["ma5"][0] is None
        assert result["ma5"][1] is None
        assert result["ma5"][2] is None
        assert result["ma5"][3] is None
        assert result["ma5"][4] is not None  # 5th value is first computed
        # ma60 needs 60 data points
        assert all(v is None for v in result["ma60"][:59])
        assert result["ma60"][59] is not None

    def test_stock_unusual_filters_by_event_type(self):
        """Test stock_unusual filters stocks by event_type"""
        import pandas as pd
        from src.mootdx2_client import MooTDX2Client, MooTDX2Config
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

    def test_indicator_kdj(self):
        """Test indicator_kdj returns k, d, j, dates and J = 3K - 2D"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
        mock_kline = [{"date": d, "open": 10.0 + i * 0.1 - 0.05, "high": 10.0 + i * 0.1 + 0.3,
                       "low": 10.0 + i * 0.1 - 0.2, "close": 10.0 + i * 0.1, "vol": 1000000}
                      for i, d in zip(range(60), dates)]
        with mock.patch.object(client, '_get_kline_sync', return_value=mock_kline):
            result = client._indicator_kdj_sync("600000", "day", 60)
        assert "k" in result
        assert "d" in result
        assert "j" in result
        assert "dates" in result
        # Verify J = 3K - 2D for computed values
        for i in range(9, len(result["k"])):
            if result["k"][i] is not None:
                expected_j = 3 * result["k"][i] - 2 * result["d"][i]
                assert abs(result["j"][i] - expected_j) < 0.01
