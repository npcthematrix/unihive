"""P4-5: FQGate OpenAPI 导入 HTTP API (console_api 纯函数级, 不起 HTTP server)。

覆盖:
- POST /api/config/import/preview  body 默认值/白名单校验/异常状态码映射;
- POST /api/config/import/apply    confirm_dangerous 强确认 + revision 透传 +
  502/503/404/400/422/409 映射;
- 路由注册。
"""
from __future__ import annotations

import json

import pytest

from src.unihive.api.ths_local_gateway_client import OpenApiFetchError
from src.unihive.core.config_lint import LintIssue, LintRejected
from src.unihive.core.entity_edit import DangerousConfirmationRequired
from src.unihive.storage.config_store import ConfigStore, RevisionConflict
from src.unihive.utils import console_api as ca

from tests.test_config_entity_api import _FakeRequest
from tests.test_config_store_v2 import BUNDLE, VERSION

UPSTREAM = "THS-LOCAL-GATEWAY"


class _FakeImportServer:
    """记录导入调用并可注入下一个异常。"""

    def __init__(self) -> None:
        self.preview_calls: list[dict] = []
        self.apply_calls: list[dict] = []
        self.exc: Exception | None = None

    async def preview_openapi_import(self, *, upstream=UPSTREAM, tools=None):
        self.preview_calls.append({"upstream": upstream, "tools": tools})
        if self.exc is not None:
            raise self.exc
        return {"changed": True, "upstream": upstream, "summary": {}}

    async def apply_openapi_import(
        self,
        *,
        upstream=UPSTREAM,
        tools=None,
        confirm_dangerous=False,
        expected_revision=None,
    ):
        self.apply_calls.append(
            {
                "upstream": upstream,
                "tools": tools,
                "confirm_dangerous": confirm_dangerous,
                "expected_revision": expected_revision,
            }
        )
        if self.exc is not None:
            raise self.exc
        return {"changed": True, "revision": 1, "upstream": upstream}


@pytest.fixture
async def db_env(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    await store.seed_from_bundle(VERSION, BUNDLE)
    server = _FakeImportServer()
    monkeypatch.setattr(ca, "_config_store", store)
    monkeypatch.setattr(ca, "_db_mode", True)
    monkeypatch.setattr(ca, "_gateway_server", server, raising=False)
    yield store, server
    await store.close()


def _payload(resp) -> dict:
    return json.loads(resp.body)


# --------------------------------------------------------------- preview


async def test_preview_empty_body_uses_defaults(db_env):
    _, server = db_env
    resp = await ca.config_import_preview_api(_FakeRequest({}, None))
    assert resp.status_code == 200
    assert server.preview_calls == [{"upstream": UPSTREAM, "tools": None}]


async def test_preview_forwards_upstream_and_tools(db_env):
    _, server = db_env
    body = {"upstream": "THS-LOCAL-GATEWAY", "tools": ["fqgate_a", "fqgate_b"]}
    resp = await ca.config_import_preview_api(_FakeRequest({}, body))
    assert resp.status_code == 200
    assert server.preview_calls == [
        {"upstream": UPSTREAM, "tools": ["fqgate_a", "fqgate_b"]}
    ]


async def test_preview_non_object_body_400(db_env):
    _, server = db_env
    resp = await ca.config_import_preview_api(_FakeRequest({}, ["x"]))
    assert resp.status_code == 400
    assert server.preview_calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"upstream": ""},
        {"upstream": 7},
        {"upstream": "   "},
        {"tools": []},
        {"tools": "fqgate_a"},
        {"tools": ["fqgate_a", 3]},
        {"tools": ["fqgate_a", ""]},
        {"tools": ["fqgate_a", "fqgate_a"]},
    ],
)
async def test_preview_bad_params_400(db_env, body):
    _, server = db_env
    resp = await ca.config_import_preview_api(_FakeRequest({}, body))
    assert resp.status_code == 400
    assert _payload(resp)["error"]
    assert server.preview_calls == []


