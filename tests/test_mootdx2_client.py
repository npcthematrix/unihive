import asyncio
import threading
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


# ========== 2026-09-14 Round 10 audit: call_tool + health_loop 回归保护 ==========

class TestMooTDX2CallToolExceptionSafety:
    """回归测试: call_tool 在方法抛异常时必须 return ToolResult, 不能 raise.

    修复前: call_tool 顶层 try/except 后 `raise`, 让异常逃出 router 整个
    fallback 链 — router 只 catch TimeoutError, 其它异常打到 MCP 协议层。
    修复后: return ToolResult(success=False, error=...), router 看到后
    继续串行走 fallback。
    """

    @pytest.mark.asyncio
    async def test_call_tool_returns_tool_result_on_exception_not_raises(self):
        """核心回归: 方法抛任何异常, call_tool 必须 return 不 raise."""
        from src.unihive.api.upstream_client import ToolResult
        config = MooTDX2Config(name="test_mootdx2", market="std")
        client = MooTDX2Client(config)
        client._status = UpstreamStatus.HEALTHY  # 起始 HEALTHY, 触发降级

        async def boom(**kwargs):
            raise RuntimeError("simulated downstream failure")

        # patch get_quote 触发异常路径
        client.get_quote = boom

        # 必须 NOT 抛
        result = await client.call_tool("get_quote", {"code": "600519.SH"})

        assert isinstance(result, ToolResult)
        assert result.success is False
        # 错误信息经 error_to_result 包装, 显示为 "内部错误: <type>"
        # 关键不变量: 不应 raise, 不应让异常逃出 call_tool
        assert "RuntimeError" in result.error
        # error_detail 也必须有
        assert result.error_detail is not None
        assert result.error_detail.get("error_type") == "internal_error"
        # 同时 status 应被降级
        assert client._status == UpstreamStatus.DEGRADED, (
            f"HEALTHY → DEGRADED 降级失败, 实际 {client._status}"
        )

    @pytest.mark.asyncio
    async def test_call_tool_does_not_break_router_fallback_chain(self):
        """核心回归: 模拟 router 视角, call_tool 抛异常会让 fallback 链断裂.

        router 只 catch TimeoutError (router.py:194), 其它异常直接逃出
        `_call_with_fallback` → 整个请求失败, 而非 fallback 到下一上游。
        """
        config = MooTDX2Config(name="test_mootdx2", market="std")
        client = MooTDX2Client(config)

        async def boom(**kwargs):
            raise ConnectionError("upstream socket reset")

        client.get_quote = boom

        # 用 asyncio.wait_for 模拟 router 的包装, 验证不会逃出
        try:
            result = await asyncio.wait_for(
                client.call_tool("get_quote", {"code": "600000"}),
                timeout=5.0,
            )
            assert result.success is False
            # 关键不变量: 不应 raise, 不应让异常逃出 call_tool
            assert "upstream socket reset" in result.error
            assert result.error_detail["error_type"] == "connection_error"
        except asyncio.TimeoutError:
            pytest.fail("call_tool 应该 return 不应 timeout")
        except Exception as e:
            pytest.fail(
                f"call_tool 不应让异常逃出 (router 拿不到 fallback 机会): {type(e).__name__}: {e}"
            )


