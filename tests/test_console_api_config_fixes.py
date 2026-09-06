"""Console API config-related logic tests.

覆盖本轮 review 提出的 HIGH/MEDIUM 修复:
- _is_secret_field 准确匹配 (避免 keyword/token_count 误伤, apikey 漏报)
- get_health 反映 gateway 真实可达性 (degraded / ok)
- load_config / get_interfaces 5s TTL 缓存
- gateway_http_port / mcp_url() 现取现算 (避免模块级常量 stale)
- validate_config 错误在 GatewayServer.__init__ 严格模式抛错
"""
from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ========== HIGH #2: _is_secret_field 精确匹配 ==========

class TestIsSecretField:
    """覆盖子串匹配遗留的误报 + 漏报."""

    @pytest.mark.parametrize("name", [
        "api_key",
        "API_KEY",
        "ApiKey",
        "apikey",
        "APIKEY",
        "access_token",
        "refresh_token",
        "bearer_token",
        "client_secret",
        "api_secret",
        "db_password",
        "db_credentials",
        "consumer_key",
        "private_key",
        "token",
        "secret",
        "password",
        "authorization",
    ])
    def test_secret_field_names_are_detected(self, name):
        from src.console_api import _is_secret_field
        assert _is_secret_field(name) is True, f"{name!r} 应当识别为密钥字段"

    @pytest.mark.parametrize("name", [
        "keyword",          # 子串 'KEY' 误伤
        "token_count",      # 子串 'TOKEN' 误伤
        "tokenwave_tdx",    # 'token' 是 tokenwave 前缀, 不是密钥
        "keystroke",        # 'key' 中间, 不是收尾
        "secret_keyword",   # secret 后接 _keyword 不应该 — 等等, 这个其实是 _keyword 后缀, 不命中
        "description",
        "base_url",
        "package",
        "command",
        "mode",
        "host",
        "port",
    ])
    def test_non_secret_field_names_are_not_detected(self, name):
        from src.console_api import _is_secret_field
        assert _is_secret_field(name) is False, f"{name!r} 不应识别为密钥字段"

    def test_empty_string_returns_false(self):
        from src.console_api import _is_secret_field
        assert _is_secret_field("") is False


class TestMaskSecretsConfig:
    """整段 config 通过 _mask_config_secrets 后, 密钥字段必须打码, 其它字段保持原样。"""

    def test_api_key_masked_but_description_intact(self):
        from src.console_api import _mask_config_secrets
        cfg = {
            "upstreams": {
                "fuyao": {
                    "type": "http",
                    "base_url": "https://example.com",
                    "api_key": "supersecret-abc-1234567890",
                    "description": "tokenwave_tdx upstream",
                }
            }
        }
        out = _mask_config_secrets(cfg)
        api_key = out["upstreams"]["fuyao"]["api_key"]
        description = out["upstreams"]["fuyao"]["description"]
        assert "supersecret" not in api_key
        assert "***" in api_key
        # description 中 tokenwave_tdx 的 'token' 是子串, 不应误伤
        assert description == "tokenwave_tdx upstream"

    def test_apikey_field_now_detected(self):
        """回归: 'apikey' (无下划线) 之前用子串匹配漏报, 现在精确匹配打码。"""
        from src.console_api import _mask_config_secrets
        cfg = {"creds": {"apikey": "abcdef1234567890"}}
        out = _mask_config_secrets(cfg)
        assert out["creds"]["apikey"] != "abcdef1234567890"
        assert "***" in out["creds"]["apikey"]


# ========== HIGH #3: GATEWAY_HTTP_PORT 现取现算 ==========

class TestGatewayPortLazy:
    """set_config() 注入晚于模块级常量求值 — 之前用模块级常量就拿不到新值."""

    def test_gateway_http_port_reflects_injected_config(self, tmp_path, monkeypatch):
        from src import console_api

        # 模拟 GatewayServer.__init__ 注入新 config, port=19999
        console_api.set_config({"gateway": {"port": 19999, "host": "10.0.0.1"}})

        assert console_api.gateway_http_port() == 19999
        assert console_api.gateway_host() == "10.0.0.1"
        assert console_api.mcp_url() == "http://10.0.0.1:19999/mcp"

    def test_falls_back_to_file_when_not_injected(self, tmp_path, monkeypatch):
        from src import console_api
        from src.config_loader import load_config as real_load

        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "gateway:\n  host: 5.5.5.5\n  port: 28888\n",
            encoding="utf-8",
        )

        # 重置注入状态, 强制走 file 路径
        monkeypatch.setattr(console_api, "_INJECTED", False)
        monkeypatch.setattr(console_api, "_GW_CFG", None)
        monkeypatch.setattr(console_api, "GATEWAY_CONFIG_PATH", str(cfg_file))

        assert console_api.gateway_http_port() == 28888
        assert console_api.gateway_host() == "5.5.5.5"


