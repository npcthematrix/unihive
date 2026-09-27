"""apply_pool_config_edit 测试 (Phase E 2026-09-27)。

写池容量/Reader 闸门到 config.db, 需重启生效; 走 ConfigStore.
update_upstream_config (per-upstream 实体行) 而不是新 schema。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest


# ========== 最小 mock: ConfigStore + GatewayServer 子集 ==========

@dataclass
class _FakeUpstream:
    name: str
    type: str = "tdx_quant"
    enabled: bool = True
    config: dict | None = None

    def get(self, key, default=None):
        return getattr(self, key, default)


class _FakeStore:
    """最小 ConfigStore 模拟: 支持 read_bundle + update_upstream_config."""

    def __init__(self, upstreams, revision=0):
        self._ups = {u.name: u for u in upstreams}
        self._revision = revision

    async def read_bundle(self):
        return {
            "revision": self._revision,
            "upstreams": list(self._ups.values()),
        }

    async def update_upstream_config(
        self,
        name: str,
        config: dict,
        *,
        type=None,
        expected_revision: int | None = None,
    ) -> int:
        if expected_revision is not None and expected_revision != self._revision:
            from src.unihive.storage.config_store import RevisionConflict
            raise RevisionConflict(expected_revision, self._revision)
        existing = self._ups[name]
        if (existing.config or {}) == config:
            return self._revision  # no-op 短路
        existing.config = dict(config)
        self._revision += 1
        return self._revision


def _make_server(*, store, pool_cfg):
    from src.unihive.gateway_server import GatewayServer

    server = GatewayServer.__new__(GatewayServer)
    server.config_store = store
    server._pool_config = pool_cfg
    return server


def _make_pool_cfg(mootdx2_reader_limit=4, mootdx2_worker_threads=8, tdx_quant_worker_threads=10):
    from src.unihive.core.pool_config import PoolConfig

    return PoolConfig(
        mootdx2_reader_limit=mootdx2_reader_limit,
        mootdx2_worker_threads=mootdx2_worker_threads,
        tdx_quant_worker_threads=tdx_quant_worker_threads,
        sources={},
    )


# ========== 用例 ==========

class TestApplyPoolConfigEdit:
    async def test_persists_and_bumps_revision(self):
        store = _FakeStore(
            [
                _FakeUpstream("mootdx2", type="mootdx2", config={"timeout_seconds": 30}),
                _FakeUpstream("tdx_quant", type="tdx_quant", config={}),
            ],
            revision=5,
        )
        server = _make_server(
            store=store, pool_cfg=_make_pool_cfg(mootdx2_worker_threads=8),
        )
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        result = await ConfigEditsMixin.apply_pool_config_edit(
            server,
            fields={
                "mootdx2_reader_limit": 6,
                "mootdx2_worker_threads": 12,
                "tdx_quant_worker_threads": 20,
            },
        )
        # 两次 UPDATE bump rev 两次 (从 5 → 7)
        assert result["revision"] == 7
        assert result["persisted"]["mootdx2_worker_threads"] == 12
        # 落地后 store 内的 config_json 已包含新字段
        assert store._ups["mootdx2"].config["reader_limit"] == 6
        assert store._ups["mootdx2"].config["worker_threads"] == 12
        assert store._ups["tdx_quant"].config["worker_threads"] == 20

    async def test_preserves_existing_keys(self):
        """shallow-merge: 不破坏 seed 时已带的 timeout_seconds / custom env 字段."""
        store = _FakeStore([
            _FakeUpstream("mootdx2", config={"timeout_seconds": 30, "custom_env": "x"}),
            _FakeUpstream("tdx_quant", config={}),
        ])
        server = _make_server(store=store, pool_cfg=_make_pool_cfg())
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        await ConfigEditsMixin.apply_pool_config_edit(server, fields={
            "mootdx2_reader_limit": 2,
            "mootdx2_worker_threads": 4,
            "tdx_quant_worker_threads": 6,
        })
        # 既有 keys 仍在
        assert store._ups["mootdx2"].config["timeout_seconds"] == 30
        assert store._ups["mootdx2"].config["custom_env"] == "x"
        assert store._ups["mootdx2"].config["reader_limit"] == 2

    async def test_pending_restart_true_when_value_differs(self):
        """新值与 running 不同 → pending_restart=true."""
        store = _FakeStore([
            _FakeUpstream("mootdx2", config={}),
            _FakeUpstream("tdx_quant", config={}),
        ])
        # running: worker_threads=8; put: worker_threads=16 → 应该 pending
        server = _make_server(store=store, pool_cfg=_make_pool_cfg(mootdx2_worker_threads=8))
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        result = await ConfigEditsMixin.apply_pool_config_edit(server, fields={
            "mootdx2_reader_limit": 4,  # 同 running
            "mootdx2_worker_threads": 16,  # 不同于 running 8
            "tdx_quant_worker_threads": 10,  # 同 running
        })
        assert result["pending_restart"]["mootdx2_worker_threads"] is True
        assert result["pending_restart"]["mootdx2_reader_limit"] is False
        assert result["pending_restart"]["tdx_quant_worker_threads"] is False
        assert result["changed"] is True

    async def test_noop_when_unchanged(self):
        """PUT 与 running 全相等 → changed=false, pending_restart 全 False."""
        store = _FakeStore([
            _FakeUpstream("mootdx2", config={}),
            _FakeUpstream("tdx_quant", config={}),
        ])
        server = _make_server(
            store=store,
            pool_cfg=_make_pool_cfg(mootdx2_reader_limit=4, mootdx2_worker_threads=8, tdx_quant_worker_threads=10),
        )
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        result = await ConfigEditsMixin.apply_pool_config_edit(server, fields={
            "mootdx2_reader_limit": 4,
            "mootdx2_worker_threads": 8,
            "tdx_quant_worker_threads": 10,
        })
        assert result["changed"] is False
        assert all(v is False for v in result["pending_restart"].values())

    async def test_missing_field_raises_value_error(self):
        """缺字段 → ValueError → 400."""
        store = _FakeStore([_FakeUpstream("mootdx2"), _FakeUpstream("tdx_quant")])
        server = _make_server(store=store, pool_cfg=_make_pool_cfg())
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        with pytest.raises(ValueError, match="missing pool field"):
            await ConfigEditsMixin.apply_pool_config_edit(server, fields={
                "mootdx2_reader_limit": 4,
                "mootdx2_worker_threads": 8,
                # 缺 tdx_quant_worker_threads
            })

    async def test_value_below_one_raises(self):
        """<1 → ValueError → 400."""
        store = _FakeStore([_FakeUpstream("mootdx2"), _FakeUpstream("tdx_quant")])
        server = _make_server(store=store, pool_cfg=_make_pool_cfg())
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        with pytest.raises(ValueError, match="≥ 1"):
            await ConfigEditsMixin.apply_pool_config_edit(server, fields={
                "mootdx2_reader_limit": 0,
                "mootdx2_worker_threads": 8,
                "tdx_quant_worker_threads": 10,
            })

    async def test_missing_upstream_raises_key_error(self):
        """mootdx2 / tdx_quant 任一缺失 → KeyError → 404."""
        store = _FakeStore([_FakeUpstream("mootdx2")])  # 缺 tdx_quant
        server = _make_server(store=store, pool_cfg=_make_pool_cfg())
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        with pytest.raises(KeyError, match="tdx_quant"):
            await ConfigEditsMixin.apply_pool_config_edit(server, fields={
                "mootdx2_reader_limit": 4,
                "mootdx2_worker_threads": 8,
                "tdx_quant_worker_threads": 10,
            })

    async def test_revision_conflict_surfaces(self):
        """expected_revision 与 read_bundle().revision 不一致 → RevisionConflict → 409."""
        store = _FakeStore(
            [_FakeUpstream("mootdx2"), _FakeUpstream("tdx_quant")],
            revision=10,
        )
        server = _make_server(store=store, pool_cfg=_make_pool_cfg())
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        from src.unihive.storage.config_store import RevisionConflict
        with pytest.raises(RevisionConflict):
            await ConfigEditsMixin.apply_pool_config_edit(
                server,
                fields={
                    "mootdx2_reader_limit": 4,
                    "mootdx2_worker_threads": 8,
                    "tdx_quant_worker_threads": 10,
                },
                expected_revision=5,
            )

    async def test_no_config_store_raises_runtime_error(self):
        """yaml 模式 / config_store is None → RuntimeError → 503 (经 _entity_edit_error_response)."""
        server = _make_server(store=None, pool_cfg=_make_pool_cfg())
        from src.unihive.gateway_server.config_edits import ConfigEditsMixin
        with pytest.raises(RuntimeError, match="config.db"):
            await ConfigEditsMixin.apply_pool_config_edit(server, fields={
                "mootdx2_reader_limit": 4,
                "mootdx2_worker_threads": 8,
                "tdx_quant_worker_threads": 10,
            })