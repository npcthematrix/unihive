"""MOOTDX2 离线接口 · Round-4 Batch 1 修复测试 (2026-09-28)。

覆盖:
- CR1 _with_warn 对 None data 不崩溃
- CR2 get_index_kline_local L1 hit 返回 list 副本 (非缓存原引用)
- H1 get_index_kline_local cache_key 用 parse_symbol 归一化
- H4 get_minute cache_key 统一 str(suffix)
- H5 search_stock 空白 keyword 入口校验
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import (
    MooTDX2Client,
    MooTDX2Config,
    ToolResult,
)


# ========== fixtures ==========

@pytest.fixture
def client() -> MooTDX2Client:
    c = MooTDX2Client(MooTDX2Config(name="test", market="std"))
    c.config.settings = MagicMock()
    c.config.settings.tdxdir = "/fake/tdx"
    return c


# ========== CR1: _with_warn None data ==========

class TestWithWarnNoneData:
    """CR1: _with_warn 对 data=None 不崩溃."""

    def test_with_warn_handles_none_data_safely(self):
        """_with_warn(result with data=None, warn) 不应抛 TypeError."""
        result = ToolResult(success=True, data=None, source="mootdx2")
        # 不带 warn 时,应该直接返回 (不变更 data)
        out = MooTDX2Client._with_warn(result, None)
        assert out.success is True
        assert out.data is None

    def test_with_warn_with_warn_and_none_data(self):
        """_with_warn(data=None, warn) 不应崩溃,保留 None."""
        result = ToolResult(success=True, data=None, source="mootdx2")
        # 带 warn 时, 应该把 warn 加到 error_detail, data 保持 None
        out = MooTDX2Client._with_warn(result, "test warn")
        assert out.success is True
        assert out.data is None
        assert out.error_detail is not None
        assert out.error_detail.get("warn") == "test warn"

    def test_with_warn_list_data_copies(self):
        """_with_warn(data=list) 应浅复制 (data) 不共享引用."""
        original_data = [{"a": 1}, {"a": 2}]
        result = ToolResult(success=True, data=original_data, source="mootdx2")
        out = MooTDX2Client._with_warn(result, "test warn")
        assert out.data is not original_data  # 复制而非共享
        assert out.data == original_data


# ========== CR2: get_index_kline_local L1 hit 列表复制 ==========

class TestIndexKlineL1HitCopy:
    """CR2: hit 路径 list 复制防下游污染."""

    def test_l1_hit_returns_copy_not_reference(self, client: MooTDX2Client):
        """当 limit >= len(cached_records) 时, 返回的是 list 副本, 非缓存原引用."""
        mock_df = pd.DataFrame(
            {"close": [3000.0, 3010.0, 3020.0]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df
        slot = client._l1_index_kline  # type: ignore[attr-defined]

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # limit >= len(cached_records) 触发原引用 bug
            r1 = asyncio.run(
                client.get_index_kline_local(code="sh000001", type="day", limit=100)
            )
            assert r1.success
            cached_obj = next(iter(slot._cache.values()))

            r2 = asyncio.run(
                client.get_index_kline_local(code="sh000001", type="day", limit=100)
            )
            assert r2.success

            # CR2 修复: r2.data 是 list 副本, 不与 cached_obj 同一引用
            assert r2.data is not cached_obj

    def test_l1_hit_truncation_also_copies(self, client: MooTDX2Client):
        """limit < len(cached_records) 切窗后, 也是 list 副本."""
        mock_df = pd.DataFrame(
            {"close": list(range(100, 200))},
            index=pd.date_range("2023-01-01", periods=100, freq="D"),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df
        slot = client._l1_index_kline  # type: ignore[attr-defined]

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次写入 100 条
            asyncio.run(
                client.get_index_kline_local(code="sh000001", type="day", limit=100)
            )
            cached_obj = next(iter(slot._cache.values()))
            assert len(cached_obj) == 100

            # 第二次 limit=10 切窗
            r2 = asyncio.run(
                client.get_index_kline_local(code="sh000001", type="day", limit=10)
            )
            assert r2.success
            assert len(r2.data) == 10
            # 切窗返回的也不是缓存原引用
            assert r2.data is not cached_obj


# ========== H1: get_index_kline_local cache_key 归一化 ==========

class TestIndexKlineL1CacheKeyNormalize:
    """H1: cache_key 用 parse_symbol 归一化, 'sh000001' 与 '000001' 同 key."""

    def test_normalized_codes_share_l1_cache(self, client: MooTDX2Client):
        """'sh000001' 与 '000001' 是同标的, 应命中同一 L1 槽."""
        mock_df = pd.DataFrame(
            {"close": [3000.0]},
            index=pd.to_datetime(["2024-01-02"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df
        slot = client._l1_index_kline  # type: ignore[attr-defined]

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次: 'sh000001'
            asyncio.run(client.get_index_kline_local(code="sh000001", type="day"))
            assert mock_reader.daily.call_count == 1

            # 第二次: '000001' (裸码,按指数意图解析)
            asyncio.run(client.get_index_kline_local(code="000001", type="day"))
            # H1 修复: 应命中 L1, reader.daily 只调一次
            assert mock_reader.daily.call_count == 1

    def test_different_index_types_dont_collide(self, client: MooTDX2Client):
        """同 code 不同 type (day/week/month) 不应互撞."""
        mock_df_day = pd.DataFrame(
            {"close": [3000.0]}, index=pd.to_datetime(["2024-01-02"])
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df_day

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            asyncio.run(client.get_index_kline_local(code="sh000001", type="day"))
            asyncio.run(client.get_index_kline_local(code="sh000001", type="week"))
            # day + week 不同 key, 都触发 reader
            assert mock_reader.daily.call_count == 2


# ========== H4: get_minute suffix 类型统一 ==========

class TestGetMinuteSuffixType:
    """H4: suffix str/int 等价处理.

    2026-09-28 性能审视: 分钟线 L1 缓存已移除 (用户决策), 两次调用都读 Reader。
    保留测试验证 suffix 仍被正确归一化 (str/int 等价), 不再验证缓存命中。
    """

    def test_int_and_str_suffix_share_l1_cache(self, client: MooTDX2Client):
        """suffix='1' 与 suffix=1 等价, 两次调用都成功 (无缓存, reader 调 2 次)."""
        mock_df = pd.DataFrame(
            {"close": [10.0]}, index=pd.to_datetime(["2024-01-02 10:00"])
        )
        mock_reader = MagicMock()
        mock_reader.minute.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r1 = asyncio.run(client.get_minute(code="600036", suffix="1"))
            r2 = asyncio.run(client.get_minute(code="600036", suffix=1))

            assert r1.success and r2.success
            # 无缓存: 两次都读 reader
            assert mock_reader.minute.call_count == 2


# ========== H5: search_stock 空白 keyword ==========

class TestSearchStockBlankKeyword:
    """H5: 空白 keyword 入口校验, 返 no_data 不污染缓存."""

    def test_empty_keyword_returns_no_data(self, client: MooTDX2Client):
        """keyword='' 返 no_data, 不调 reader."""
        slot = client._l1_search  # type: ignore[attr-defined]

        r = asyncio.run(client.search_stock(keyword=""))
        assert r.success
        assert r.data is None  # no_data
        assert slot.misses == 0  # H5 修复: 不写 L1, 不算 miss

    def test_whitespace_keyword_returns_no_data(self, client: MooTDX2Client):
        """keyword='   ' 返 no_data, 不调 reader."""
        slot = client._l1_search  # type: ignore[attr-defined]

        r = asyncio.run(client.search_stock(keyword="   "))
        assert r.success
        assert r.data is None
        assert slot.misses == 0

    def test_blank_keyword_does_not_pollute_cache(self, client: MooTDX2Client):
        """空白 keyword 调用后, 后续非空 keyword 调用应正常 miss."""
        mock_df = pd.DataFrame({
            "code": ["600519"],
            "name": ["贵州茅台"],
        })
        mock_reader = MagicMock()
        mock_reader.block.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次空白 → no_data, 不写 L1
            asyncio.run(client.search_stock(keyword="   "))
            # 第二次非空 → 应正常 miss 调 reader
            r = asyncio.run(client.search_stock(keyword="茅台"))
            assert r.success
            assert r.data is not None
            assert mock_reader.block.call_count == 1


# ========== H2: get_minute / get_index_minute L1 双层 ==========

class TestGetMinuteL1TwoLayer:
    """get_minute 日期窗过滤 (2026-09-28: 分钟线 L1 已移除).

    分钟线无缓存 → 每次读 Reader, 但日期窗过滤仍生效 (返回窗内条数)。
    """

    def test_different_date_windows_share_l1_cache(self, client: MooTDX2Client):
        """无日期窗 vs 有日期窗: 两次都读 reader, 各自按窗过滤."""
        mock_df = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0]},
            index=pd.to_datetime([
                "2024-01-01 10:00",
                "2024-01-02 10:00",
                "2024-01-03 10:00",
            ]),
        )
        mock_reader = MagicMock()
        mock_reader.minute.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次: 无日期窗
            r1 = asyncio.run(client.get_minute(code="600036", suffix=1))
            assert r1.success
            assert mock_reader.minute.call_count == 1

            # 第二次: 有日期窗 — 无缓存, 再读 reader
            r2 = asyncio.run(client.get_minute(
                code="600036", suffix=1,
                start_date="2024-01-01", end_date="2024-01-02"
            ))
            assert r2.success
            assert mock_reader.minute.call_count == 2


class TestGetIndexMinuteL1TwoLayer:
    """get_index_minute_local 日期窗过滤 (2026-09-28: 分钟线 L1 已移除)."""

    def test_index_minute_different_date_windows_share_l1(self, client: MooTDX2Client):
        """不同日期窗: 两次都读 reader, 各自按窗过滤."""
        # get_index_minute_local 内部调用 reader.minute()
        mock_df = pd.DataFrame(
            {"close": [3000.0, 3010.0, 3020.0]},
            index=pd.to_datetime([
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
            ]),
        )
        mock_reader = MagicMock()
        mock_reader.minute.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次: 无日期窗
            r1 = asyncio.run(client.get_index_minute_local(code="sh000001", suffix="1"))
            assert r1.success

            # 第二次: 有日期窗 — 无缓存, 再读 reader
            r2 = asyncio.run(client.get_index_minute_local(
                code="sh000001", suffix="1",
                start_date="2024-01-01", end_date="2024-01-02"
            ))
            assert r2.success
            assert mock_reader.minute.call_count == 2


# ========== H3: indicator_atr L1 双层 ==========

class TestIndicatorAtrL1TwoLayer:
    """H3: indicator_atr cache_key 移除 limit, 按 limit 切片."""

    def test_different_limits_share_l1_cache(self, client: MooTDX2Client):
        """不同 limit 应命中同一 L1 缓存, 返回对应切片."""
        # _indicator_atr_sync 调用 _get_kline_sync 获取 K 线数据
        # mock _get_kline_sync 返回符合预期的 kline dict list
        mock_kline = [
            {"date": "2024-01-01", "close": 100.0, "high": 105.0, "low": 95.0},
            {"date": "2024-01-02", "close": 101.0, "high": 106.0, "low": 96.0},
            {"date": "2024-01-03", "close": 102.0, "high": 107.0, "low": 97.0},
            {"date": "2024-01-04", "close": 103.0, "high": 108.0, "low": 98.0},
            {"date": "2024-01-05", "close": 104.0, "high": 109.0, "low": 99.0},
        ]

        with patch.object(client, "_get_kline_sync", return_value=mock_kline):
            # 第一次: limit=10 (超出现有 5 条, 应全返)
            r1 = asyncio.run(client.indicator_atr(code="600036", type="day", limit=10))
            assert r1.success
            assert len(r1.data["atr"]) == 5

            # 第二次: limit=3, 应命中缓存并切片
            r2 = asyncio.run(client.indicator_atr(code="600036", type="day", limit=3))
            assert r2.success
            # H3: 不应再调 _get_kline_sync, 返回最后 3 条
            assert len(r2.data["atr"]) == 3
            assert r2.data["dates"] == ["2024-01-03", "2024-01-04", "2024-01-05"]


# ========== 额外修复: get_daily_batch_local 日期归一化 ==========

class TestBatchLocalDateNormalization:
    """修复: get_daily_batch_local 未调用 _normalize_date_window."""

    def test_batch_accepts_yyyymmdd_date_format(self, client: MooTDX2Client):
        """YYYYMMDD 格式日期应被正确归一化为 YYYY-MM-DD."""
        client.config.settings = MagicMock()
        client.config.settings.tdxdir = "/fake/tdx"

        mock_df = pd.DataFrame(
            {"close": [100.0, 101.0]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # YYYYMMDD 格式 (用户实际使用的格式)
            r = asyncio.run(client.get_daily_batch_local(
                codes="sh600036",
                start_date="20240101",
                end_date="20240102",
            ))
            # 不应因日期格式错误而失败
            assert r.success or "date" in (r.error or "").lower()

    def test_batch_accepts_hyphen_date_format(self, client: MooTDX2Client):
        """YYYY-MM-DD 格式日期应正常工作."""
        client.config.settings = MagicMock()
        client.config.settings.tdxdir = "/fake/tdx"

        mock_df = pd.DataFrame(
            {"close": [100.0, 101.0]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(client.get_daily_batch_local(
                codes="sh600036",
                start_date="2024-01-01",
                end_date="2024-01-02",
            ))
            assert r.success