# 注: load_config 不再做缓存 — tool_loader.load_all_tools 会回写
# upstream_tool_mapping, 缓存会让写回跨调用泄漏到 /api/config 输出。
# get_interfaces 自己有缓存 (见下), 不影响 /api/interfaces 性能。


# ========== MEDIUM #6: get_interfaces 5s TTL 缓存 ==========

class TestGetInterfacesCached:
    def test_second_call_within_ttl_returns_same_object(self, monkeypatch, tmp_path):
        from src import console_api

        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "upstreams:\n  x:\n    type: http\n    base_url: http://a\n    api_key: k\n"
            "upstream_tool_mapping: {}\n"
            "routing: {}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(console_api, "GATEWAY_CONFIG_PATH", str(cfg_file))
        monkeypatch.setattr(console_api, "_interfaces_cache", type(console_api._interfaces_cache)(maxsize=1, ttl=5.0))
        monkeypatch.setattr(console_api, "_INJECTED", False)
        monkeypatch.setattr(console_api, "_GW_CFG", None)

        out1 = console_api.get_interfaces()
        out2 = console_api.get_interfaces()
        assert out1 is out2, "TTL 内应直接返回同一对象"


# ========== MEDIUM #9: get_health 反映可达性 ==========

class TestGetHealthReflectsReachability:
    def test_status_is_degraded_when_gateway_unreachable(self, monkeypatch):
        from src import console_api

        # 找一个空闲端口然后立刻关掉, 保证没监听
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        monkeypatch.setattr(console_api, "_gateway_port", lambda: port)
        monkeypatch.setattr(console_api, "_gateway_health_cache", {})

        health = console_api.get_health()
        assert health["status"] == "degraded"
        assert health["gateway_reachable"] is False

    def test_status_is_ok_when_gateway_listening(self, monkeypatch):
        from src import console_api

        # 起一个真监听的端口
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            monkeypatch.setattr(console_api, "_gateway_port", lambda: port)
            monkeypatch.setattr(console_api, "_gateway_health_cache", {})

            health = console_api.get_health()
            assert health["status"] == "ok"
            assert health["gateway_reachable"] is True
        finally:
            s.close()


# ========== HIGH #1: validate_config blocking via GatewayServer ==========

class TestValidateConfigBlocking:
    """GatewayServer.__init__ 在 strict_validation=True 时必须因 validate 错误抛错,
    而不是只 warning 然后带着错配置启动."""

    def test_init_raises_on_missing_base_url(self, tmp_path):
        from src.gateway_server import GatewayServer

        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "upstreams:\n"
            "  bad:\n"
            "    enabled: true\n"
            "    type: http\n"
            "    api_key: k\n",  # 缺 base_url
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Config validation failed"):
            GatewayServer(config_path=str(cfg_file), strict_env=False)

    def test_init_non_strict_only_warns(self, tmp_path):
        """strict_validation=False 时只 warning, 不抛 — 用于开发/调试场景."""
        from src.gateway_server import GatewayServer

        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "upstreams:\n"
            "  bad:\n"
            "    enabled: true\n"
            "    type: http\n"
            "    api_key: k\n",  # 缺 base_url
            encoding="utf-8",
        )

        server = GatewayServer(
            config_path=str(cfg_file),
            strict_env=False,
            strict_validation=False,
        )
        assert server.config is not None  # 初始化没抛

    def test_init_strict_passes_on_valid_config(self, tmp_path):
        from src.gateway_server import GatewayServer

        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "upstreams:\n"
            "  good:\n"
            "    enabled: true\n"
            "    type: http\n"
            "    base_url: http://x\n"
            "    api_key: k\n",
            encoding="utf-8",
        )

        server = GatewayServer(config_path=str(cfg_file), strict_env=False)
        assert server.config["upstreams"]["good"]["base_url"] == "http://x"
