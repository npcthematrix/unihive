"""P3-6: 出厂更新 HTTP API (console_api 纯函数级, 不起 HTTP server)。

覆盖:
- GET  /api/config/template-status   模板 hash 信号 / build_error / yaml 模式;
- POST /api/config/upgrade/dry-run    resolutions 校验 + 503 + 异常映射;
- POST /api/config/upgrade/apply      confirm 强确认 + revision 校验 + 异常映射;
- GET  /api/config/upgrade/backup/latest  404 / 200 FileResponse / yaml 503。
"""
from __future__ import annotations

import json
import os

import pytest

from src.unihive.core import config_db
from src.unihive.core.config_lint import LintIssue, LintRejected
from src.unihive.storage.config_store import ConfigStore, RevisionConflict
from src.unihive.utils import console_api as ca

from tests.test_config_entity_api import _FakeRequest
from tests.test_config_store_v2 import BUNDLE, VERSION


class _FakeUpgradeServer:
    def __init__(self) -> None:
        self.preview_calls: list = []
        self.apply_calls: list = []
        self.exc: Exception | None = None

    async def preview_config_upgrade(self, resolutions=None):
        self.preview_calls.append(resolutions)
        if self.exc is not None:
            raise self.exc
        return {"changed": False, "resolutions_applied": sorted(resolutions or {})}

    async def apply_config_upgrade(self, *, resolutions=None, expected_revision=None):
        self.apply_calls.append(
            {"resolutions": resolutions, "expected_revision": expected_revision}
        )
        if self.exc is not None:
            raise self.exc
        return {
            "changed": True,
            "revision": 4,
            "backup_path": "/tmp/config-upgrade-x.json",
        }


@pytest.fixture
async def db_env(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    await store.seed_from_bundle(VERSION, BUNDLE)
    server = _FakeUpgradeServer()
    monkeypatch.setattr(ca, "_config_store", store)
    monkeypatch.setattr(ca, "_db_mode", True)
    monkeypatch.setattr(ca, "_gateway_server", server, raising=False)
    yield tmp_path, store, server
    await store.close()


def _payload(resp) -> dict:
    return json.loads(resp.body)


# --- GET template-status ----------------------------------------------------


async def test_template_status_db_mode_compares_hashes(db_env):
    resp = await ca.config_template_status_api(_FakeRequest({}))
    assert resp.status_code == 200
    body = _payload(resp)
    assert body["source"] == "db"
    assert body["revision"] == 0
    assert body["seeded_version"] == VERSION
    assert isinstance(body["current_version"], str)
    assert len(body["current_version"]) == 64
    # 合成 VERSION 不可能等于真实出厂模板 hash
    assert body["template_changed"] is True
    assert body["factory_counts"]["tools"] > 100
    assert body["factory_counts"]["upstreams"] >= 5
    assert "build_error" not in body


async def test_template_status_reports_build_error_without_500(db_env, monkeypatch):
    def _boom(_path):
        raise ValueError("Refusing to seed with plaintext secret fields: x")

    monkeypatch.setattr(config_db, "build_template", _boom)
    resp = await ca.config_template_status_api(_FakeRequest({}))
    assert resp.status_code == 200
    body = _payload(resp)
    assert body["source"] == "db"
    assert body["current_version"] is None
    assert body["template_changed"] is False
    assert "plaintext secret" in body["build_error"]


async def test_template_status_yaml_mode(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_template_status_api(_FakeRequest({}))
    assert resp.status_code == 200
    assert _payload(resp) == {"source": "yaml"}


# --- POST dry-run -----------------------------------------------------------


async def test_dry_run_empty_body_ok(db_env):
    _, _, server = db_env
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, None))
    assert resp.status_code == 200
    assert server.preview_calls == [{}]


async def test_dry_run_forwards_resolutions(db_env):
    _, _, server = db_env
    body = {"resolutions": {"tool:a_one:description": "accept_new"}}
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, body))
    assert resp.status_code == 200
    assert server.preview_calls == [{"tool:a_one:description": "accept_new"}]


@pytest.mark.parametrize(
    "body",
    [
        {"resolutions": []},
        {"resolutions": "x"},
        {"resolutions": {"k": "accept_old"}},
        {"resolutions": {12: "accept_new"}},
        "not-object",
    ],
)
async def test_dry_run_bad_resolutions_400(db_env, body):
    _, _, server = db_env
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, body))
    assert resp.status_code == 400
    assert server.preview_calls == []


async def test_dry_run_yaml_mode_503(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, {}))
    assert resp.status_code == 503


async def test_dry_run_no_server_503(db_env, monkeypatch):
    monkeypatch.setattr(ca, "_gateway_server", None, raising=False)
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, {}))
    assert resp.status_code == 503


async def test_dry_run_unresolved_conflict_maps_400(db_env):
    _, _, server = db_env
    server.exc = ValueError("2 unresolved field conflict(s): tool:a_one:description")
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, {}))
    assert resp.status_code == 400
    assert "unresolved field conflict" in _payload(resp)["error"]


