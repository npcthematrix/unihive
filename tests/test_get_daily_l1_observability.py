"""get_daily L1 缓存可观测性测试。

覆盖: 命中路径 / miss 写缓存 / 并发 miss（当前契约：信号量限流但不做
single-flight 去重 —— _daily_l1_inflight 已在 59004ea 随"次日 9 点动态
TTL"改造移除）/ adjust=qfq 旁路 / 异常路径 / metrics 字段。
"""
import asyncio
import datetime
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
    """两次相同请求, 第 2 次走 L1, reader 不再被调用."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r1 = asyncio.run(client.get_daily("600036"))
        assert r1.success is True
        assert mock_reader.daily.call_count == 1

        r2 = asyncio.run(client.get_daily("600036"))
        assert r2.success is True
        assert mock_reader.daily.call_count == 1
        assert client._l1_daily.hits == 1
        assert client._l1_daily.misses == 1


def test_concurrent_miss_is_best_effort_dedup(mock_setup):
    """5 个并发同 symbol 请求全部成功；reader 调用次数随时序落在 1..5。

    single-flight（_daily_l1_inflight）已在 59004ea 移除。当前实现里
    L1 缓存在信号量之外、同步检查：先完成的任务写缓存后，尚未开始查
    缓存的任务会直接命中（线程池预热时实测 2 次，冷启动 5 次）。
    这是机会主义去重而非契约 —— 本测试只保证所有调用方成功且不会
    有任务漏网打第 6 次 reader。若未来重新引入 single-flight，
    可收紧为 "reader 仅 1 次 + 结果共享"。
    """
    client, mock_reader, _ = mock_setup

    async def run_concurrent():
        return await asyncio.gather(*[client.get_daily("600036") for _ in range(5)])

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        results = asyncio.run(run_concurrent())

    assert all(r.success for r in results)
    assert 1 <= mock_reader.daily.call_count <= 5


def test_first_miss_writes_to_l1(mock_setup):
    """首次 miss 后, _l1_daily 槽有 1 条; key 为 (date, reader_symbol, adjust)."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036"))
    assert len(client._l1_daily) == 1
    today = datetime.date.today().isoformat()
    key = next(iter(client._l1_daily._cache.keys()))
    assert key == (today, "600036", "none")


def test_index_qfq_forced_none_warn_survives_l1_hit(mock_setup):
    """指数传 qfq：miss 与 L1 命中两次都必须带"强制 none"warn（防提示漂移）。"""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r1 = asyncio.run(client.get_daily("sh000300", adjust="qfq"))
        r2 = asyncio.run(client.get_daily("sh000300", adjust="qfq"))

    assert r1.success and r2.success
    assert client._l1_daily.hits == 1, "指数 qfq 实际按 none 执行，第二次必须命中 L1"
    assert mock_reader.daily.call_count == 1
    assert mock_reader.daily.call_args.kwargs["adjust"] == "none"
    for r in (r1, r2):
        assert "强制为 none" in r.error_detail["warn"]


def test_qfq_bypasses_l1(mock_setup):
    """adjust=qfq 不进 L1, 两次调用 reader 两次."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036", adjust="qfq"))
        asyncio.run(client.get_daily("600036", adjust="qfq"))
    assert mock_reader.daily.call_count == 2
    assert len(client._l1_daily) == 0


def test_reader_exception_returns_failure_toolresult(mock_setup):
    """reader 抛异常时每个并发调用都拿到失败 ToolResult（不吞异常、不共享）."""
    client, mock_reader, _ = mock_setup
    mock_reader.daily.side_effect = RuntimeError("boom")

    async def run_concurrent():
        return await asyncio.gather(
            *[client.get_daily("600036") for _ in range(3)],
            return_exceptions=True,
        )

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        results = asyncio.run(run_concurrent())

    from src.unihive.api.upstream_client import ToolResult
    assert all(isinstance(r, ToolResult) for r in results)
    assert all(not r.success for r in results)
    assert mock_reader.daily.call_count == 3


def test_metrics_includes_daily_l1_block(mock_setup):
    """get_metrics() 返回 daily_l1 子块, 字段与动态 TTL 契约一致."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036"))
        asyncio.run(client.get_daily("600036"))  # 命中

    m = client.get_metrics()
    assert "daily_l1" in m
    block = m["daily_l1"]
    assert block["enabled"] is True
    assert block["hits"] == 1
    assert block["misses"] == 1
    assert block["hit_rate"] == 0.5
    assert block["size"] == 1
    assert block["maxsize"] == 2048
    # TTL 动态过期到次日 9:00（非固定 7200 秒）；两次取值相差毫秒级
    assert abs(block["ttl_seconds"] - client._get_ttl_until_tomorrow_9am()) < 5
    assert 0 < block["ttl_seconds"] <= 24 * 3600
    assert "reader_semaphore_limit" in block
