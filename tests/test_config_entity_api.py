"""P3-3: 实体编辑 HTTP API (console_api 纯函数级, 不起 HTTP server)。

覆盖:
- GET /api/config/tools/{name}: 详情 / 404 / yaml 逃生模式;
- PUT /api/config/tools/{name}: body 校验 + apply_tool_edit 异常→状态码映射
  (400 缺确认 / 422 lint / 409 乐观锁 / 404 / 503 / 500);
- PUT /api/config/upstreams/{name}: body 校验 + 同一异常映射。
"""
from __future__ import annotations

import json

import pytest

from src.unihive.core.config_lint import LintIssue, LintRejected
from src.unihive.core.entity_edit import DangerousConfirmationRequired, EntityEditError
from src.unihive.storage.config_store import ConfigStore, RevisionConflict
from src.unihive.utils import console_api as ca

from tests.test_config_store_v2 import BUNDLE, VERSION


class _FakeRequest:
    def __init__(self, params: dict, body=None):
        self.path_params = params
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


class _FakeGatewayServer:
    """记录调用并可注入下一个异常。"""

    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.upstream_calls: list[dict] = []
        self.exc: Exception | None = None

    async def apply_tool_edit(
        self, name, patch, *, confirm_dangerous=False, expected_revision=None
    ):
        self.tool_calls.append(
            {
                "name": name,
                "patch": patch,
                "confirm_dangerous": confirm_dangerous,
                "expected_revision": expected_revision,
            }
        )
        if self.exc is not None:
            raise self.exc
        return {
            "changed": True,
            "name": name,
            "revision": 3,
            "published": True,
            "previously_published": True,
            "contract_fields": [],
        }

    async def apply_upstream_edit(
        self, name, config, *, type=None, confirm_dangerous=False, expected_revision=None
    ):
        self.upstream_calls.append(
            {
                "name": name,
                "config": config,
                "type": type,
                "confirm_dangerous": confirm_dangerous,
                "expected_revision": expected_revision,
            }
        )
        if self.exc is not None:
            raise self.exc
        return {
            "changed": True,
            "name": name,
            "revision": 3,
            "enabled": True,
            "connected": True,
            "contract_fields": ["upstream.config"],
        }


@pytest.fixture
async def db_env(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    await store.seed_from_bundle(VERSION, BUNDLE)
    server = _FakeGatewayServer()
    monkeypatch.setattr(ca, "_config_store", store)
    monkeypatch.setattr(ca, "_db_mode", True)
    monkeypatch.setattr(ca, "_gateway_server", server, raising=False)
    yield store, server
    await store.close()


def _payload(resp) -> dict:
    return json.loads(resp.body)


# --- GET detail ------------------------------------------------------------


async def test_get_tool_detail_returns_full_row_and_revision(db_env):
    store, _ = db_env
    resp = await ca.config_tool_detail_api(_FakeRequest({"name": "a_one"}))
    assert resp.status_code == 200
    body = _payload(resp)
    assert body["source"] == "db"
    assert body["revision"] == 0
    assert body["tool"]["name"] == "a_one"
    assert body["tool"]["spec"]["routing"] == "a_one"
    assert body["tool"]["mapping"] == {"alpha": "a_one"}


async def test_get_tool_detail_unknown_404(db_env):
    resp = await ca.config_tool_detail_api(_FakeRequest({"name": "ghost"}))
    assert resp.status_code == 404


async def test_get_tool_detail_yaml_mode_reports_source(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_tool_detail_api(_FakeRequest({"name": "a_one"}))
    assert resp.status_code == 200
    assert _payload(resp) == {"source": "yaml"}


# --- GET upstream detail ---------------------------------------------------


async def test_get_upstream_detail_returns_entity_row_with_placeholders(db_env):
    """连接块编辑必须吃实体原值 (${VAR} 占位符), 不是脱敏快照。"""
    resp = await ca.config_upstream_detail_api(_FakeRequest({"name": "beta"}))
    assert resp.status_code == 200
    body = _payload(resp)
    assert body["source"] == "db"
    assert body["revision"] == 0
    assert body["upstream"]["name"] == "beta"
    assert body["upstream"]["type"] == "http"
    assert body["upstream"]["enabled"] is False
    assert body["upstream"]["config"] == {
        "base_url": "http://x",
        "api_key": "${BETA_KEY}",
    }


async def test_get_upstream_detail_unknown_404(db_env):
    resp = await ca.config_upstream_detail_api(_FakeRequest({"name": "ghost"}))
    assert resp.status_code == 404


async def test_get_upstream_detail_yaml_mode_reports_source(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_upstream_detail_api(_FakeRequest({"name": "beta"}))
    assert resp.status_code == 200
    assert _payload(resp) == {"source": "yaml"}


# --- PUT tool --------------------------------------------------------------


async def test_put_tool_happy_path_forwards_args(db_env):
    _, server = db_env
    req = _FakeRequest(
        {"name": "a_one"},
        {"patch": {"description": "新"}, "confirm_dangerous": True, "revision": 2},
    )
    resp = await ca.config_tool_update_api(req)
    assert resp.status_code == 200
    assert _payload(resp)["revision"] == 3
    assert server.tool_calls == [
        {
            "name": "a_one",
            "patch": {"description": "新"},
            "confirm_dangerous": True,
            "expected_revision": 2,
        }
    ]


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"patch": {}},
        {"patch": "notdict"},
        {"patch": {"description": "x"}, "revision": "2"},
        {"patch": {"description": "x"}, "revision": True},
        "not-object",
    ],
)
async def test_put_tool_bad_body_400(db_env, body):
    resp = await ca.config_tool_update_api(_FakeRequest({"name": "a_one"}, body))
    assert resp.status_code == 400


