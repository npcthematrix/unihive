"""配置审视批次3: 启动 / seed 韧性 (H4 + M9 + M10)。

- H4: 出厂模板在 seed 事务提交前必须过装配 + validate + lint 门,
  坏模板不得粘进空库 (修 YAML 后下次启动即可恢复);
- M9: db 模式下健康 config.db 不得被坏 YAML 预检基线拖死; 空库时仍 fail-closed;
  yaml 逃生模式保持原有严格语义;
- M10: config.db 路径锚定项目根, 防止从别的 CWD 启动造成"裂脑"空库重 seed。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from unihive.core.config_db import (
    ensure_seeded,
    project_root,
    resolve_config_db_path,
    seed_database,
)
from unihive.gateway_server import GatewayServer
from unihive.storage.config_store import ConfigStore
from unihive.utils import console_api as ca

REPO_ROOT = Path(__file__).resolve().parents[1]

_VALID_YAML = (
    "upstreams:\n"
    "  ok:\n"
    "    type: http\n"
    "    enabled: true\n"
    "    base_url: http://127.0.0.1:9/sse\n"
    "    api_key: ${M9_OK_KEY}\n"
    "upstream_tool_mapping: {}\n"
    "routing: {}\n"
)

# 结构损坏: 启用 http 上游缺 base_url (占位符本身通过明文密钥扫描)
_BROKEN_YAML = (
    "upstreams:\n"
    "  ok:\n"
    "    type: http\n"
    "    enabled: true\n"
    "    api_key: ${M9_OK_KEY}\n"
    "upstream_tool_mapping: {}\n"
    "routing: {}\n"
)


@pytest.fixture(autouse=True)
def _restore_ca_config():
    saved = ca._GW_CFG
    yield
    ca._GW_CFG = saved


async def _seed_healthy_db(db_path: Path, yaml_path: Path) -> None:
    yaml_path.write_text(_VALID_YAML, encoding="utf-8")
    store = ConfigStore(str(db_path))
    await store.initialize()
    await seed_database(store, yaml_path)
    await store.close()


# ========== H4: 坏模板不得粘库 ==========


async def test_h4_ensure_seeded_rejects_bad_template_without_persisting(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_BROKEN_YAML, encoding="utf-8")
    db = tmp_path / "config.db"
    store = ConfigStore(str(db))
    await store.initialize()

    with pytest.raises(ValueError, match="(?i)factory template|validation"):
        await ensure_seeded(store, cfg)

    # 库文件可能已建 (DDL/meta), 但实体行为空 —— 不粘
    assert await store.is_seeded() is False

    # 修复 YAML 后同库重试直接成功, 无需删库
    cfg.write_text(_VALID_YAML, encoding="utf-8")
    template = await ensure_seeded(store, cfg)
    assert {u.name for u in template.upstreams} == {"ok"}
    assert await store.is_seeded() is True
    await store.close()


async def test_h4_seed_database_cli_rejects_bad_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_BROKEN_YAML, encoding="utf-8")
    db = tmp_path / "config.db"
    store = ConfigStore(str(db))
    await store.initialize()

    with pytest.raises(Exception, match="(?i)factory template|validation"):
        await seed_database(store, cfg, force=True)

    assert await store.is_seeded() is False
    assert await store.revision() == 0
    await store.close()


# ========== M9: 健康 DB 不被坏 YAML 预检拖死 ==========


async def test_m9_db_mode_healthy_db_survives_structurally_broken_yaml(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    db = tmp_path / "config.db"
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(db))
    cfg = tmp_path / "upstreams.yaml"
    await _seed_healthy_db(db, cfg)
    monkeypatch.setenv("M9_OK_KEY", "k")

    # 出厂 YAML 被改坏 (启用 http 缺 base_url): 旧实现在 __init__ 直接 ValueError
    cfg.write_text(_BROKEN_YAML, encoding="utf-8")

    # strict_validation 默认 True; db 模式预检失败只告警, 不得抛出
    server = GatewayServer(str(cfg), strict_env=False)
    try:
        await server._load_db_config()
        # 运行态以库内健康配置为准
        assert server.config["upstreams"]["ok"]["base_url"] == (
            "http://127.0.0.1:9/sse"
        )
        assert server.config_store is not None
    finally:
        await server.stop()


async def test_m9_db_mode_healthy_db_survives_missing_env_preflight(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    db = tmp_path / "config.db"
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(db))
    cfg = tmp_path / "upstreams.yaml"
    await _seed_healthy_db(db, cfg)
    monkeypatch.setenv("M9_OK_KEY", "k")
    monkeypatch.delenv("M9_UNSET_VAR_XYZ", raising=False)

    # YAML 新增一个引用未设置环境变量的块; 库内实体没有它
    cfg.write_text(
        "upstreams:\n"
        "  ok:\n"
        "    type: http\n"
        "    enabled: true\n"
        "    base_url: http://127.0.0.1:9/sse\n"
        "    api_key: ${M9_OK_KEY}\n"
        "  extra:\n"
        "    type: omni\n"
        "    enabled: false\n"
        "    description: ${M9_UNSET_VAR_XYZ}\n"
        "upstream_tool_mapping: {}\n"
        "routing: {}\n",
        encoding="utf-8",
    )

    # strict_env=True: 旧实现在 load_config 预检阶段 KeyError, 网关根本起不来
    server = GatewayServer(str(cfg), strict_env=True)
    try:
        await server._load_db_config()
        assert "ok" in server.config["upstreams"]
        assert "extra" not in server.config["upstreams"]
    finally:
        await server.stop()


async def test_m9_yaml_mode_still_hard_fails_on_broken_yaml(tmp_path, monkeypatch):
    """逃生模式保持原语义: YAML 坏 = 启动失败。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNIHIVE_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(tmp_path / "config.db"))
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_BROKEN_YAML, encoding="utf-8")

    with pytest.raises(ValueError, match="Config validation failed"):
        GatewayServer(str(cfg), strict_env=False)