async def test_preview_yaml_mode_503(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_import_preview_api(_FakeRequest({}, {}))
    assert resp.status_code == 503


async def test_preview_no_server_503(db_env, monkeypatch):
    monkeypatch.setattr(ca, "_gateway_server", None, raising=False)
    resp = await ca.config_import_preview_api(_FakeRequest({}, {}))
    assert resp.status_code == 503


@pytest.mark.parametrize(
    "exc,status",
    [
        (KeyError("NOPE"), 404),
        (ValueError("selected tools not in added/changed set: fqgate_x"), 400),
        (ValueError("does not support OpenAPI import"), 400),
        (OpenApiFetchError("upstream returned HTTP 500"), 502),
        (RuntimeError("upstream 'X' is configured but not connected"), 503),
        (RuntimeError("Gateway not initialized"), 503),
        (RuntimeError("upstream 'X' tools/list failed: boom"), 502),
    ],
)
async def test_preview_exception_status_mapping(db_env, exc, status):
    _, server = db_env
    server.exc = exc
    resp = await ca.config_import_preview_api(_FakeRequest({}, {}))
    assert resp.status_code == status


async def test_preview_lint_rejected_422(db_env):
    _, server = db_env
    server.exc = LintRejected([LintIssue("error", "M001", "bad contract")])
    resp = await ca.config_import_preview_api(_FakeRequest({}, {}))
    assert resp.status_code == 422
    assert _payload(resp)["issues"][0]["code"] == "M001"


# ----------------------------------------------------------------- apply


async def test_apply_requires_confirmation(db_env):
    _, server = db_env
    resp = await ca.config_import_apply_api(_FakeRequest({}, {}))
    assert resp.status_code == 400
    assert _payload(resp)["error"] == "dangerous_confirmation_required"
    assert server.apply_calls == []


async def test_apply_invalid_json_400(db_env):
    _, server = db_env
    resp = await ca.config_import_apply_api(_FakeRequest({}, None))
    assert resp.status_code == 400
    assert server.apply_calls == []


async def test_apply_confirm_forwards_all_kwargs(db_env):
    _, server = db_env
    body = {
        "confirm_dangerous": True,
        "upstream": UPSTREAM,
        "tools": ["fqgate_a"],
        "revision": 3,
    }
    resp = await ca.config_import_apply_api(_FakeRequest({}, body))
    assert resp.status_code == 200
    assert server.apply_calls == [
        {
            "upstream": UPSTREAM,
            "tools": ["fqgate_a"],
            "confirm_dangerous": True,
            "expected_revision": 3,
        }
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"confirm_dangerous": True, "tools": []},
        {"confirm_dangerous": True, "tools": [1]},
        {"confirm_dangerous": True, "upstream": ""},
        {"confirm_dangerous": "true"},
        {"confirm_dangerous": 1},
        {"confirm_dangerous": True, "revision": "3"},
        {"confirm_dangerous": True, "revision": True},
        ["x"],
    ],
)
async def test_apply_bad_body_400(db_env, body):
    _, server = db_env
    resp = await ca.config_import_apply_api(_FakeRequest({}, body))
    assert resp.status_code == 400
    assert server.apply_calls == []


async def test_apply_yaml_mode_503(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_import_apply_api(
        _FakeRequest({}, {"confirm_dangerous": True})
    )
    assert resp.status_code == 503


async def test_apply_no_server_503(db_env, monkeypatch):
    monkeypatch.setattr(ca, "_gateway_server", None, raising=False)
    resp = await ca.config_import_apply_api(
        _FakeRequest({}, {"confirm_dangerous": True})
    )
    assert resp.status_code == 503


async def test_apply_dangerous_confirmation_from_runtime_400(db_env):
    """server 内部再确认 (理论上路由已挡) → 同体 contract_fields 400。"""
    _, server = db_env
    server.exc = DangerousConfirmationRequired(["tool:fqgate_a:contract"])
    resp = await ca.config_import_apply_api(
        _FakeRequest({}, {"confirm_dangerous": True})
    )
    assert resp.status_code == 400
    body = _payload(resp)
    assert body["error"] == "dangerous_confirmation_required"
    assert body["contract_fields"] == ["tool:fqgate_a:contract"]


async def test_apply_openapi_fetch_failure_502(db_env):
    _, server = db_env
    server.exc = OpenApiFetchError("ConnectError")
    resp = await ca.config_import_apply_api(
        _FakeRequest({}, {"confirm_dangerous": True})
    )
    assert resp.status_code == 502
    assert _payload(resp)["error"] == "openapi_fetch_failed"


async def test_apply_revision_conflict_409(db_env):
    _, server = db_env
    server.exc = RevisionConflict(expected=2, actual=5)
    resp = await ca.config_import_apply_api(
        _FakeRequest({}, {"confirm_dangerous": True})
    )
    assert resp.status_code == 409


async def test_apply_unknown_upstream_404(db_env):
    _, server = db_env
    server.exc = KeyError("NOPE")
    resp = await ca.config_import_apply_api(
        _FakeRequest({}, {"confirm_dangerous": True, "upstream": "NOPE"})
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------- routes


async def test_import_routes_registered():
    routes = ca.build_upstream_routes()
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes}
    assert ("/api/config/import/preview", ("POST",)) in paths
    assert ("/api/config/import/apply", ("POST",)) in paths
