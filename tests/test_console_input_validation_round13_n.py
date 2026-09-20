"""Round-13 簇N — 控制台边界输入校验 / 异常文本不外泄 / 死模块删除。

四项审视结论:
1. POST /api/auth/login 收到 JSON 数组/字符串 (而非对象) 时,
   ``body.get(...)`` 抛 AttributeError → 500。契约应为 400。
2. POST /api/omni/sync 同样裸 ``await req.json()`` + ``body.get``:
   坏 JSON / 非对象 → 500; GET /api/omni/query?page=abc 裸 int() → 500。
3. config_upstream_detail_api / config_tool_detail_api 把内部异常文本
   拼进响应体 (可能含 sqlite 路径/SQL/内部结构)。应对外固定错误码,
   细节只进日志。
4. src/unihive/core/auth_middleware.py 零引用且 PUBLIC_PATHS 会放开
   整个 /api/* —— 删除, 防未来误挂载。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from src.unihive.console_auth import hash_password, setup_console_auth
from src.unihive.utils import console_api

_SECRET = "test-only-secret-not-the-factory-default-0123456789ab"


def _auth_app() -> Starlette:
    app = Starlette()
    setup_console_auth(
        app,
        {
            "username": "admin",
            "password_hash": hash_password("devpass"),
            "session_secret": _SECRET,
        },
    )
    return app


# ---------------------------------------------------------------------------
# 1. login: 非对象 JSON 体必须 400 (不是 500)
# ---------------------------------------------------------------------------


class TestLoginBodyValidation:
    @pytest.mark.parametrize("raw", [b"[]", b'"x"', b"123", b"null"])
    def test_non_object_json_body_returns_400(self, raw):
        client = TestClient(_auth_app())
        resp = client.post(
            "/api/auth/login",
            content=raw,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "JSON" in resp.json()["error"]

    def test_malformed_json_body_returns_400(self):
        client = TestClient(_auth_app())
        resp = client.post(
            "/api/auth/login",
            content=b"{,",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_empty_object_body_returns_400_missing_fields(self):
        client = TestClient(_auth_app())
        resp = client.post(
            "/api/auth/login",
            content=b"{}",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "用户名和密码" in resp.json()["error"]

    def test_valid_object_body_still_reaches_credential_check(self):
        # 防过度拦截: 形态正确的对象必须走到凭证校验 (错误凭证 → 401, 不是 400)。
        client = TestClient(_auth_app())
        resp = client.post(
            "/api/auth/login",
            content=b'{"username":"admin","password":"wrong"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. OMNI 路由: sync JSON 体 + query 分页参数
# ---------------------------------------------------------------------------


@pytest.fixture
def omni_client():
    return TestClient(Starlette(routes=console_api.build_omni_routes()))


class TestOmniSyncBodyValidation:
    @pytest.mark.parametrize("raw", [b"[]", b'"x"', b"123", b"null"])
    def test_non_object_json_body_returns_400(self, omni_client, raw):
        resp = omni_client.post(
            "/api/omni/sync",
            content=raw,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "JSON" in resp.json()["error"]

    def test_malformed_json_body_returns_400(self, omni_client):
        resp = omni_client.post(
            "/api/omni/sync",
            content=b"{not-json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_empty_object_body_is_accepted_defaults(self, omni_client):
        # 形态正确 → 进入既有业务链 (测试环境 OMNI 未初始化 → 200 + failed 信封,
        # 重点是不再因 .get / int() 在边界上炸 500)。
        resp = omni_client.post(
            "/api/omni/sync",
            content=b"{}",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"


class TestOmniQueryPaginationValidation:
    @pytest.mark.parametrize("qs", ["page=abc", "page=-1", "page_size=xyz", "page=1&page_size=0"])
    def test_non_positive_integer_pagination_returns_400(self, omni_client, qs):
        resp = omni_client.get(f"/api/omni/query?{qs}")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_no_pagination_params_uses_defaults(self, omni_client):
        resp = omni_client.get("/api/omni/query")
        assert resp.status_code == 200

    def test_valid_positive_integers_pass_boundary(self, omni_client):
        resp = omni_client.get("/api/omni/query?page=2&page_size=20")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. 配置实体详情接口: 内部异常文本不得进响应体
# ---------------------------------------------------------------------------


class _ExplodingStore:
    def __init__(self, detail: str):
        self.detail = detail

    async def get_upstream(self, name):
        raise RuntimeError(f"sqlite error at /secret/internal/path: {self.detail}")

    async def get_tool(self, name):
        raise RuntimeError(f"sqlite error at /secret/internal/path: {self.detail}")

    async def revision(self):
        return 1


class _FakeReq:
    def __init__(self, name: str):
        self.path_params = {"name": name}


class TestConfigDetailErrorLeak:
    @pytest.fixture
    def db_mode(self, monkeypatch):
        monkeypatch.setattr(console_api, "_db_mode", True, raising=False)
        store = _ExplodingStore("disk I/O failure")
        monkeypatch.setattr(console_api, "_config_store", store, raising=False)
        return store

    @pytest.mark.asyncio
    async def test_upstream_detail_returns_fixed_code_without_internal_text(self, db_mode):
        resp = await console_api.config_upstream_detail_api(_FakeReq("fuyao"))
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "secret" not in body
        assert "sqlite" not in body
        assert "disk I/O" not in body
        assert json.loads(resp.body) == {"error": "upstream_load_failed"}

    @pytest.mark.asyncio
    async def test_tool_detail_returns_fixed_code_without_internal_text(self, db_mode):
        resp = await console_api.config_tool_detail_api(_FakeReq("m_get_quote"))
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "secret" not in body
        assert "sqlite" not in body
        assert json.loads(resp.body) == {"error": "tool_load_failed"}


# ---------------------------------------------------------------------------
# 4. 死模块 auth_middleware.py 必须保持删除
# ---------------------------------------------------------------------------


def test_bearer_token_middleware_module_removed():
    repo_root = Path(__file__).resolve().parent.parent
    dead = repo_root / "src" / "unihive" / "core" / "auth_middleware.py"
    assert not dead.exists(), (
        "core/auth_middleware.py 已废弃删除 (PUBLIC_PATHS 会放开整个 /api/*); "
        "不要重新引入, Bearer 需求应新建模块。"
    )
    assert importlib.util.find_spec("src.unihive.core.auth_middleware") is None