async def test_put_tool_requires_confirmation_400_with_contract_fields(db_env):
    _, server = db_env
    server.exc = DangerousConfirmationRequired(["mapping", "name"])
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"mapping": {"beta": "x"}}})
    )
    assert resp.status_code == 400
    body = _payload(resp)
    assert body["error"] == "dangerous_confirmation_required"
    assert body["contract_fields"] == ["mapping", "name"]


async def test_put_tool_lint_rejected_422_with_issues(db_env):
    _, server = db_env
    server.exc = LintRejected(
        [
            LintIssue("error", "M001", "unknown upstream ghost"),
            LintIssue("error", "T001", "bad spec"),
        ]
    )
    resp = await ca.config_tool_update_api(
        _FakeRequest(
            {"name": "a_one"},
            {"patch": {"mapping": {"ghost": "x"}}, "confirm_dangerous": True},
        )
    )
    assert resp.status_code == 422
    body = _payload(resp)
    assert body["error"] == "lint_rejected"
    assert [i["code"] for i in body["issues"]] == ["M001", "T001"]


async def test_put_tool_revision_conflict_409(db_env):
    _, server = db_env
    server.exc = RevisionConflict(expected=2, actual=5)
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"description": "x"}, "revision": 2})
    )
    assert resp.status_code == 409
    body = _payload(resp)
    assert body["error"] == "revision_conflict"
    assert body["expected"] == 2 and body["actual"] == 5


async def test_put_tool_unknown_404(db_env):
    _, server = db_env
    server.exc = KeyError("ghost")
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "ghost"}, {"patch": {"description": "x"}})
    )
    assert resp.status_code == 404


async def test_put_tool_entity_edit_error_400(db_env):
    _, server = db_env
    server.exc = EntityEditError("non-writable tool field(s): foo")
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"foo": 1}})
    )
    assert resp.status_code == 400


async def test_put_tool_store_unavailable_503(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"description": "x"}})
    )
    assert resp.status_code == 503
    assert _payload(resp)["error"].startswith("config_store_unavailable")


async def test_put_tool_server_missing_503(db_env, monkeypatch):
    monkeypatch.setattr(ca, "_gateway_server", None, raising=False)
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"description": "x"}})
    )
    assert resp.status_code == 503


