"""get_daily L1 缓存可观测性 + single-flight 测试。

覆盖: 命中路径 / 首次 miss 单飞 / 并发雪崩去重 / adjust=qfq 旁路 /
leader 异常时 waiter 共享异常。
"""
import asyncio
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config


@pytest.fixture
def client():
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


@pytest.fixture
def mock_setup(client):
    """client + mock Reader + 返回 2 条记录的 DataFrame."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_df = pd.DataFrame(
        {"close": [10.0, 11.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    mock_reader = MagicMock()
    mock_reader.daily.return_value = mock_df
    return client, mock_reader, mock_df


def test_hit_path_does_not_call_reader_again(mock_setup):
    """两次相同请求, 第 2 次 _get_daily_sync 调用计数 = 0 (走 L1)."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r1 = asyncio.run(client.get_daily("600036"))
        assert r1.success is True
        assert mock_reader.daily.call_count == 1

        r2 = asyncio.run(client.get_daily("600036"))
        assert r2.success is True
        # 第 2 次命中 L1, 不再调 reader
        assert mock_reader.daily.call_count == 1
        assert client._daily_l1_hits == 1
        assert client._daily_l1_misses == 1


def test_concurrent_miss_single_flight(mock_setup):
    """5 个并发同 symbol 请求, reader.daily 仅被调 1 次, 5 个结果一致."""
    client, mock_reader, _ = mock_setup

    async def run_concurrent():
        return await asyncio.gather(*[client.get_daily("600036") for _ in range(5)])

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        results = asyncio.run(run_concurrent())

    assert all(r.success for r in results)
    assert mock_reader.daily.call_count == 1
    # 5 个返回同一 records 列表引用 (single-flight 共享结果)
    assert all(r.data is results[0].data for r in results)


def test_first_miss_writes_to_l1(mock_setup):
    """首次 miss 后, _daily_l1 有 1 条; 同 key 再请求命中."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036"))
    assert len(client._daily_l1) == 1
    # 缓存 key 形状 = (reader_symbol, "none")
    key = next(iter(client._daily_l1.keys()))
    assert key == ("600036", "none")


def test_qfq_bypasses_l1_and_inflight(mock_setup):
    """adjust=qfq 不进 L1 也不进 inflight, 两次调用 reader 两次."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036", adjust="qfq"))
        asyncio.run(client.get_daily("600036", adjust="qfq"))
    assert mock_reader.daily.call_count == 2
    assert len(client._daily_l1) == 0
    assert len(client._daily_l1_inflight) == 0


def test_leader_exception_shared_by_waiters(mock_setup):
    """leader 抛异常, waiter 拿到同一异常 (走 get_daily 现有 error 路径)."""
    client, mock_reader, _ = mock_setup
    mock_reader.daily.side_effect = RuntimeError("boom")

    async def run_concurrent():
        return await asyncio.gather(
            *[client.get_daily("600036") for _ in range(3)],
            return_exceptions=True,
        )

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        results = asyncio.run(run_concurrent())

    # 3 个结果都是 ToolResult (失败), 不是裸 exception
    from src.unihive.api.upstream_client import ToolResult
    assert all(isinstance(r, ToolResult) for r in results)
    assert all(not r.success for r in results)
    # reader 只被调 1 次
    assert mock_reader.daily.call_count == 1
    # inflight 已清理
    assert len(client._daily_l1_inflight) == 0