async def test_m9_db_mode_empty_db_still_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    db = tmp_path / "config.db"
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(db))
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_BROKEN_YAML, encoding="utf-8")

    server = GatewayServer(str(cfg), strict_env=False, strict_validation=False)
    try:
        with pytest.raises(Exception, match="(?i)factory template|validation"):
            await server._load_db_config()
        assert server.config_store is None
        # 空库未被坏模板污染, 修 YAML 后可恢复
        store = ConfigStore(str(db))
        await store.initialize()
        assert await store.is_seeded() is False
        await store.close()
    finally:
        await server.stop()


# ========== M10: config.db 路径锚定项目根 ==========


def test_m10_helper_anchors_default_under_project_root():
    assert resolve_config_db_path(None) == project_root() / "data" / "config.db"
    assert resolve_config_db_path("data/config.db") == (
        project_root() / "data" / "config.db"
    )


def test_m10_helper_relative_anchored_absolute_honored(tmp_path):
    assert resolve_config_db_path("sub/custom.db") == (
        project_root() / "sub" / "custom.db"
    )
    absolute = tmp_path / "explicit.db"
    assert resolve_config_db_path(absolute) == absolute


async def test_m10_gateway_default_db_path_independent_of_cwd(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    monkeypatch.delenv("UNIHIVE_CONFIG_DB", raising=False)
    monkeypatch.chdir(tmp_path)

    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_VALID_YAML, encoding="utf-8")
    monkeypatch.setenv("M9_OK_KEY", "k")

    server = GatewayServer(str(cfg), strict_env=False)
    try:
        db_path = Path(server._db_path)
        assert db_path.is_absolute()
        assert db_path == REPO_ROOT / "data" / "config.db"
    finally:
        await server.stop()


async def test_m10_gateway_relative_env_db_anchored_to_project_root(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("UNIHIVE_CONFIG_SOURCE", raising=False)
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", "data/sidecar-config.db")
    monkeypatch.chdir(tmp_path)

    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_VALID_YAML, encoding="utf-8")
    monkeypatch.setenv("M9_OK_KEY", "k")

    server = GatewayServer(str(cfg), strict_env=False)
    try:
        assert Path(server._db_path) == REPO_ROOT / "data" / "sidecar-config.db"
    finally:
        await server.stop()