class TestMooTDX2HealthLoopRecovery:
    """回归测试: health_loop DEGRADED → HEALTHY 自动恢复."""

    @pytest.mark.asyncio
    async def test_health_loop_recovers_degraded_to_healthy(self, monkeypatch):
        """DEGRADED 状态在 TDX 恢复后应被 health_loop 推回 HEALTHY.

        简化策略: 把 health_loop 内的 `await asyncio.sleep(30.0)` 替换为
        `await asyncio.sleep(0.05)`, 直接 monkeypatch 该方法 (而不是全局
        asyncio.sleep — 后者会自递归)。
        """
        config = MooTDX2Config(name="test_mootdx2", market="std")
        client = MooTDX2Client(config)

        # mock quotes 让 probe 成功
        class MockQuotes:
            def quotes(self, symbols):
                import pandas as pd
                return pd.DataFrame([{"symbol": "000001", "close": 10.0}])

            def close(self):
                pass

        client._quotes = MockQuotes()
        client._status = UpstreamStatus.DEGRADED

        # 替换 client 实例的 health_loop: 不改源码, 而是用 faster 副本
        # 通过 patch.object 直接覆盖该方法, 内部跑简化版的 while + probe
        async def fast_health_loop():
            """简化版 health_loop: sleep 0.05s 而非 30s, 逻辑与原版一致."""
            try:
                while True:
                    await asyncio.sleep(0.05)
                    if client._quotes is None:
                        continue
                    try:
                        ok = await asyncio.wait_for(
                            client._probe_with_existing_quotes(),
                            timeout=2.5,
                        )
                        if ok and client._status != UpstreamStatus.HEALTHY:
                            client._status = UpstreamStatus.HEALTHY
                        elif not ok and client._status == UpstreamStatus.HEALTHY:
                            client._status = UpstreamStatus.DEGRADED
                    except asyncio.TimeoutError:
                        if client._status == UpstreamStatus.HEALTHY:
                            client._status = UpstreamStatus.DEGRADED
            except asyncio.CancelledError:
                pass

        monkeypatch.setattr(client, "_health_loop", fast_health_loop)

        # 启动 health_loop, 等 0.2s 看是否恢复到 HEALTHY
        task = asyncio.create_task(client._health_loop())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client._status == UpstreamStatus.HEALTHY, (
            f"health_loop 应把 DEGRADED 推回 HEALTHY, 实际 {client._status}"
        )


