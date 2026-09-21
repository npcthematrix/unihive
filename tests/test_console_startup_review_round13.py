"""Round-13 console 启动审视 — 实现后三方审查 (TS/Python/安全) 修复批。

审查方: 前端 TypeScript / 后端 Python / 安全 (OWASP) 三个独立 reviewer。
前端项在 web/ 下 vitest 覆盖; 本文件覆盖后端/部署侧确认项:

- 安全 H1: 出厂模板 host 回到回环; 非回环绑定 + 鉴权关闭必须显著告警
  (簇K 去掉出厂凭证后, "0.0.0.0 + 默认无鉴权" 变成同网段未认证可写)。
- 安全 M1: config/config.yaml 解析失败不得被宽 except 吞成静默开放;
  env 完整配置可作为逃生路径, 否则 fail-closed (退出码 2)。
- 安全 M2/L5 + 后端 M2: console 字段非字符串 (int/bool) 显式拒绝;
  弱口令集 (admin/admin123/123456/password/与用户名相同) env 直查、
  YAML 哈希 verify; enabled: false 显式关闭语义。
- 安全 L1: 配置解析错误日志/报错不回显出错行内容 (可能含密钥)。
- 后端 L2: 超大分页整数不得在 sqlite int64 溢出成 500, 边界 400。
- 后端 L3: mcp_path 白名单字符集, 拒绝控制字符/空格/反斜杠/点段。
- 后端 M3 收尾: entities/metrics/hot-toggle 500 不回显内部异常文本。
- 后端 L5: console 解析尊重 --config 锚定的配置目录 (config_yaml_path)。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.unihive.console_auth import (
    ConsoleAuthConfigError,
    hash_password,
    verify_password,
)
from src.unihive.gateway_server._helpers import (
    _console_auth_config_from_env,
    _get_console_auth_config,
)
from src.unihive.gateway_server.http import (
    is_non_loopback_host,
    validate_mcp_path,
)
from src.unihive.utils import console_api
from src.unihive.utils.config_loader import validate_config

ROOT = Path(__file__).resolve().parent.parent
_FRESH_SECRET = "fresh-secret-not-the-factory-default-0123456789abcd"


# ---------------------------------------------------------------------------
# 安全 H1: 默认回环 + 非回环开放绑定告警
# ---------------------------------------------------------------------------


class TestLoopbackDefault:
    def test_factory_config_bind_host_is_explicit(self):
        """出厂模板 host 必须显式声明。

        历史上出厂默认回环 127.0.0.1; 2026-09 用户显式决定改为 0.0.0.0 以
        允许局域网访问 (commit 9cd91fb, 同时关闭 Host/Origin 白名单)。
        补偿控制是启动时非回环 + 无鉴权显著 WARNING
        (见 test_serve_http_warns_when_open_and_non_loopback),
        而非把 host 悄悄留空或写成不可识别的值。
        """
        import yaml

        text = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        host = (yaml.safe_load(text) or {})["gateway"]["host"]
        assert host in {"127.0.0.1", "0.0.0.0"}, (
            f"出厂模板 host 必须显式为回环或全网卡, 得到 {host!r}"
        )

    @pytest.mark.parametrize(
        "host,expected",
        [
            ("127.0.0.1", False),
            ("127.0.0.8", False),
            ("::1", False),
            ("localhost", False),
            ("0.0.0.0", True),
            ("::", True),
            ("192.168.1.10", True),
            ("10.0.0.5", True),
            ("255.255.255.255", True),
            # 无法判定的主机名按 "可能对外暴露" 处理 (只影响是否打告警)
            ("gateway.local", True),
        ],
    )
    def test_is_non_loopback_host(self, host, expected):
        assert is_non_loopback_host(host) is expected

    def test_serve_http_warns_when_open_and_non_loopback(self):
        """告警必须在鉴权关闭分支内, 文案点明 /api/* 无鉴权可达。"""
        import inspect

        from src.unihive.gateway_server import HttpServeMixin

        src = inspect.getsource(HttpServeMixin.serve_http)
        else_idx = src.index("else:")
        branch_end = src.index("make_mcp_path_canonicalizer", else_idx)
        disabled_branch = src[else_idx:branch_end]
        assert "is_non_loopback_host" in disabled_branch
        assert "WARNING" in disabled_branch or "logger.warning" in disabled_branch


# ---------------------------------------------------------------------------
# 安全 M1 + L1: config.yaml 解析失败不再静默开放, 且不回显出错行
# ---------------------------------------------------------------------------


BROKEN_YAML_WITH_SECRET_LINE = (
    "console:\n"
    "  session_secret: \"abcdefghijklmnopqrstuvwxyz1234567890-DO-NOT-ECHO\n"
    "  username: admin\n"
    "   bad-indent: [\n"
)


class TestBrokenConfigYamlFailClosed:
    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_broken_yaml_without_env_raises(self, tmp_path, monkeypatch, caplog):
        for var in (
            "UNIHIVE_CONSOLE_USER",
            "UNIHIVE_CONSOLE_PASSWORD",
            "UNIHIVE_CONSOLE_SESSION_SECRET",
        ):
            monkeypatch.delenv(var, raising=False)
        path = self._write(tmp_path, BROKEN_YAML_WITH_SECRET_LINE)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(ConsoleAuthConfigError, match="config/config.yaml|config.yaml"):
                _get_console_auth_config({}, config_yaml_path=path)
        # L1: 出错行内容 (可能含密钥) 不进异常消息也不进日志
        assert "DO-NOT-ECHO" not in str(caplog.text)

    def test_broken_yaml_complete_env_is_escape_hatch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "ops")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "a-distinct-pw-7q2x")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _FRESH_SECRET)
        path = self._write(tmp_path, BROKEN_YAML_WITH_SECRET_LINE)
        cfg, reason = _get_console_auth_config({}, config_yaml_path=path)
        assert reason == "ok"
        assert cfg is not None and cfg["username"] == "ops"

    def test_broken_yaml_partial_env_still_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "ops")
        monkeypatch.delenv("UNIHIVE_CONSOLE_PASSWORD", raising=False)
        monkeypatch.delenv("UNIHIVE_CONSOLE_SESSION_SECRET", raising=False)
        path = self._write(tmp_path, BROKEN_YAML_WITH_SECRET_LINE)
        with pytest.raises(ConsoleAuthConfigError):
            _get_console_auth_config({}, config_yaml_path=path)

    def test_explicit_console_section_in_explicit_config_dir_is_honored(self, tmp_path):
        """L5: 传 config_yaml_path 时从该路径读 (尊重 --config 锚定目录),
        而不是写死的 layout 根。"""
        fresh_hash = hash_password("distinct-pw-9f3k")
        path = self._write(
            tmp_path,
            "console:\n"
            "  username: ops2\n"
            f"  password_hash: {fresh_hash!r}\n"
            f"  session_secret: {_FRESH_SECRET!r}\n",
        )
        cfg, reason = _get_console_auth_config({}, config_yaml_path=path)
        assert reason == "ok"
        assert cfg["username"] == "ops2"


# ---------------------------------------------------------------------------
# 安全 M2: 非字符串字段显式拒绝 (不靠 AttributeError 歪打正着)
# ---------------------------------------------------------------------------


class TestNonStringConsoleFields:
    @pytest.mark.parametrize(
        "section",
        [
            {"username": 7, "password_hash": "x", "session_secret": "y" * 40},
            {"username": "admin", "password_hash": 42, "session_secret": "y" * 40},
            {"username": "admin", "password_hash": "x", "session_secret": True},
        ],
    )
    def test_non_string_scalar_raises_clean_error(self, section, tmp_path):
        # 走显式路径 + 不存在的 config.yaml, 锚定 step2 的字段校验
        missing = tmp_path / "absent-config.yaml"
        with pytest.raises(ConsoleAuthConfigError, match="字符串|string"):
            _get_console_auth_config(
                {"console": section}, config_yaml_path=missing
            )

    def test_lint_flags_non_string_fields(self):
        errors = validate_config(
            {
                "upstreams": {"a": {"type": "omni", "enabled": True}},
                "console": {
                    "username": 7,
                    "password_hash": "x",
                    "session_secret": "y" * 40,
                },
            }
        )
        assert any("字符串|string" in e or "username" in e for e in errors)


# ---------------------------------------------------------------------------
# 弱口令集 (env 明文直查, YAML 哈希 verify)
# ---------------------------------------------------------------------------


class TestWeakPasswords:
    @pytest.mark.parametrize("weak", ["admin", "admin123", "123456", "password"])
    def test_env_weak_password_rejected(self, monkeypatch, weak):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "ops")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", weak)
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _FRESH_SECRET)
        with pytest.raises(ConsoleAuthConfigError, match="weak|弱|常见"):
            _console_auth_config_from_env()

    def test_env_password_equal_username_rejected(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "same-pw-user")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "same-pw-user")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _FRESH_SECRET)
        with pytest.raises(ConsoleAuthConfigError):
            _console_auth_config_from_env()

    @pytest.mark.parametrize("weak", ["admin", "admin123", "123456", "password"])
    def test_yaml_hash_of_weak_password_rejected(self, weak, tmp_path):
        section = {
            "username": "admin",
            "password_hash": hash_password(weak),
            "session_secret": "z" * 40,
        }
        with pytest.raises(ConsoleAuthConfigError, match="weak|弱|常见|default"):
            _get_console_auth_config(
                {"console": section},
                config_yaml_path=tmp_path / "absent.yaml",
            )

    def test_strong_password_accepted(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "ops")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "a-strong-uncommon-pw-8W")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _FRESH_SECRET)
        cfg, reason = _console_auth_config_from_env()
        assert reason == "ok"
        assert verify_password("a-strong-uncommon-pw-8W", cfg["password_hash"])


# ---------------------------------------------------------------------------
# enabled: false 显式关闭语义
# ---------------------------------------------------------------------------


class TestConsoleEnabledFlag:
    def test_enabled_false_with_credentials_is_disabled(self, tmp_path):
        section = {
            "enabled": False,
            "username": "admin",
            "password_hash": hash_password("whatever-7q"),
            "session_secret": "z" * 40,
        }
        cfg, reason = _get_console_auth_config(
            {"console": section}, config_yaml_path=tmp_path / "absent.yaml"
        )
        assert cfg is None
        assert "disabled" in reason

    def test_enabled_non_bool_raises(self, tmp_path):
        section = {"enabled": "yes", "username": "a"}
        with pytest.raises(ConsoleAuthConfigError, match="布尔|bool"):
            _get_console_auth_config(
                {"console": section}, config_yaml_path=tmp_path / "absent.yaml"
            )

    def test_enabled_true_still_requires_full_config(self, tmp_path):
        with pytest.raises(ConsoleAuthConfigError):
            _get_console_auth_config(
                {"console": {"enabled": True, "username": "a"}},
                config_yaml_path=tmp_path / "absent.yaml",
            )

    def test_lint_skips_when_explicitly_disabled(self):
        errors = validate_config(
            {
                "upstreams": {"a": {"type": "omni", "enabled": True}},
                "console": {"enabled": False},
            }
        )
        assert not [e for e in errors if "console" in e]


# ---------------------------------------------------------------------------
# L4: env 口令不 strip (首尾空格是合法口令字符); 纯空白按未设置处理
# ---------------------------------------------------------------------------


class TestEnvPasswordPreserved:
    def test_leading_trailing_spaces_preserved(self, monkeypatch):
        pw = "  a-password-with-spaces  "
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "ops")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", pw)
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _FRESH_SECRET)
        cfg, reason = _console_auth_config_from_env()
        assert reason == "ok"
        assert verify_password(pw, cfg["password_hash"])
        assert not verify_password(pw.strip(), cfg["password_hash"])

    def test_whitespace_only_password_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "ops")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "   ")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _FRESH_SECRET)
        with pytest.raises(ConsoleAuthConfigError, match="UNIHIVE_CONSOLE_PASSWORD"):
            _console_auth_config_from_env()


# ---------------------------------------------------------------------------
# L3: mcp_path 字符集/点段
# ---------------------------------------------------------------------------


class TestMcpPathCharset:
    @pytest.mark.parametrize(
        "raw",
        [
            "/mcp\n",
            "/m?p",
            "/m c p",
            "/m\\cp",
            "/m\tcp",
            "/api/../mcp",
            "/./mcp",
        ],
    )
    def test_rejects_control_space_backslash_dot_segments(self, raw):
        with pytest.raises(ValueError):
            validate_mcp_path(raw)

    @pytest.mark.parametrize("raw", ["/mcp", "/api/mcp", "/mcp-v1/a_b"])
    def test_accepts_normal_paths(self, raw):
        assert validate_mcp_path(raw) == raw.rstrip("/")


# ---------------------------------------------------------------------------
# L2: 超大分页整数 → 400 (不进 sqlite int64 溢出成 500)
# ---------------------------------------------------------------------------


class TestPaginationIntCap:
    @pytest.mark.parametrize("value", ["99999999999999999999999", "99999999999", "2147483648"])
    def test_overflow_integer_returns_400(self, value):
        _value, bad = console_api._positive_int_param(
            {"page": value}, "page", 1
        )
        assert bad is not None
        assert bad.status_code == 400

    @pytest.mark.parametrize("value", ["1", "100", "2147483647"])
    def test_in_range_accepted(self, value):
        parsed, bad = console_api._positive_int_param(
            {"page": value}, "page", 1
        )
        assert bad is None
        assert parsed == int(value)


# ---------------------------------------------------------------------------
# M3 收尾: 500 响应体不回显内部异常文本
# ---------------------------------------------------------------------------


class _ExplodingEntitiesStore:
    async def entities_summary(self):
        raise RuntimeError("sqlite error at /secret/internal/path: disk boom")


class _FakeReq:
    def __init__(self, name: str | None = None):
        self.path_params = {"name": name} if name is not None else {}


class TestErrorBodiesFixedCodes:
    @pytest.mark.asyncio
    async def test_entities_summary_failure_fixed_code(self, monkeypatch):
        monkeypatch.setattr(console_api, "_db_mode", True, raising=False)
        monkeypatch.setattr(
            console_api, "_config_store", _ExplodingEntitiesStore(), raising=False
        )
        resp = await console_api.config_entities_api(_FakeReq())
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "sqlite" not in body and "secret" not in body and "disk" not in body
        assert json.loads(body) == {"error": "entities_load_failed"}

    @pytest.mark.asyncio
    async def test_metrics_failure_fixed_code(self, monkeypatch):
        class _BoomClient:
            def get_metrics(self):
                raise RuntimeError("internal /secret/path leak")

        monkeypatch.setattr(
            console_api, "_get_upstream_client", lambda name: _BoomClient()
        )
        resp = await console_api.upstream_metrics_api(_FakeReq("mootdx2"))
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "secret" not in body and "leak" not in body
        assert json.loads(body) == {"error": "metrics_read_failed"}

    def test_runtime_toggle_failure_fixed_code_source_contract(self):
        """热切换 500 分支必须用固定错误码; 源码契约防回归。"""
        import inspect

        src = inspect.getsource(console_api.upstream_enabled_api)
        assert "runtime_toggle_failed" in src
        assert 'f"runtime toggle failed: {e}"' not in src
