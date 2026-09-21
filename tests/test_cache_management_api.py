"""缓存监控扩展指标 + 管理端点测试 (2026-09-21 网关缓存监控页丰富专题)。

覆盖:
- get_cache_stats: 条目分桶 (live/expired/null_expiry) / 最快到期 /
  最旧写入 / freelist+page 存储指标 / stats 文件锚定 + updated_at/pid
- get_cache_breakdown: 按工具 (key 前缀) 聚合
- POST /api/cache/cleanup|clear|vacuum + /api/upstreams/<name>/l1/reset
"""
import json
import sqlite3
import time

import pytest
from starlette.requests import Request as StarletteRequest

from src.unihive.utils import console_api
from src.unihive.utils.console_api import (
    cache_cleanup_api,
    cache_clear_api,
    cache_tool_breakdown_api,
    cache_vacuum_api,
    get_cache_stats,
    upstream_l1_reset_api,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_cache_db(path, rows):
    """在 path 建 cache 表并插桩数据。rows: (key, expires_at, created_at)。"""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            expires_at INTEGER,
            created_at INTEGER NOT NULL
        )
        """
    )
    now = time.time()
    for key, expires_at, created_at in rows:
        conn.execute(
            "INSERT INTO cache (key, value, expires_at, created_at) "
            "VALUES (?, '[]', ?, ?)",
            (key, expires_at, created_at or now),
        )
    conn.commit()
    conn.close()


def _make_req(*, path_params=None, query_string=b"", body=None):
    """最小 Starlette Request; body 为 bytes 或可 JSON 序列化对象。"""
    if body is None:
        raw = b""
    elif isinstance(body, bytes):
        raw = body
    else:
        raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw}

    return StarletteRequest(
        scope={
            "type": "http",
            "path_params": path_params or {},
            "query_string": query_string,
            "headers": [],
        },
        receive=receive,
    )


class _FakeLiveCache:
    def __init__(self):
        self.cleanup_expired_calls = 0
        self.clear_calls = 0

    async def cleanup_expired(self):
        self.cleanup_expired_calls += 1
        return 7

    async def clear(self):
        self.clear_calls += 1
        return True


class _FakeServer:
    def __init__(self, cache):
        self.cache = cache


class _FakeResetClient:
    def __init__(self, result=None, raises=False):
        self._result = result
        self._raises = raises
        self.calls = 0

    def reset_l1_caches(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._result


# ---------------------------------------------------------------------------
# get_cache_stats
# ---------------------------------------------------------------------------


def test_stats_disabled_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(console_api, "CACHE_DB_PATH", str(tmp_path / "nope.db"))
    stats = get_cache_stats()
    assert stats["enabled"] is False
    assert "timestamp" in stats


def test_stats_row_buckets_and_storage_metrics(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    now = time.time()
    live_expiry = int(now + 1000)
    expired_expiry = int(now - 1000)
    _build_cache_db(
        str(db_path),
        [
            ("tool_a:hash1", live_expiry, now - 50),
            ("tool_a:hash2", expired_expiry, now - 200),
            ("tool_b:hash3", None, now - 100),
        ],
    )
    monkeypatch.setattr(console_api, "CACHE_DB_PATH", str(db_path))
    monkeypatch.setattr(console_api, "_RUNTIME_ROOT", tmp_path)

    # stats 文件锚定 _RUNTIME_ROOT/logs (而非 CWD)
    stats_dir = tmp_path / "logs"
    stats_dir.mkdir()
    (stats_dir / "cache_stats.json").write_text(
        json.dumps(
            {"hits": 4, "misses": 1, "updated_at": 12345, "pid": 4242}
        ),
        encoding="utf-8",
    )

    stats = get_cache_stats()
    assert stats["enabled"] is True
    assert stats["entries"] == 3
    assert stats["live_entries"] == 1
    assert stats["expired_entries"] == 1
    assert stats["null_expiry_entries"] == 1
    assert stats["nearest_expiry"] == live_expiry
    assert stats["oldest_created"] == pytest.approx(now - 200, abs=1)
    assert isinstance(stats["freelist_count"], int)
    assert isinstance(stats["page_count"], int)
    assert isinstance(stats["page_size"], int)
    assert stats["wal_size_bytes"] == 0  # rollback journal 模式无 -wal
    assert stats["hits"] == 4
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(0.8)
    assert stats["stats_updated_at"] == 12345
    assert stats["stats_pid"] == 4242


def test_stats_empty_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    _build_cache_db(str(db_path), [])
    monkeypatch.setattr(console_api, "CACHE_DB_PATH", str(db_path))
    monkeypatch.setattr(console_api, "_RUNTIME_ROOT", tmp_path)

    stats = get_cache_stats()
    assert stats["entries"] == 0
    assert stats["live_entries"] == 0
    assert stats["expired_entries"] == 0
    assert stats["null_expiry_entries"] == 0
    assert stats["nearest_expiry"] is None


# ---------------------------------------------------------------------------
# breakdown
# ---------------------------------------------------------------------------


def test_breakdown_groups_by_tool_prefix(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    now = time.time()
    _build_cache_db(
        str(db_path),
        [
            ("tool_a:h1", int(now + 100), now),
            ("tool_a:h2", int(now - 100), now),
            ("tool_b:h3", int(now + 100), now),
        ],
    )
    monkeypatch.setattr(console_api, "CACHE_DB_PATH", str(db_path))

    result = console_api.get_cache_breakdown()
    rows = result["tools"]
    assert result["total_tools"] == 2
    by_tool = {r["tool"]: r for r in rows}
    assert by_tool["tool_a"]["entries"] == 2
    assert by_tool["tool_a"]["live"] == 1
    assert by_tool["tool_b"]["entries"] == 1
    assert rows[0]["tool"] == "tool_a"  # 降序


def test_breakdown_api_200(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    _build_cache_db(
        str(db_path), [("tool_a:h1", int(time.time() + 100), time.time())]
    )
    monkeypatch.setattr(console_api, "CACHE_DB_PATH", str(db_path))

    import asyncio

    resp = asyncio.run(cache_tool_breakdown_api(_make_req()))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["tools"][0]["tool"] == "tool_a"


def test_breakdown_api_rejects_bad_limit(tmp_path):
    import asyncio

    req = _make_req(query_string=b"limit=nope")
    resp = asyncio.run(cache_tool_breakdown_api(req))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# management: cleanup / clear
# ---------------------------------------------------------------------------


def test_cleanup_api_runs_live_cleanup():
    import asyncio

    live = _FakeLiveCache()
    console_api.set_gateway_server(_FakeServer(live))
    try:
        resp = asyncio.run(cache_cleanup_api(_make_req()))
        assert resp.status_code == 200
        assert json.loads(resp.body)["deleted"] == 7
        assert live.cleanup_expired_calls == 1
    finally:
        console_api.set_gateway_server(None)


def test_cleanup_api_503_without_live_cache():
    import asyncio

    console_api.set_gateway_server(None)
    resp = asyncio.run(cache_cleanup_api(_make_req()))
    assert resp.status_code == 503


def test_clear_api_requires_confirm():
    import asyncio

    live = _FakeLiveCache()
    console_api.set_gateway_server(_FakeServer(live))
    try:
        resp = asyncio.run(cache_clear_api(_make_req(body={})))
        assert resp.status_code == 400
        assert live.clear_calls == 0

        resp = asyncio.run(cache_clear_api(_make_req(body={"confirm": True})))
        assert resp.status_code == 200
        assert json.loads(resp.body)["cleared"] is True
        assert live.clear_calls == 1
    finally:
        console_api.set_gateway_server(None)


def test_clear_api_rejects_non_object_body():
    import asyncio

    resp = asyncio.run(cache_clear_api(_make_req(body=b"not-json")))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# management: vacuum
# ---------------------------------------------------------------------------


def test_vacuum_api_reclaims_space(tmp_path, monkeypatch):
    import asyncio

    db_path = tmp_path / "cache.db"
    now = time.time()
    # 先造大量数据再删, 制造 freelist 死页
    _build_cache_db(
        str(db_path),
        [
            (f"tool_x:h{i}", int(now + 100), now)
            for i in range(200)
        ],
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM cache")
    conn.commit()
    conn.close()

    monkeypatch.setattr(console_api, "CACHE_DB_PATH", str(db_path))
    resp = asyncio.run(cache_vacuum_api(_make_req()))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert isinstance(body["db_size_mb"], (int, float))
    assert body["wal_size_bytes"] == 0


def test_vacuum_api_409_when_db_missing(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(
        console_api, "CACHE_DB_PATH", str(tmp_path / "missing.db")
    )
    resp = asyncio.run(cache_vacuum_api(_make_req()))
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# management: upstream L1 reset
# ---------------------------------------------------------------------------


def test_l1_reset_200():
    import asyncio

    fake = _FakeResetClient(result={"enabled": True, "cleared": ["daily"]})
    console_api.set_upstream_registry({"mootdx2": fake})
    try:
        resp = asyncio.run(
            upstream_l1_reset_api(_make_req(path_params={"name": "mootdx2"}))
        )
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["result"]["cleared"] == ["daily"]
        assert fake.calls == 1
    finally:
        console_api.set_upstream_registry({})


def test_l1_reset_404_unknown():
    import asyncio

    console_api.set_upstream_registry({})
    resp = asyncio.run(
        upstream_l1_reset_api(_make_req(path_params={"name": "x"}))
    )
    assert resp.status_code == 404


def test_l1_reset_501_unimplemented():
    import asyncio

    class _NoReset:
        pass

    console_api.set_upstream_registry({"fuyao": _NoReset()})
    try:
        resp = asyncio.run(
            upstream_l1_reset_api(_make_req(path_params={"name": "fuyao"}))
        )
        assert resp.status_code == 501
    finally:
        console_api.set_upstream_registry({})


def test_l1_reset_500_on_exception():
    import asyncio

    fake = _FakeResetClient(raises=True)
    console_api.set_upstream_registry({"mootdx2": fake})
    try:
        resp = asyncio.run(
            upstream_l1_reset_api(_make_req(path_params={"name": "mootdx2"}))
        )
        assert resp.status_code == 500
    finally:
        console_api.set_upstream_registry({})