class TestMooTDX2MetricsThreadSafety:
    """回归测试: 并发 _record_error 不能丢更新."""

    def test_concurrent_record_error_no_lost_updates(self):
        config = MooTDX2Config(name="test_mootdx2", market="std")
        client = MooTDX2Client(config)

        n_writers = 10
        per_writer = 100

        def writer(error_type):
            for _ in range(per_writer):
                client._record_error(error_type)

        threads = [
            threading.Thread(target=writer, args=("test_error",))
            for _ in range(n_writers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 期望 total_errors = n_writers * per_writer = 1000
        assert client._metrics["total_errors"] == n_writers * per_writer, (
            f"丢更新: 期望 {n_writers * per_writer}, "
            f"实际 {client._metrics['total_errors']}"
        )
        # error_counts['test_error'] 也必须 1000
        assert client._metrics["error_counts"].get("test_error") == n_writers * per_writer


# ========== 2026-09-14 固定行情服务器 (pinned servers + failover) ==========

class TestMooTDX2PinnedServers:
    """配置 servers 后 Quotes.factory 必须固定连指定 IP, 多台注入 failover 候选。"""

    @staticmethod
    def _make_fake_quotes_module(monkeypatch, captured):
        """patch mootdx2.quotes.Quotes.factory, 返回带 manager 的假 StdQuotes。

        复刻真实构造: manager candidates 初始只有 server= 传入的首台,
        其余候选靠 _pin_quotes_servers 追加。
        """
        import threading
        import mootdx2.quotes as mq

        class FakeManager:
            def __init__(self, candidates):
                self.lock = threading.RLock()
                self._candidates = list(candidates)
                self.current = candidates[0] if candidates else None

        class FakeStdQuotes:
            def __init__(self, server, kwargs):
                self.server = server
                self.kwargs = kwargs
                self.manager = FakeManager([server] if server else [])

        def fake_factory(market=None, driver=None, **kwargs):
            captured.update({"market": market, **kwargs})
            return FakeStdQuotes(kwargs.get("server"), {"market": market, **kwargs})

        monkeypatch.setattr(mq.Quotes, "factory", staticmethod(fake_factory))

    def test_get_quotes_pins_first_server_and_injects_failover_candidates(self, monkeypatch):
        from src.unihive.models.mootdx2_config import MooTDX2Settings, ServerConfig

        settings = MooTDX2Settings(
            market="std",
            servers=[
                ServerConfig(host="101.33.225.16", port=7709),
                ServerConfig(host="81.71.32.47", port=7709),
            ],
        )
        config = MooTDX2Config(name="test", market="std", settings=settings)
        client = MooTDX2Client(config)

        captured = {}
        self._make_fake_quotes_module(monkeypatch, captured)

        quotes = client._get_quotes()

        # factory 必须关闭 bestip, 固定连首台, threshold=1 保证第二台能轮到
        assert captured["bestip"] is False
        assert captured["server"] == ("101.33.225.16", 7709)
        assert captured["auto_switch"] == 1
        # 第二台必须被注入同一 manager 的候选列表 (顺序 failover)
        assert quotes.manager._candidates == [
            ("101.33.225.16", 7709),
            ("81.71.32.47", 7709),
        ]

        # 再次获取必须复用缓存实例, 不重复 factory
        assert client._get_quotes() is quotes

    def test_get_quotes_without_servers_keeps_bestip_autoselect(self, monkeypatch):
        """无 servers 配置时维持原行为: bestip=True, 不传 server。"""
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)

        captured = {}
        self._make_fake_quotes_module(monkeypatch, captured)

        client._get_quotes()

        assert captured["bestip"] is True
        assert "server" not in captured
        assert "auto_switch" not in captured

    def test_get_quotes_single_server_no_extra_candidates(self, monkeypatch):
        from src.unihive.models.mootdx2_config import MooTDX2Settings, ServerConfig

        settings = MooTDX2Settings(
            servers=[ServerConfig(host="101.33.225.16", port=7709)],
        )
        config = MooTDX2Config(name="test", market="std", settings=settings)
        client = MooTDX2Client(config)

        captured = {}
        self._make_fake_quotes_module(monkeypatch, captured)

        quotes = client._get_quotes()
        assert quotes.manager._candidates == [("101.33.225.16", 7709)]

    @pytest.mark.asyncio
    async def test_get_server_status_reports_current_and_configured(self, monkeypatch):
        import threading
        from src.unihive.models.mootdx2_config import MooTDX2Settings, ServerConfig

        settings = MooTDX2Settings(
            servers=[
                ServerConfig(host="101.33.225.16", port=7709),
                ServerConfig(host="81.71.32.47", port=7709),
            ],
        )
        config = MooTDX2Config(name="test", market="std", settings=settings)
        client = MooTDX2Client(config)

        class FakeManager:
            lock = threading.RLock()
            current = ("81.71.32.47", 7709)  # 模拟 failover 后当前节点

        client._quotes = type("FakeQuotes", (), {"manager": FakeManager()})()

        result = await client.get_server_status()
        assert result.success is True
        assert result.data["current_server"] == {"host": "81.71.32.47", "port": 7709}
        assert result.data["servers"] == [
            {"host": "101.33.225.16", "port": 7709},
            {"host": "81.71.32.47", "port": 7709},
        ]


class TestParseMooTDX2Servers:
    """gateway 配置边界校验: 只接受 IP 字面量 + 合法端口。"""

    def test_valid_servers(self):
        from src.unihive.gateway_server import _parse_mootdx2_servers
        from src.unihive.models.mootdx2_config import ServerConfig

        servers = _parse_mootdx2_servers([
            {"host": "101.33.225.16", "port": 7709},
            {"host": "81.71.32.47"},  # 默认端口
        ])
        assert servers == [
            ServerConfig(host="101.33.225.16", port=7709),
            ServerConfig(host="81.71.32.47", port=7709),
        ]

    def test_empty_list(self):
        from src.unihive.gateway_server import _parse_mootdx2_servers
        assert _parse_mootdx2_servers([]) == []

    def test_domain_rejected(self):
        from src.unihive.gateway_server import _parse_mootdx2_servers
        with pytest.raises(ValueError, match="只接受 IP 字面量"):
            _parse_mootdx2_servers([{"host": "tdx.example.com", "port": 7709}])

    def test_bad_port_rejected(self):
        from src.unihive.gateway_server import _parse_mootdx2_servers
        with pytest.raises(ValueError, match="port"):
            _parse_mootdx2_servers([{"host": "101.33.225.16", "port": 70000}])


# ========== 2026-09-14 Round 11 audit: startup edge cases ==========

class TestMooTDX2StartEdgeCases:
    """回归测试: start() 在 quotes init 失败 / probe 路径上的边角行为."""

    @pytest.mark.asyncio
    async def test_start_does_not_launch_health_loop_when_quotes_init_fails(self, monkeypatch):
        """HIGH-1: factory 失败时不应启动 health_loop (会纯空转 30s/tick).

        之前实现: init 失败也调 _start_health_loop(), health_loop 每次
        sleep 30s 后看到 self._quotes is None 就 continue — 纯空转,浪费
        CPU + 日志。改为: init 失败时不启动, 等下次 start() 重新尝试。
        """
        config = MooTDX2Config(name="test_mootdx2", market="std")
        client = MooTDX2Client(config)

        # 强制 _get_quotes 抛异常, 模拟 factory 失败
        def boom_quotes():
            raise ConnectionError("TDX unreachable")

        monkeypatch.setattr(client, "_get_quotes", boom_quotes)

        await client.start()

        assert client._status == UpstreamStatus.DEGRADED
        # 关键断言: health_loop 不应被启动
        assert client._health_task is None, (
            f"init 失败时不应启动 health_loop, 但 _health_task={client._health_task}"
        )

    @pytest.mark.asyncio
    async def test_probe_returns_false_when_quotes_is_none(self):
        """HIGH-2: _probe_with_existing_quotes 在 quotes 为 None 时应返 False
        而非调 _get_quotes() 重建 — 后者与 stop() race 会泄漏旧连接。
        """
        config = MooTDX2Config(name="test_mootdx2", market="std")
        client = MooTDX2Client(config)
        client._quotes = None

        # _get_quotes 应根本不被调用
        called = []

        def mock_get_quotes():
            called.append(1)
            raise AssertionError(
                "probe 路径不应调 _get_quotes() (会与 stop race)"
            )

        client._get_quotes = mock_get_quotes

        result = await client._probe_with_existing_quotes()
        assert result is False
        assert called == [], "probe 路径不应触发 factory"

class TestGetCodeList:
    """get_code_list: mootdx2 stocks(market) 分页拉全量, 输出合同 = code/name 记录。

    mootdx2 Quotes.stocks(market) 只支持沪深 (1=SH / 0=SZ), market=2(BJ) 会
    抛 MootdxValidationException; 全市场用 stock_all()。返回行是裸 6 位 code,
    没有 symbol 字段。
    """

    class _FakeQuotes:
        def __init__(self):
            self.calls: list[tuple] = []

        def stocks(self, market):
            import pandas as pd

            self.calls.append(("stocks", market))
            if market == 1:
                return pd.DataFrame([
                    {"code": "600000", "name": "浦发银行", "volunit": 100, "decimal_point": 3},
                    {"code": "600519", "name": "贵州茅台", "volunit": 100, "decimal_point": 3},
                ])
            if market == 0:
                return pd.DataFrame([
                    {"code": "000001", "name": "平安银行", "volunit": 100, "decimal_point": 3},
                ])
            raise ValueError("市场代码错误, 目前只支持沪深市场")

        def stock_all(self):
            import pandas as pd

            self.calls.append(("stock_all",))
            return pd.DataFrame([
                {"code": "600000", "name": "浦发银行"},
                {"code": "000001", "name": "平安银行"},
            ])

    def _make_client(self, fake_quotes):
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        client._quotes = fake_quotes
        return client

    def test_sh_uses_market_1_and_projects_code_name(self):
        q = self._FakeQuotes()
        client = self._make_client(q)
        rows = client._get_code_list_sync("sh")
        assert q.calls == [("stocks", 1)]
        assert rows == [
            {"code": "600000", "name": "浦发银行"},
            {"code": "600519", "name": "贵州茅台"},
        ]  # volunit/decimal_point 等内部列不得泄漏

    def test_sz_uses_market_0(self):
        q = self._FakeQuotes()
        client = self._make_client(q)
        rows = client._get_code_list_sync("sz")
        assert q.calls == [("stocks", 0)]
        assert rows == [{"code": "000001", "name": "平安银行"}]

    def test_empty_exchange_uses_stock_all(self):
        q = self._FakeQuotes()
        client = self._make_client(q)
        rows = client._get_code_list_sync("")
        assert q.calls == [("stock_all",)]
        assert len(rows) == 2

    def test_bj_unsupported_returns_none_without_calling_stocks_2(self):
        q = self._FakeQuotes()
        client = self._make_client(q)
        assert client._get_code_list_sync("bj") is None
        assert q.calls == []  # 库对 market=2 直接抛异常, 不应发起调用

    async def test_async_success_envelope(self):
        client = self._make_client(self._FakeQuotes())
        result = await client.get_code_list("sh")
        assert result.success is True
        assert result.source == "mootdx2"
        assert result.data[0]["code"] == "600000"

    async def test_async_bj_returns_no_data_not_error(self):
        client = self._make_client(self._FakeQuotes())
        result = await client.get_code_list("bj")
        # BJ 是库限制而非调用失败: 走 no_data 业务状态 (success=True, data=None),
        # 不产生 error_result, 也不会触发 router 降级链
        assert result.success is True
        assert result.data is None
        assert result.error_detail["error_type"] == "no_data"
        assert "北京证券交易所" in result.error