async def test_dry_run_lint_rejected_maps_422(db_env):
    _, _, server = db_env
    server.exc = LintRejected([LintIssue("error", "M001", "unknown upstream")])
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, {}))
    assert resp.status_code == 422
    assert _payload(resp)["issues"][0]["code"] == "M001"


async def test_dry_run_revision_conflict_maps_409(db_env):
    _, _, server = db_env
    server.exc = RevisionConflict(expected=2, actual=5)
    resp = await ca.config_upgrade_dry_run_api(_FakeRequest({}, {}))
    assert resp.status_code == 409


# --- POST apply -------------------------------------------------------------


async def test_apply_requires_confirmation(db_env):
    _, _, server = db_env
    resp = await ca.config_upgrade_apply_api(_FakeRequest({}, {}))
    assert resp.status_code == 400
    body = _payload(resp)
    assert body["error"] == "dangerous_confirmation_required"
    assert server.apply_calls == []


async def test_apply_confirm_forwards_resolutions_and_revision(db_env):
    _, _, server = db_env
    body = {
        "confirm": True,
        "resolutions": {"tool:a_one:description": "keep_cur"},
        "revision": 7,
    }
    resp = await ca.config_upgrade_apply_api(_FakeRequest({}, body))
    assert resp.status_code == 200
    assert server.apply_calls == [
        {
            "resolutions": {"tool:a_one:description": "keep_cur"},
            "expected_revision": 7,
        }
    ]


async def test_apply_invalid_json_body_400(db_env):
    resp = await ca.config_upgrade_apply_api(_FakeRequest({}, None))
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"confirm": True, "revision": "7"},
        {"confirm": True, "revision": True},
        {"confirm": True, "resolutions": []},
        {"confirm": "true"},
        {"confirm": 1},
    ],
)
async def test_apply_bad_body_400(db_env, body):
    resp = await ca.config_upgrade_apply_api(_FakeRequest({}, body))
    assert resp.status_code == 400


async def test_apply_yaml_mode_503(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_upgrade_apply_api(
        _FakeRequest({}, {"confirm": True})
    )
    assert resp.status_code == 503


async def test_apply_no_server_503(db_env, monkeypatch):
    monkeypatch.setattr(ca, "_gateway_server", None, raising=False)
    resp = await ca.config_upgrade_apply_api(
        _FakeRequest({}, {"confirm": True})
    )
    assert resp.status_code == 503


async def test_apply_unresolved_conflict_400(db_env):
    _, _, server = db_env
    server.exc = ValueError("unresolved field conflict(s): x")
    resp = await ca.config_upgrade_apply_api(
        _FakeRequest({}, {"confirm": True})
    )
    assert resp.status_code == 400


async def test_apply_lint_rejected_422(db_env):
    _, _, server = db_env
    server.exc = LintRejected([LintIssue("error", "M001", "bad")])
    resp = await ca.config_upgrade_apply_api(
        _FakeRequest({}, {"confirm": True})
    )
    assert resp.status_code == 422


# --- GET backup -------------------------------------------------------------


async def test_backup_latest_none_404(db_env):
    resp = await ca.config_upgrade_backup_api(_FakeRequest({}))
    assert resp.status_code == 404


async def test_backup_latest_returns_newest_file(db_env):
    tmp_path, _, _ = db_env
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    older = backup_dir / "config-upgrade-20260101T000000Z-rev0.json"
    newer = backup_dir / "config-upgrade-20260918T120000Z-rev3.json"
    older.write_text('{"revision_before": 0}', encoding="utf-8")
    newer.write_text('{"revision_before": 3}', encoding="utf-8")
    # mtime 决定 "最新", 不依赖文件名排序
    os.utime(older, (newer.stat().st_mtime + 10, newer.stat().st_mtime + 10))

    resp = await ca.config_upgrade_backup_api(_FakeRequest({}))
    assert resp.status_code == 200
    assert resp.media_type == "application/json"
    assert resp.path.endswith("config-upgrade-20260101T000000Z-rev0.json")


async def test_backup_yaml_mode_503(monkeypatch):
    monkeypatch.setattr(ca, "_db_mode", False)
    monkeypatch.setattr(ca, "_config_store", None)
    resp = await ca.config_upgrade_backup_api(_FakeRequest({}))
    assert resp.status_code == 503


# --- routes -----------------------------------------------------------------


async def test_upgrade_routes_registered():
    routes = ca.build_upstream_routes()
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes}
    assert ("/api/config/template-status", ("GET", "HEAD")) in paths
    assert ("/api/config/upgrade/dry-run", ("POST",)) in paths
    assert ("/api/config/upgrade/apply", ("POST",)) in paths
    assert (
        "/api/config/upgrade/backup/latest",
        ("GET", "HEAD"),
    ) in paths
