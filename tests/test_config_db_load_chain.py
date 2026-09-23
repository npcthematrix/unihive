"""v2 网关加载链: config.db 实体表为事实源 (空库自动 seed / 装配 / fail-closed)。

与 test_config_db_golden.py 的分工: 后者锁死纯函数 normalize↔assemble 的
双向等价; 本文件测 GatewayServer._load_db_config 的编排 (env/注入/revision/
错误路径) 与 console_api 在实体模式下的同步读路径。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from unihive.core.config_db import seed_database
from unihive.gateway_server import GatewayServer
from unihive.storage.config_store import ConfigStore
from unihive.utils import console_api as ca

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAMS_YAML = REPO_ROOT / "config" / "upstreams.yaml"


@pytest.fixture
def db_mode(monkeypatch, tmp_path):
    """显式进入 v2 实体模式, 库指向 tmp (覆盖 autouse 的 yaml 默认)。"""
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    db_path = tmp_path / "config.db"
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(db_path))
    return db_path


async def test_empty_db_auto_seeds_and_assembles(db_mode):
    server = GatewayServer(str(UPSTREAMS_YAML), strict_env=False, strict_validation=False)
    try:
        await server._load_db_config()
        assert server.config_store is not None
        cfg = server.config
        assert len(cfg["upstreams"]) == 9
        assert len(cfg["tools"]) == 294
        # bootstrap 只来自 config.yaml。Round-13 起出厂模板不再携带 console
        # 凭证段 (固定凭证随公开仓库发布 = cookie 可伪造; 真实凭证走 .env /
        # UNIHIVE_CONSOLE_*), 空库 seed 的装配配置里不应出现 console 段。
        assert "console" not in cfg
        assert cfg["gateway"]["port"] == 18080
        # 库内调参段
        assert cfg["gateway"]["per_upstream_timeout"] == 10
        assert await server.config_store.revision() == 0

        # console_api 实体模式同步读: 不回读 YAML, 不携带 294 条 spec
        snap = ca.load_config()
        assert snap["version"] == 0
        assert "tools" not in snap
        assert "upstreams" in snap

        # interfaces/source_map 直接吃 live 装配 config, 数量与运行时门控一致
        iface_tools = ca.get_interfaces()["tools"]
        assert len(iface_tools) == len(server._load_all_tools())
        assert len(ca._build_source_map()) == 294

        # 空库自动 seed 已落盘: 第二次启动不再 seed
        await server.config_store.close()
        server2 = GatewayServer(str(UPSTREAMS_YAML), strict_env=False, strict_validation=False)
        try:
            await server2._load_db_config()
            assert len(server2.config["tools"]) == 294
            assert await server2.config_store.revision() == 0
        finally:
            await server2.stop()
    finally:
        await server.stop()


async def test_entity_edit_before_startup_is_assembled(db_mode):
    # 先用出厂模板灌库, 再改一个 upstream 实体行 (模拟上次控制台写后重启)
    store = ConfigStore(db_mode)
    await store.initialize()
    revision = await seed_database(store, UPSTREAMS_YAML)
    assert revision == 0
    revision = await store.update_upstream_enabled("THS-LOCAL-GATEWAY", False)
    assert revision == 1
    await store.close()

    server = GatewayServer(str(UPSTREAMS_YAML), strict_env=False, strict_validation=False)
    try:
        await server._load_db_config()
        block = server.config["upstreams"]["THS-LOCAL-GATEWAY"]
        assert block["enabled"] is False
        # 异型连接字段不丢
        assert block["type"] == "ths_local_gateway"
        assert "base_url" in block
        assert len(server.config["tools"]) == 294
        assert ca.load_config()["version"] == 1
        # 实体模式下 console_api 写接口直接打实体表
        persisted = await ca.save_upstream_enabled(
            "THS-LOCAL-GATEWAY", True, expected_version=1
        )
        assert persisted["version"] == 2
    finally:
        await server.stop()


async def test_assembled_config_validation_fail_closed(db_mode):
    # 灌库后直接破坏一个启用 http 上游的 config_json (抽掉 api_key),
    # 装配校验必须 fail-closed, 不留半开 store。
    store = ConfigStore(db_mode)
    await store.initialize()
    await seed_database(store, UPSTREAMS_YAML)
    async with await store._session() as session:
        row = (
            await session.execute(
                text("SELECT config_json FROM upstream WHERE name='fuyao_ashare'")
            )
        ).scalar_one()
        doc = json.loads(row)
        doc.pop("api_key", None)
        await session.execute(
            text("UPDATE upstream SET config_json=:c WHERE name='fuyao_ashare'"),
            {"c": json.dumps(doc, ensure_ascii=False, separators=(",", ":"))},
        )
        await session.commit()
    await store.close()

    server = GatewayServer(str(UPSTREAMS_YAML), strict_env=False, strict_validation=False)
    with pytest.raises(ValueError, match="DB config validation failed"):
        await server._load_db_config()
    assert server.config_store is None
    # 自初始化的 store 已关闭, stop 幂等
    await server.stop()


async def test_plaintext_secret_template_refuses_seed(tmp_path, monkeypatch):
    # 空库自动 seed 时发现出厂 YAML 含明文密钥 → ValueError, 拒绝落库
    upstreams = tmp_path / "upstreams.yaml"
    upstreams.write_text(
        "upstreams:\n"
        "  leaky:\n"
        "    type: http\n"
        "    enabled: true\n"
        "    base_url: http://127.0.0.1:9/sse\n"
        "    api_key: sk-literal-not-placeholder\n"
        "upstream_tool_mapping: {}\n"
        "routing: {}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(tmp_path / "config.db"))

    server = GatewayServer(str(upstreams), strict_env=False, strict_validation=False)
    with pytest.raises(ValueError, match="[Pp]laintext secret"):
        await server._load_db_config()
    assert server.config_store is None
    await server.stop()


async def test_yaml_mode_skips_db_entirely(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIHIVE_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(tmp_path / "config.db"))
    server = GatewayServer(str(UPSTREAMS_YAML), strict_env=False, strict_validation=False)
    try:
        await server._load_db_config()
        assert server.config_store is None
        assert len(server._load_all_tools()) == 294
        # 库文件根本没被创建
        assert not (tmp_path / "config.db").exists()
    finally:
        await server.stop()
