"""P4: FQGate OpenAPI 实时导入 — gateway preview/apply 热编排。

覆盖:
- preview: added/changed/unchanged/unmapped/removed 台账, 发布差集
  (新增 add / 契约变化 republish), YAML 片段, 零写入;
- apply: 契约层强确认 → 备份 → 运行态切换 → provider 摘/注 → 单事务
  upsert_tools (origin='user'), 成功后 revision +1;
- 上游未注册 / 已配置但未连接 / 拉取失败 的错误分类;
- 白名单选择 + 未知工具名拒绝;
- provider 失败 / 乐观锁冲突 → 运行态回滚, 库不动;
- 二次导入 (cur 已与上游一致) no-op。
绕过 GatewayServer.__init__ (同 test_config_upgrade_hot 手法)。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.unihive.api.mcp_tools_fetch import RawToolsResult
from src.unihive.api.ths_local_gateway_client import OpenApiFetchError
from src.unihive.core import openapi_import as oi
from src.unihive.core.config_lint import LintRejected
from src.unihive.core.db_assembler import assemble_config, entities_from_json
from src.unihive.core.entity_edit import DangerousConfirmationRequired
from src.unihive.core.router import Router
from src.unihive.core.tool_loader import filter_specs_by_env
from src.unihive.gateway_server import GatewayServer
from src.unihive.storage.config_store import ConfigStore, RevisionConflict

from tests.test_upstream_enabled_toggle import _FakeMCP

UPSTREAM = "THS-LOCAL-GATEWAY"


def _openapi_doc() -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/v1/market/health": {"get": {"responses": {}}},
            "/v1/market/realtime/quote": {
                "post": {
                    "parameters": [
                        {"name": "detail", "in": "query", "required": False,
                         "schema": {"type": "boolean"}, "description": "明细"},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/QuoteReq"},
                            }
                        }
                    },
                    "responses": {},
                }
            },
        },
        "components": {
            "schemas": {
                "QuoteReq": {
                    "type": "object",
                    "required": ["code"],
                    "properties": {
                        "code": {"type": "string", "description": "证券代码。"},
                    },
                }
            }
        },
    }


def _mcp_tools() -> list[dict]:
    return [
        {"name": "fqgate_market_market_health", "description": "健康检查",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "fqgate_quote",
         "description": "实时报价。POST /v1/market/realtime/quote。",
         "inputSchema": {
             "type": "object",
             "properties": {
                 "code": {"type": "string", "description": "代码"},
                 "requestTimeoutMs": {"type": "integer",
                                      "description": "超时(毫秒)"},
             },
             "required": ["code"],
         }},
        {"name": "fqgate_lonely", "description": "无 REST 提示也无手工映射",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


BUNDLE = {
    "sections": {},
    "upstreams": [
        {"name": UPSTREAM, "type": "ths_local_gateway", "enabled": True,
         "config": {"base_url": "http://127.0.0.1:17281/mcp"}},
    ],
    "tools": [
        {
            "name": "fqgate_quote",
            "routing": "fqgate_quote",
            "description": "旧版描述",
            "cache_ttl_key": None,
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {UPSTREAM: "fqgate_quote"},
            "spec": {
                "name": "fqgate_quote",
                "routing": "fqgate_quote",
                "description": "旧版描述",
                "params": [],
                "upstream_tool_mapping": {UPSTREAM: "fqgate_quote"},
                "data_source_type": "online",
            },
            "source_file": "tools_ths_local.yaml",
            "origin": "builtin",
        },
    ],
}
VERSION = "c" * 64


class _FakeThsClient:
    """ThsLocalClient 测试替身: 只实现导入用到的两个拉取方法。"""

    def __init__(self, *, openapi=None, tools=None, fail: Exception | None = None):
        self._openapi = openapi if openapi is not None else _openapi_doc()
        self._tools = tools if tools is not None else _mcp_tools()
        self._fail = fail

    async def fetch_openapi(self) -> dict:
        if self._fail is not None:
            raise self._fail
        return self._openapi

    async def list_tools_raw(self, **kwargs):
        if self._fail is not None:
            raise self._fail
        return RawToolsResult(ok=True, tools=self._tools, pages=1)


@pytest.fixture(autouse=True)
async def _isolate_console_api_globals():
    import src.unihive.utils.console_api as ca_mod

    saved = {
        "_config_store": ca_mod._config_store,
        "_db_mode": ca_mod._db_mode,
        "_db_revision": ca_mod._db_revision,
        "_GW_CFG": ca_mod._GW_CFG,
        "_INJECTED": ca_mod._INJECTED,
    }
    yield
    ca_mod._config_store = saved["_config_store"]
    ca_mod._db_mode = saved["_db_mode"]
    ca_mod._db_revision = saved["_db_revision"]
    ca_mod._GW_CFG = saved["_GW_CFG"]
    ca_mod._INJECTED = saved["_INJECTED"]
    ca_mod._interfaces_cache.clear()
    ca_mod._mcp_tools_cache.clear()


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    await s.seed_from_bundle(VERSION, BUNDLE)
    yield s
    await s.close()


async def _make_server(store, *, client=None):
    import src.unihive.gateway_server as gs_mod

    server = GatewayServer.__new__(GatewayServer)
    server._db_mode = True
    server.config_store = store
    server._strict_env = False
    server.config_path = None

    bundle = await store.load_bundle()
    template = entities_from_json(bundle)
    config = assemble_config(template)
    server.config = config
    server.tool_specs = filter_specs_by_env(list(config["tools"]))
    server._db_hidden_tools = set()
    server.mcp = _FakeMCP()
    server.upstreams = {UPSTREAM: client or _FakeThsClient()}
    server.router = Router(
        upstreams={},
        routing_config=config.get("routing", {}),
        upstream_tool_mapping=config.get("upstream_tool_mapping", {}),
        tool_specs=server.tool_specs,
    )
    server._upstream_toggle_lock = asyncio.Lock()
    for spec in server.tool_specs:
        gs_mod.register_tool(server, spec)
    server.mcp._tools = {"fqgate_quote"}
    server._published_tools = {"fqgate_quote"}
    server.mcp.tool_calls.registered.clear()
    server.mcp.removed.clear()
    return server


# --------------------------------------------------------------- preview


async def test_preview_classifies_and_computes_impact(store):
    server = await _make_server(store)
    preview = await server.preview_openapi_import()

    assert preview["changed"] is True
    assert preview["upstream"] == UPSTREAM
    assert preview["summary"] == {
        "added": 1, "changed": 1, "unchanged": 0,
        "removed": 0, "unmapped": 1,
    }
    by_name = {c["name"]: c for c in preview["changes"]}
    assert by_name["fqgate_market_market_health"]["action"] == "added"
    assert by_name["fqgate_quote"]["action"] == "changed"
    assert "spec" in by_name["fqgate_quote"]["changed_fields"]
    assert preview["unmapped"][0]["name"] == "fqgate_lonely"

    impact = preview["impact"]
    assert impact["tools_added"] == ["fqgate_market_market_health"]
    assert impact["tools_republished"] == ["fqgate_quote"]
    assert impact["tools_removed"] == []
    # 导入不碰上游连接
    assert impact["upstreams_attach"] == []
    assert impact["upstreams_reconnect"] == []
    assert impact["upstreams_detach"] == []

    assert "fqgate_market_market_health" in preview["yaml_fragment"]
    assert preview["lint_errors"] == []
    # dry-run 零副作用
    assert "fqgate_market_market_health" not in server._published_tools
    assert await store.get_tool("fqgate_market_market_health") is None
    assert await store.revision() == 0


async def test_preview_selection_whitelist(store):
    server = await _make_server(store)
    preview = await server.preview_openapi_import(tools=["fqgate_market_market_health"])
    assert preview["selected"] == ["fqgate_market_market_health"]
    assert preview["impact"]["tools_republished"] == []
    assert "fqgate_quote" not in preview["yaml_fragment"]


async def test_preview_unknown_selected_name_raises(store):
    server = await _make_server(store)
    with pytest.raises(ValueError):
        await server.preview_openapi_import(tools=["fqgate_nope"])


async def test_preview_unknown_upstream_raises_keyerror(store):
    server = await _make_server(store)
    with pytest.raises(KeyError):
        await server.preview_openapi_import(upstream="NOPE")


async def test_preview_disconnected_upstream_raises(store):
    server = await _make_server(store, client=_FakeThsClient())
    server.upstreams = {}
    with pytest.raises(RuntimeError, match="not connected"):
        await server.preview_openapi_import()


async def test_preview_wrong_upstream_type_rejected(store):
    server = await _make_server(store)
    server.upstreams = {UPSTREAM: object()}  # 无 fetch_openapi/list_tools_raw
    with pytest.raises(ValueError, match="OpenAPI"):
        await server.preview_openapi_import()


async def test_preview_fetch_failure_propagates(store):
    server = await _make_server(
        store, client=_FakeThsClient(fail=OpenApiFetchError("boom"))
    )
    with pytest.raises(OpenApiFetchError):
        await server.preview_openapi_import()


# --------------------------------------------------------------- apply


async def test_apply_requires_dangerous_confirmation(store):
    server = await _make_server(store)
    with pytest.raises(DangerousConfirmationRequired):
        await server.apply_openapi_import()
    # 未确认: 零副作用
    assert await store.get_tool("fqgate_market_market_health") is None
    assert "fqgate_market_market_health" not in server._published_tools


async def test_apply_publishes_upserts_and_backs_up(store):
    server = await _make_server(store)
    result = await server.apply_openapi_import(confirm_dangerous=True)

    assert result["changed"] is True
    assert result["revision"] == 1
    assert result["tools_added"] == ["fqgate_market_market_health"]
    assert result["tools_republished"] == ["fqgate_quote"]
    assert result["upstreams_attached"] == []
    assert "backup_path" in result and result["backup_path"]

    # provider: changed 先摘后注, added 新注
    assert server.mcp.removed == ["fqgate_quote"]
    assert {n for n, _ in server.mcp.tool_calls.registered} == {
        "fqgate_market_market_health", "fqgate_quote",
    }
    assert server._published_tools == {"fqgate_market_market_health", "fqgate_quote"}

    # 库: origin=user / source_file=openapi-import, 契约已刷新
    health = await store.get_tool("fqgate_market_market_health")
    assert health["origin"] == "user"
    assert health["source_file"] == "openapi-import"
    assert health["mapping"] == {UPSTREAM: "fqgate_market_market_health"}
    quote = await store.get_tool("fqgate_quote")
    assert quote["origin"] == "user"
    assert quote["source_file"] == "openapi-import"
    assert [p["name"] for p in quote["spec"]["params"]] == [
        "detail", "code", "requestTimeoutMs",
    ]

    # 运行态 config 已含新工具
    live_names = {s["name"] for s in server.tool_specs}
    assert {"fqgate_quote", "fqgate_market_market_health"} <= live_names

    # 备份落盘 (apply 前 cur)
    import json

    backups = list(
        (Path(store.db_path).parent / "backups").glob("config-upgrade-*.json")
    )
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert [t["name"] for t in payload["bundle"]["tools"]] == ["fqgate_quote"]


async def test_apply_second_run_is_noop(store):
    server = await _make_server(store)
    await server.apply_openapi_import(confirm_dangerous=True)
    server.mcp.removed.clear()
    server.mcp.tool_calls.registered.clear()

    result = await server.apply_openapi_import(confirm_dangerous=True)
    assert result["changed"] is False
    assert result["revision"] == 1
    assert server.mcp.removed == []
    assert server.mcp.tool_calls.registered == []
    assert await store.revision() == 1


async def test_apply_selection_applies_only_whitelist(store):
    server = await _make_server(store)
    result = await server.apply_openapi_import(
        tools=["fqgate_market_market_health"], confirm_dangerous=True
    )
    assert result["tools_added"] == ["fqgate_market_market_health"]
    assert result["tools_republished"] == []
    assert server.mcp.removed == []
    quote = await store.get_tool("fqgate_quote")
    assert quote["origin"] == "builtin"
    assert quote["description"] == "旧版描述"


# ----------------------------------------------------- rollback / conflicts


async def test_apply_provider_failure_rolls_everything_back(store, monkeypatch):
    import src.unihive.gateway_server as gs_mod

    server = await _make_server(store)
    real_register_tool = gs_mod.register_tool

    def selective_boom(srv, spec):
        # 只让新增工具发布失败; 回滚路径要重注旧版 fqgate_quote, 若一并
        # boom, 回滚补发会失败并把 quote 移出 _published_tools, 无法验证全量复位。
        if spec["name"] == "fqgate_market_market_health":
            raise RuntimeError("provider exploded")
        return real_register_tool(srv, spec)

    monkeypatch.setattr(gs_mod, "register_tool", selective_boom)
    with pytest.raises(RuntimeError, match="openapi-import"):
        await server.apply_openapi_import(confirm_dangerous=True)

    assert await store.revision() == 0
    assert await store.get_tool("fqgate_market_market_health") is None
    assert "fqgate_market_market_health" not in server._published_tools
    assert "fqgate_quote" in server._published_tools
    assert all(s["name"] != "fqgate_market_market_health" for s in server.tool_specs)
    old_quote = next(s for s in server.tool_specs if s["name"] == "fqgate_quote")
    assert old_quote["description"] == "旧版描述"


async def test_apply_stale_revision_conflicts_and_rolls_back(store):
    server = await _make_server(store)
    with pytest.raises(RevisionConflict):
        await server.apply_openapi_import(
            confirm_dangerous=True, expected_revision=99
        )
    assert await store.revision() == 0
    assert await store.get_tool("fqgate_market_market_health") is None
    assert "fqgate_market_market_health" not in server._published_tools
    assert "fqgate_quote" in server._published_tools
    assert all(s["name"] != "fqgate_market_market_health" for s in server.tool_specs)


async def test_apply_lint_error_blocks_before_backup(store):
    """prep 产出 lint error → LintRejected, 备份/运行态/库均不动。"""
    from unihive.core.config_lint import LintIssue

    server = await _make_server(store)

    async def _fake_prepare(*_args, **_kwargs):
        prep = await GatewayServer._prepare_openapi_import(server, UPSTREAM, None)
        prep["lint_errors"] = [
            LintIssue(severity="error", code="T_TEST", message="synthetic")
        ]
        return prep

    # 实例属性遮蔽绑定方法 (apply 内 self._prepare_openapi_import(...) 会命中)
    server._prepare_openapi_import = _fake_prepare
    with pytest.raises(LintRejected):
        await server.apply_openapi_import(confirm_dangerous=True)
    assert await store.revision() == 0
    assert "fqgate_market_market_health" not in server._published_tools
    assert not (Path(store.db_path).parent / "backups").exists()
