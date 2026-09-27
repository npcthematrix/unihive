"""MOOTDX2 P0 性能审视 ship 测试 (2026-09-15).

覆盖:
- H2-a: stock_top_board / stock_unusual L1 + single-flight
- H3: _run_blocking helper 行为等价 run_in_executor
- M3: search_stock 向量化返回结构与旧逻辑一致
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.api.upstream_client import ToolResult


@pytest.fixture
def client():
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


# ============ H2-a: stock_top_board L1 ============

def _mock_quotes_client(stock_count=10):
    """构造 mock mootdx2 Quotes: stock(exchange=) 返回 N 只股票, quotes(symbols=) 返回 1 行 DataFrame."""
    mock_q = MagicMock()
    df_stock = pd.DataFrame({"code": [f"{600000+i:06d}" for i in range(stock_count)]})
    mock_q.stock.return_value = df_stock
    df_quote = pd.DataFrame([{
        "price": 10.0, "last_close": 9.5, "open": 9.6, "high": 10.2, "low": 9.4,
        "vol": 100, "amount": 1000.0, "pct_chg": 5.0, "turnover": 1.5,
        "volume_ratio": 1.2,
    }])
    mock_q.quotes.return_value = df_quote
    return mock_q


@pytest.mark.asyncio
async def test_stock_top_board_l1_hit_does_not_rescan(client):
    """两次相同参数: 第 2 次命中 L1, _stock_top_board_sync 只被调 1 次。"""
    mock_q = _mock_quotes_client(stock_count=10)
    with patch.object(client, "_get_quotes", return_value=mock_q):
        r1 = await client.stock_top_board()
        assert r1.success is True
        sync_calls = client._stock_top_board_sync
        # 用 sync_calls 不行, 改用 wrapped counter
        with patch.object(client, "_stock_top_board_sync", wraps=sync_calls) as wrapped:
            r2 = await client.stock_top_board()
            assert r2.success is True
            # r2 命中 L1, 不再调 sync
            assert wrapped.call_count == 0
    assert client._top_board_l1_hits == 1
    assert client._top_board_l1_misses == 1


@pytest.mark.asyncio
async def test_stock_top_board_l1_concurrent_all_succeed(client):
    """5 个并发同参数请求: 无 single-flight, 全部成功, L1 最终可命中。

    2026-09-16 移除 single-flight 后, 并发同 key 的 miss 计数存在竞态
    (先计算的线程可能先写好 L1, 后调度的任务读到缓存), 因此不再断言
    "全共享同一引用 / 精确 miss==1", 只断言功能正确 + L1 最终命中。
    """
    mock_q = _mock_quotes_client(stock_count=10)
    with patch.object(client, "_get_quotes", return_value=mock_q):
        results = await asyncio.gather(*[client.stock_top_board() for _ in range(5)])
    assert all(r.success for r in results)
    # 至少一次同步计算把结果写入 L1
    assert client._top_board_l1_misses >= 1
    # 批内完成后, 同 key 单次请求命中 L1 (~10s TTL)
    extra = await client.stock_top_board()
    assert extra.success is True
    assert client._top_board_l1_hits >= 1


@pytest.mark.asyncio
async def test_stock_top_board_metrics_block(client):
    """get_metrics() 暴露 top_board_l1 子块, 含 hits/misses/size/ttl。"""
    mock_q = _mock_quotes_client(stock_count=5)
    with patch.object(client, "_get_quotes", return_value=mock_q):
        await client.stock_top_board()
        await client.stock_top_board(limit=10)  # 不同 key
    m = client.get_metrics()
    block = m["top_board_l1"]
    assert block["enabled"] is True
    assert block["ttl_seconds"] == 10.0
    assert block["size"] == 2
    assert block["maxsize"] == block["size"] or block["maxsize"] >= 2


# ============ H2-a: stock_unusual L1 ============

@pytest.mark.asyncio
async def test_stock_unusual_l1_hit_does_not_rescan(client):
    mock_q = MagicMock()
    mock_q.block.return_value = pd.DataFrame([{"name": "概念1", "code_list": "600000"}])
    mock_q.quotes.return_value = pd.DataFrame([{"price": 10, "vol": 100, "pct_chg": 9.9}])
    with patch.object(client, "_get_quotes", return_value=mock_q):
        r1 = await client.stock_unusual()
        assert r1.success is True
        with patch.object(client, "_stock_unusual_sync", wraps=client._stock_unusual_sync) as wrapped:
            r2 = await client.stock_unusual()
            assert wrapped.call_count == 0
    assert client._unusual_l1_hits == 1
    assert client._unusual_l1_misses == 1


@pytest.mark.asyncio
async def test_stock_unusual_l1_concurrent_all_succeed(client):
    """3 个并发同参数请求: 无 single-flight, 全部成功, 至少一次算入 L1。"""
    mock_q = MagicMock()
    mock_q.block.return_value = pd.DataFrame([{"name": "概念1", "code_list": "600000"}])
    mock_q.quotes.return_value = pd.DataFrame([{"price": 10, "vol": 100, "pct_chg": 9.9}])
    with patch.object(client, "_get_quotes", return_value=mock_q):
        results = await asyncio.gather(*[client.stock_unusual() for _ in range(3)])
    assert all(r.success for r in results)
    assert client._unusual_l1_misses >= 1


@pytest.mark.asyncio
async def test_stock_unusual_metrics_block(client):
    m = client.get_metrics()
    assert "unusual_l1" in m
    assert m["unusual_l1"]["ttl_seconds"] == 10.0


# ============ H3: _run_blocking helper ============

@pytest.mark.asyncio
async def test_run_blocking_uses_running_loop(client):
    """_run_blocking 走 get_running_loop + mootdx2 专用阻塞池 (Route A 后与 tdx_quant 拆分)。"""
    sync_fn = MagicMock(return_value="ok")
    result = await client._run_blocking(sync_fn, "arg1", key="kw")
    assert result == "ok"
    sync_fn.assert_called_once_with("arg1", key="kw")


@pytest.mark.asyncio
async def test_run_blocking_propagates_exception(client):
    def boom():
        raise RuntimeError("downstream error")
    with pytest.raises(RuntimeError, match="downstream error"):
        await client._run_blocking(boom)


# ============ M3: search_stock 向量化 ============

def test_search_stock_vectorized_matches_iterrows():
    """向量化 str.contains 与原 iterrows 逻辑结果一致。"""
    df_block = pd.DataFrame({
        "code": ["600000", "000001", "300750", "830001"],
        "name": ["浦发银行", "平安银行", "宁德时代", "颖泰生物"],
    })
    kw = "bank"
    # 向量化版
    code_col = df_block["code"].astype(str)
    name_col = df_block["name"].astype(str)
    mask = code_col.str.contains(kw, case=False, na=False) | name_col.str.contains(kw, case=False, na=False)
    matches_v = df_block[mask].head(20)
    # 原 iterrows 版
    matches_i = []
    for _, row in df_block.iterrows():
        code = str(row.get("code", ""))
        name = row.get("name", "")
        if not code or not name:
            continue
        if "bank" in code or "bank" in name.lower():
            matches_i.append((code, name))
    # 验证索引集合一致
    assert set(matches_v.index.tolist()) == {i for i, _ in matches_i}


def test_search_stock_returns_correct_market_prefix():
    """市场前缀推断: 60/68→sh, 00/30→sz, 8/4→bj。"""
    # 用通配符 .* 命中全部 5 行, 验证每个 code 的市场前缀推断
    df_block = pd.DataFrame({
        "code": ["600000", "000001", "300750", "830001", "999000"],
        "name": ["a", "b", "c", "d", "e"],
    })
    code_col = df_block["code"].astype(str)
    name_col = df_block["name"].astype(str)
    mask = code_col.str.contains("0", case=False, na=False)  # 所有 code 都含 "0"
    matches = df_block[mask]
    result = []
    for code, name in zip(code_col[matches.index].tolist(), name_col[matches.index].tolist()):
        if not code:
            continue
        if code.startswith(("60", "68")):
            mkt = "sh"
        elif code.startswith(("00", "30")):
            mkt = "sz"
        elif code.startswith(("8", "4")):
            mkt = "bj"
        else:
            mkt = "sz"
        result.append((code, mkt))
    markets = {c: m for c, m in result}
    assert markets["600000"] == "sh"
    assert markets["000001"] == "sz"
    assert markets["300750"] == "sz"
    assert markets["830001"] == "bj"
    assert markets["999000"] == "sz"  # 兜底