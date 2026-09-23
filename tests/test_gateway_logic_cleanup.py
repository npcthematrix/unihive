"""gateway_server 功能审视 (2026-09-23) — C 组三个逻辑缺陷的回归测试。

C1: apply_config_upgrade 的 detach 循环遇 CancelledError 必须回滚后重抛
    (与 attach 侧 Round-9 M3 同守卫), 不得逃出锁留下半迁移;
C2: disabled reconcile 中, detach 只由 unpublish 失败门控; 不相关的
    republish 失败不得永久卡住禁用 client 的摘除;
C3: 带 late-recovery 钩子的 client (mootdx2/tdx_quant) start() 同步快失败
    时没有在途阻塞线程, UNKNOWN 必须无条件收敛为 UNAVAILABLE。
"""
from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.gateway_server import GatewayServer

from tests.test_config_store_v2 import BUNDLE
from tests.test_upstream_edit_hot import _make_beta_server, beta_store
from tests.test_upstream_enabled_toggle import _FakeClient, _FakeMCP


# ---------------------------------------------------------------------------
# C1: detach 期间 CancelledError → 回滚 + 传播
# ---------------------------------------------------------------------------


async def test_c1_cancel_during_detach_rolls_back_and_reraises(
    beta_store, monkeypatch
):
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)

    rollback_calls: list[dict] = []

    async def fake_rollback(**kwargs):
        rollback_calls.append(kwargs)

    server._rollback_config_upgrade_locked = fake_rollback  # type: ignore[assignment]

    async def boom_detach(name):
        raise asyncio.CancelledError()

    server._detach_upstream_client_locked = boom_detach  # type: ignore[assignment]

    # 假 plan, 避开真实 _prepare_config_upgrade 的 DB+YAML 读 (同 Round-9 M3 手法)。
    fake_result = MagicMock()
    fake_result.conflicts = []
    fake_result.bundle = copy.deepcopy(BUNDLE)
    fake_result.summary = MagicMock(return_value={
        "added": [], "removed": [], "updated": [{"path": "beta"}],
    })
    fake_plan = {
        "resolutions": {},
        "revision": 0,
        "old_version": "v0",
        "new_version": "v1",
        "new_bundle": copy.deepcopy(BUNDLE),
        "result": fake_result,
        "summary": fake_result.summary.return_value,
        "changed": True,
        "candidate": copy.deepcopy(server.config),
        "candidate_specs": list(server.tool_specs),
        "candidate_hidden": set(),
        "lint_errors": [],
        "lint_warnings": [],
    }
    server._prepare_config_upgrade = AsyncMock(return_value=fake_plan)  # type: ignore[assignment]

    # beta 需要 detach; 工具侧零差集。
    monkeypatch.setattr(
        server, "_upgrade_upstream_impact", lambda candidate: ([], [], ["beta"])
    )
    monkeypatch.setattr(
        server,
        "_upgrade_tool_impact",
        lambda old_s, new_s, c, h: (set(), set(), []),
    )
    monkeypatch.setattr(
        server, "_unpublish_tools_locked", lambda names, errors: []
    )
    monkeypatch.setattr(
        server, "_publish_tools_locked", lambda names, errors: []
    )
    monkeypatch.setattr(
        server, "_raise_if_toggle_errors", lambda label, errors: None
    )

    # 落库不应到达 (cancel 先于落库)。
    monkeypatch.setattr(
        server.config_store, "apply_upgrade_bundle", AsyncMock(return_value=1)
    )

    with pytest.raises(asyncio.CancelledError):
        await server.apply_config_upgrade(resolutions=None, expected_revision=0)

    assert rollback_calls, "CancelledError during detach must trigger rollback"
    # beta 必须记入切换清单, 回滚才知道要复原它。
    assert "beta" in rollback_calls[0]["switched_upstreams"]


# ---------------------------------------------------------------------------
# C2: disabled reconcile — republish 失败不挡 detach
# ---------------------------------------------------------------------------


async def test_c2_disabled_reconcile_detaches_despite_republish_failure(
    monkeypatch,
):
    server = GatewayServer.__new__(GatewayServer)
    server.config = {
        "upstreams": {"A": {"enabled": False, "type": "http"}},
        "upstream_tool_mapping": {},
        "routing": {},
    }
    server.tool_specs = [
        {"name": "multi_t", "routing": "multi_t", "params": []},
    ]
    server.mcp = _FakeMCP()
    server._published_tools: set[str] = set()
    server._upstream_toggle_lock = asyncio.Lock()
    client = _FakeClient("A")
    server.upstreams = {"A": client}

    # 多 backer 工具 multi_t 失去本上游后仍应可经其他 backer 发布 → 差集里只有 to_add。
    server._toggle_tool_diff_locked = lambda name: ({"multi_t"}, set())  # type: ignore[assignment]

    async def _noop_cancel_late_starts(name=None):
        pass

    server._cancel_late_starts = _noop_cancel_late_starts  # type: ignore[assignment]

    async def _fake_stop(name, c):
        await c.stop()

    server._stop_one_upstream = _fake_stop  # type: ignore[assignment]

    # 补发在 provider 侧失败 (模拟注册异常)。
    def boom_register(srv, spec):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        "src.unihive.core.registry.register_tool", boom_register
    )

    with pytest.raises(RuntimeError, match="partial failure") as exc:
        await server._reconcile_upstream_locked(
            "A", server.config["upstreams"]["A"], False
        )
    assert "publish multi_t" in str(exc.value)

    # 核心: unpublish 侧无错误, 即使补发失败 client 也必须摘除 (修复前被误挡,
    # 禁用 client 永久残留)。
    assert "A" not in server.upstreams
    assert client.stopped is True


# ---------------------------------------------------------------------------
# C3: 钩子 client start() 同步快失败 → UNKNOWN 落 UNAVAILABLE
# ---------------------------------------------------------------------------


class _HookedClient:
    """mootdx2/tdx_quant 同构替身: 带 start_after_timeout 钩子, 初始 UNKNOWN。"""

    def __init__(self) -> None:
        self._status = UpstreamStatus.UNKNOWN
        # 钩子存在即被 _settle_unavailable_if_unknown 默认跳过。
        self.start_after_timeout = object()

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    async def start(self) -> None:
        raise FileNotFoundError("tdx dll missing")


async def test_c3_hooked_client_fast_start_failure_settles_unavailable():
    server = GatewayServer.__new__(GatewayServer)
    server.upstreams = {}
    server._upstream_start_timeout = 30

    client = _HookedClient()
    connected = await server._start_upstream_client(
        "tdx_quant", {"type": "http"}, client
    )

    assert connected is False
    assert client.status is UpstreamStatus.UNAVAILABLE
    # client 仍登记, stop() 可清理。
    assert server.upstreams["tdx_quant"] is client
