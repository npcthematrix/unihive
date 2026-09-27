"""config_pool_update_api handler 测试 (Phase E 2026-09-27)。

yaml 模式 503 / 形态/范围错 400 / 上游缺 404 / revision 冲突 409 / happy path 200。
"""
from __future__ import annotations

import json

import pytest


def _make_req(body: dict | None):
    """构造一个 Starlette Request; body 为 None 时为空 body。"""
    from starlette.requests import Request as StarletteRequest

    raw = b"" if body is None else json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw}

    return StarletteRequest(
        scope={"type": "http", "path_params": {}, "query_string": b"", "headers": []},
        receive=receive,
    )


@pytest.fixture(autouse=True)
def _isolate_globals(monkeypatch):
    """每个用例前后复位 console_api 单例与 db mode, 避免互污染。"""
    import src.unihive.utils.console_api as ca

    monkeypatch.setattr(ca, "_gateway_server", None)
    monkeypatch.setattr(ca, "_db_mode", True)
    monkeypatch.setattr(ca, "_config_store", object())  # 非 None 即可
    yield
    monkeypatch.setattr(ca, "_gateway_server", None)


def _wire_server_with_store(server):
    import src.unihive.utils.console_api as ca

    ca._gateway_server = server
    ca._db_mode = True
    ca._config_store = object()


def _wire_yaml_mode():
    import src.unihive.utils.console_api as ca

    ca._gateway_server = None  # 任意 server 都不该在 yaml 模式下被信任
    ca._db_mode = False
    ca._config_store = None


# ========== 用例 ==========

class TestPoolUpdateApi:
    async def test_503_in_yaml_mode(self):
        _wire_yaml_mode()
        from src.unihive.utils.console_api import config_pool_update_api

        resp = await config_pool_update_api(_make_req({"fields": {}}))
        assert resp.status_code == 503
        body = json.loads(resp.body)
        assert body.get("error") == "config_store_unavailable"

    async def test_400_invalid_json(self):
        from starlette.responses import JSONResponse

        from starlette.requests import Request as StarletteRequest

        async def bad_receive():
            return {"type": "http.request", "body": b"{not json"}

        req = StarletteRequest(
            scope={"type": "http", "path_params": {}, "query_string": b"", "headers": []},
            receive=bad_receive,
        )
        from src.unihive.gateway_server import GatewayServer
        from src.unihive.utils.console_api import config_pool_update_api

        server = GatewayServer.__new__(GatewayServer)
        server.config_store = None  # yaml 路径不会触到这里
        _wire_server_with_store(server)

        resp = await config_pool_update_api(req)
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 400
        assert json.loads(resp.body).get("error") == "invalid json body"

    async def test_400_missing_fields(self):
        """fields 缺键 → ValueError → 400。"""
        from src.unihive.gateway_server import GatewayServer
        from src.unihive.utils.console_api import config_pool_update_api

        server = GatewayServer.__new__(GatewayServer)
        server.config_store = object()  # 形似, 但调用 apply 时会失败 (object 没 read_bundle)
        # 实际让 ValueError 在 apply 里抛, 这里把字段形态校验放在 apply 里 ——
        # 但 handler 是直接 await server.apply_pool_config_edit(fields) 的,
        # 会落到我们的 ValueError 分支。
        _wire_server_with_store(server)

        resp = await config_pool_update_api(
            _make_req({"fields": {"mootdx2_reader_limit": 4, "mootdx2_worker_threads": 8}}),  # 缺 tdx_quant
        )
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "tdx_quant_worker_threads" in body.get("message", body.get("error", ""))

    async def test_400_below_one(self):
        from src.unihive.gateway_server import GatewayServer
        from src.unihive.utils.console_api import config_pool_update_api

        server = GatewayServer.__new__(GatewayServer)
        server.config_store = object()
        _wire_server_with_store(server)

        resp = await config_pool_update_api(_make_req({"fields": {
            "mootdx2_reader_limit": 0,
            "mootdx2_worker_threads": 8,
            "tdx_quant_worker_threads": 10,
        }}))
        assert resp.status_code == 400

    async def test_200_no_confirm_required(self):
        """happy path, fields 完整 ≥1 → 200; 不要求 confirm_dangerous。"""
        from src.unihive.core.pool_config import PoolConfig
        from src.unihive.gateway_server import GatewayServer
        from src.unihive.storage.config_store import ConfigStore
        from src.unihive.utils.console_api import config_pool_update_api

        # 真实 _FakeStore 走 write 路径 (handler 不区分)
        from tests.test_pool_config_edit import _FakeStore, _FakeUpstream  # noqa: E402

        store = _FakeStore([
            _FakeUpstream("mootdx2", config={}),
            _FakeUpstream("tdx_quant", config={}),
        ])
        server = GatewayServer.__new__(GatewayServer)
        server.config_store = store
        server._pool_config = PoolConfig(
            mootdx2_reader_limit=4, mootdx2_worker_threads=8, tdx_quant_worker_threads=10,
            sources={},
        )
        _wire_server_with_store(server)

        resp = await config_pool_update_api(_make_req({"fields": {
            "mootdx2_reader_limit": 6,
            "mootdx2_worker_threads": 12,
            "tdx_quant_worker_threads": 16,
        }}))
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["persisted"]["mootdx2_worker_threads"] == 12
        assert body["pending_restart"]["mootdx2_worker_threads"] is True

    async def test_409_revision_conflict(self):
        """revision 过期 → 409 revision_conflict。"""
        from src.unihive.core.pool_config import PoolConfig
        from src.unihive.gateway_server import GatewayServer
        from src.unihive.utils.console_api import config_pool_update_api

        from tests.test_pool_config_edit import _FakeStore, _FakeUpstream  # noqa: E402

        store = _FakeStore(
            [_FakeUpstream("mootdx2"), _FakeUpstream("tdx_quant")],
            revision=10,
        )
        server = GatewayServer.__new__(GatewayServer)
        server.config_store = store
        server._pool_config = PoolConfig(
            mootdx2_reader_limit=4, mootdx2_worker_threads=8, tdx_quant_worker_threads=10,
            sources={},
        )
        _wire_server_with_store(server)

        resp = await config_pool_update_api(_make_req({
            "fields": {
                "mootdx2_reader_limit": 4,
                "mootdx2_worker_threads": 8,
                "tdx_quant_worker_threads": 10,
            },
            "revision": 5,  # 与 store.revision=10 不一致
        }))
        assert resp.status_code == 409
        body = json.loads(resp.body)
        assert body.get("error") == "revision_conflict"