async def test_put_tool_runtime_requires_db_503(db_env):
    _, server = db_env
    server.exc = RuntimeError(
        "tool editing requires the config.db entity source; no store"
    )
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"description": "x"}})
    )
    assert resp.status_code == 503


async def test_put_tool_unexpected_runtime_500(db_env):
    _, server = db_env
    server.exc = RuntimeError("provider exploded")
    resp = await ca.config_tool_update_api(
        _FakeRequest({"name": "a_one"}, {"patch": {"description": "x"}})
    )
    assert resp.status_code == 500


async def test_put_runtime_500_envelope_carries_current_revision(db_env, monkeypatch):
    """M2: 热路径 500 信封必须带库内当前 revision, 供前端判断是否需要刷新。"""
    _, server = db_env
    monkeypatch.setattr(ca, "_db_revision", 5)
    server.exc = RuntimeError("provider exploded")
    resp = await ca.config_upstream_update_api(
        _FakeRequest(
            {"name": "beta"},
            {"config": {"base_url": "http://x"}, "confirm_dangerous": True},
        )
    )
    assert resp.status_code == 500
    assert _payload(resp)["revision"] == 5


# --- PUT upstream ----------------------------------------------------------


async def test_put_upstream_happy_path(db_env):
    _, server = db_env
    body = {
        "config": {"base_url": "http://new", "api_key": "${BETA_KEY}"},
        "type": "http",
        "confirm_dangerous": True,
        "revision": 1,
    }
    resp = await ca.config_upstream_update_api(_FakeRequest({"name": "beta"}, body))
    assert resp.status_code == 200
    assert server.upstream_calls[0]["name"] == "beta"
    assert server.upstream_calls[0]["type"] == "http"
    assert server.upstream_calls[0]["expected_revision"] == 1


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"config": "nope"},
        {"config": {}},  # 空连接块: 边界层允许空 dict, 这里 400? 见下
        {"config": {"x": 1}, "type": ""},
        {"config": {"x": 1}, "type": 9},
        {"config": {"x": 1}, "revision": "0"},
    ],
)
async def test_put_upstream_bad_body_400(db_env, body):
    # 注意: 空 dict 是合法连接块 (validate_upstream_config 放行), 从参数中剔除。
    if body is not None and body == {"config": {}}:
        pytest.skip("empty config object is shape-valid; covered by runtime tests")
    resp = await ca.config_upstream_update_api(_FakeRequest({"name": "beta"}, body))
    assert resp.status_code == 400


async def test_put_upstream_empty_config_accepted_shape(db_env):
    resp = await ca.config_upstream_update_api(
        _FakeRequest(
            {"name": "beta"},
            {"config": {}, "confirm_dangerous": True},
        )
    )
    # 形态合法 → 进入 fake server, 200
    assert resp.status_code == 200


async def test_put_upstream_requires_confirmation_400(db_env):
    _, server = db_env
    server.exc = DangerousConfirmationRequired(["upstream.config"])
    resp = await ca.config_upstream_update_api(
        _FakeRequest({"name": "beta"}, {"config": {"base_url": "http://x"}})
    )
    assert resp.status_code == 400
    assert _payload(resp)["error"] == "dangerous_confirmation_required"


async def test_routes_include_entity_edit_endpoints():
    # Starlette 给 GET 自动补 HEAD; 同路径两条 Route 按方法区分。
    routes = ca.build_upstream_routes()
    tool_get = next(
        r for r in routes if r.path == "/api/config/tools/{name}" and "GET" in r.methods
    )
    tool_put = next(
        r for r in routes if r.path == "/api/config/tools/{name}" and "PUT" in r.methods
    )
    upstream_get = next(
        r
        for r in routes
        if r.path == "/api/config/upstreams/{name}" and "GET" in r.methods
    )
    upstream_put = next(
        r
        for r in routes
        if r.path == "/api/config/upstreams/{name}" and "PUT" in r.methods
    )
    assert "HEAD" in tool_get.methods
    assert tool_put.methods == {"PUT"}
    assert "HEAD" in upstream_get.methods
    assert upstream_put.methods == {"PUT"}
