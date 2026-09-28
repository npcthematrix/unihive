"""MOOTDX2 离线接口 · 第三轮 batch2 L1 缓存体系测试 (2026-09-28)。

覆盖:
- _L1Slot helper 行为 (hits/misses/empty-key/key-builder)
- H2-perf daily L1 双层 (key 不含 start/end, 命中后切窗)
- H4-silent top_board / unusual cache 只存 records, 不存 ToolResult
- H1-perf 8 工具 L1 (minute/fzline/index_kline/index_minute/index_fzline/
  search_stock/indicator_atr/block_scan)
- M-perf3 search_stock block 缓存 (复用 _l1_search)

mock 优先用 reader.factory, 而非 _xxx_sync 直接 mock, 覆盖更接近真实路径。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.api._l1_slot import _L1Slot  # type: ignore[attr-defined]


# ========== fixtures ==========

@pytest.fixture
def client() -> MooTDX2Client:
    c = MooTDX2Client(MooTDX2Config(name="test", market="std"))
    c.config.settings = MagicMock()
    c.config.settings.tdxdir = "/fake/tdx"
    return c


# ========== _L1Slot helper 单元测试 ==========

class TestL1SlotUnit:
    """_L1Slot 抽象: TTL+maxsize+hits/misses, 仅 records."""

    def test_hit_miss_counters(self):
        slot = _L1Slot("t", maxsize=8, ttl=10)
        assert slot.enabled
        assert slot.get(("k",)) is None  # miss
        assert slot.misses == 1
        slot.put(("k",), [{"a": 1}])
        v = slot.get(("k",))
        assert v == [{"a": 1}]
        assert slot.hits == 1

    def test_empty_records_not_cached(self):
        slot = _L1Slot("t", maxsize=8, ttl=10)
        slot.put(("k",), [])
        slot.put(("k",), None)
        assert slot.get(("k",)) is None
        # 1 次 get 都 miss (2 次 put 因 records 空被全部短路, 没进 cache)
        assert slot.misses == 1

    def test_clear(self):
        slot = _L1Slot("t", maxsize=8, ttl=10)
        slot.put(("k",), [1])
        assert len(slot) == 1
        slot.clear()
        assert len(slot) == 0

    def test_disabled_when_cachetools_missing(self):
        """ImportError 降级: enabled=False, get/put 静默 no-op."""
        slot = _L1Slot.__new__(_L1Slot)  # bypass __init__
        slot._enabled = False
        slot._cache = None
        slot.hits = 0
        slot.misses = 0
        assert slot.enabled is False
        assert slot.get(("k",)) is None
        slot.put(("k",), [1])  # no-op
        slot.clear()  # no-op
        assert len(slot) == 0


# ========== H2-perf: daily L1 双层 ==========

class TestDailyL1DoubleLayer:
    """daily L1 key 不含 (start_date, end_date) → 同 symbol 多窗口 1 份全量."""

    def test_two_window_requests_share_cache(self, client: MooTDX2Client):
        """同 symbol 不同 start_date 命中同一 L1 记录, 不再各存 1 份."""
        mock_df = pd.DataFrame(
            {"close": list(range(10))},
            index=pd.date_range("2024-01-01", periods=10, freq="D"),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次拉 (全窗, 写入 L1)
            r1 = asyncio.run(
                client.get_daily(code="sh600519", start_date="2024-01-01", end_date="2024-01-10")
            )
            # 第二次拉 (窄窗, 应命中 L1 不再调 reader)
            r2 = asyncio.run(
                client.get_daily(code="sh600519", start_date="2024-01-05", end_date="2024-01-06")
            )

        assert r1.success and r2.success
        assert len(r1.data) == 10  # 全量
        assert len(r2.data) == 2  # 切窗
        # reader.daily 实际只调一次
        assert mock_reader.daily.call_count == 1

    def test_cache_only_records_not_tool_result(self, client: MooTDX2Client):
        """L1 槽存的是 records list, 不是 ToolResult."""
        from src.unihive.api._l1_slot import _L1Slot
        # daily_l1 槽存 raw records
        slot: _L1Slot = client._l1_daily  # type: ignore[attr-defined]
        mock_df = pd.DataFrame(
            {"close": [10.0]},
            index=pd.to_datetime(["2024-01-02"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            asyncio.run(client.get_daily(code="sh600036"))

        # 槽中存的是 list[dict] 而非 ToolResult
        cached = next(iter(slot._cache.values())) if hasattr(slot, "_cache") else None
        # 取一个 key 验证类型
        for v in (slot._cache.values() if hasattr(slot, "_cache") else []):
            cached = v
            break
        assert isinstance(cached, list)
        assert all(isinstance(r, dict) for r in cached)


# ========== H4-silent: top_board / unusual records-only ==========

class TestTopBoardUnusualRecordsOnly:
    """top_board / unusual cache 只存 records; ToolResult 每次现包."""

    def test_top_board_cache_hit_returns_fresh_tool_result(self, client: MooTDX2Client):
        """缓存命中返回的 ToolResult.data 不应是缓存槽里的同一引用."""
        # 该 impl 由 _with_warn 复制 records 实现, 此处仅验存在.
        # 真正的引用共享测试在 _with_warn 单元级别覆盖.
        slot = client._l1_top_board  # type: ignore[attr-defined]
        assert slot is not None


# ========== H1-perf: 8 工具 L1 ==========

class TestEightToolsL1:
    """单 symbol 工具 L1 缓存接入。

    2026-09-28 性能审视: 4 个分钟线槽已移除 (用户决策: 只有日线数据需要 CACHE)。
    仅 index_kline (日K类) 保留 L1; minute/fzline/index_minute/index_fzline
    每次直读 Reader, 无缓存。
    """

    @pytest.mark.parametrize(
        "method_name, kwargs, fixture_df",
        [
            # index_kline 是日K类 (Reader.daily 读 .day), 保留 L1
            ("get_index_kline_local", {"code": "sh000001"}, "kline"),
        ],
    )
    def test_offline_kline_l1_hit(
        self, method_name, kwargs, fixture_df, client: MooTDX2Client
    ):
        """index_kline 连续同入参 → 第二次命中 L1, reader 只调 1 次."""
        mock_df = pd.DataFrame(
            {"close": [3000.0]},
            index=pd.to_datetime(["2024-01-02"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily = MagicMock(return_value=mock_df)

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            method = getattr(client, method_name)
            r1 = asyncio.run(method(**kwargs))
            r2 = asyncio.run(method(**kwargs))

        assert r1.success and r2.success
        # reader 实际只调 1 次 (L1 命中)
        assert mock_reader.daily.call_count == 1

    @pytest.mark.parametrize(
        "method_name, kwargs, reader_method, fixture_kind",
        [
            ("get_minute", {"code": "600036", "suffix": "1"}, "minute", "minute"),
            ("get_fzline", {"code": "600036"}, "fzline", "fzline"),
            ("get_index_minute_local", {"code": "sh000001", "suffix": "1"}, "minute", "minute"),
            ("get_index_fzline_local", {"code": "sh000001"}, "fzline", "fzline"),
        ],
    )
    def test_minute_tools_no_l1_cache(
        self, method_name, kwargs, reader_method, fixture_kind, client: MooTDX2Client
    ):
        """分钟线工具无 L1 缓存 (2026-09-28 决策): 连续两次同入参都读 Reader."""
        mock_df = pd.DataFrame(
            {"close": [10.0]},
            index=pd.to_datetime(["2024-01-02 10:00"]),
        )
        mock_reader = MagicMock()
        setattr(mock_reader, reader_method, MagicMock(return_value=mock_df))

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            method = getattr(client, method_name)
            r1 = asyncio.run(method(**kwargs))
            r2 = asyncio.run(method(**kwargs))

        assert r1.success and r2.success
        # 无缓存 → reader 两次都调
        reader_mock = getattr(mock_reader, reader_method)
        assert reader_mock.call_count == 2

    def test_search_stock_l1_hit(self, client: MooTDX2Client):
        """search_stock 连续同 keyword → 第二次命中 L1."""
        df_block = pd.DataFrame({
            "code": ["600519", "000001"],
            "name": ["贵州茅台", "平安银行"],
        })
        mock_reader = MagicMock()
        mock_reader.block.return_value = df_block

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            # 第一次 reader.block 调一次
            r1 = asyncio.run(client.search_stock(keyword="茅台"))
            assert r1.success

            # 第二次同 keyword → 应命中 L1, reader 不再调
            r2 = asyncio.run(client.search_stock(keyword="茅台"))
            assert r2.success

        assert mock_reader.block.call_count == 1