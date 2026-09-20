"""Unit tests for console auth env var wiring (HIGH #4).

Round-13 console 启动审视: "显式但不完整" 的 console 配置由 fail-open
(静默禁用) 改为 fail-closed (抛 ConsoleAuthConfigError 拒启); 只有三个
变量全不设才是真正的"未启用"。

注意: 一律走 monkeypatch.setenv —— 早期版本用裸 os.environ 写入,
依赖 autouse fixture 的 delenv undo 间接清理; 该写法会在极少数
fixture/undo 顺序下把短 secret 泄漏给后续进程内用例 (factory config.yaml
console 段移除后 env 成为唯一来源, 泄漏即可见)。
"""
from __future__ import annotations

import pytest

from src.unihive.console_auth import ConsoleAuthConfigError
from src.unihive.gateway_server import _console_auth_config_from_env


@pytest.fixture(autouse=True)
def clean_console_env(monkeypatch):
    """Strip UNIHIVE_CONSOLE_* before/after each test so we don't leak between tests."""
    for var in (
        "UNIHIVE_CONSOLE_USER",
        "UNIHIVE_CONSOLE_PASSWORD",
        "UNIHIVE_CONSOLE_SESSION_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


_SECRET_32 = "0" * 32  # ≥32 chars, valid


class TestConsoleAuthEnv:
    def test_disabled_when_no_env_vars_set(self):
        cfg, reason = _console_auth_config_from_env()
        assert cfg is None
        assert "no UNIHIVE_CONSOLE_*" in reason

    def test_failclosed_when_only_user_set(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "admin")
        with pytest.raises(ConsoleAuthConfigError) as ei:
            _console_auth_config_from_env()
        msg = str(ei.value)
        assert "UNIHIVE_CONSOLE_PASSWORD" in msg
        assert "UNIHIVE_CONSOLE_SESSION_SECRET" in msg

    def test_failclosed_when_user_and_password_only(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "admin")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "secret")
        with pytest.raises(ConsoleAuthConfigError) as ei:
            _console_auth_config_from_env()
        assert "UNIHIVE_CONSOLE_SESSION_SECRET" in str(ei.value)

    def test_failclosed_when_secret_too_short(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "admin")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "secret")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", "short")
        with pytest.raises(ConsoleAuthConfigError, match="32 characters"):
            _console_auth_config_from_env()

    def test_failclosed_known_default_secret_rejected(self, monkeypatch):
        # 出厂公开常量即便被显式配置也拒绝 (防止照抄旧 config.yaml)
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "admin")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "secret")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", "0123456789abcdef" * 4)
        with pytest.raises(ConsoleAuthConfigError, match="default|known|public"):
            _console_auth_config_from_env()

    def test_enabled_when_all_three_set(self, monkeypatch):
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "admin")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "secret")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", _SECRET_32)
        cfg, reason = _console_auth_config_from_env()
        assert cfg is not None, f"expected enabled; reason={reason!r}"
        assert cfg["username"] == "admin"
        # H4 (round 9 audit): env 路径就地 hash 明文 → password_hash (PBKDF2),
        # 不再把明文塞 cfg (setup_console_auth 拒绝明文, 旧实现会让 serve_http 崩)。
        assert "password" not in cfg
        assert cfg["password_hash"].startswith("pbkdf2_sha256$")
        # 哈希必须能验证明文
        from src.unihive.console_auth import verify_password

        assert verify_password("secret", cfg["password_hash"]) is True
        assert cfg["session_secret"] == _SECRET_32

    def test_strips_whitespace_user_but_preserves_password(self, monkeypatch):
        # round13 L4: username/secret 仍 strip; password 逐字保留 —— 尾部空格
        # 可能是口令的真实组成部分, 静默 strip 会让 "配置时录入" 与 "登录时录入"
        # 产生难以排查的不一致。纯空白 password 仍按 "未设置" 处理 (fail-closed)。
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "  admin  ")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "  secret  ")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", "  " + _SECRET_32 + "  ")
        cfg, _ = _console_auth_config_from_env()
        assert cfg["username"] == "admin"
        assert cfg["session_secret"] == _SECRET_32
        assert "password" not in cfg
        from src.unihive.console_auth import verify_password

        # 逐字 (含首尾空格) 才能验证通过; strip 后的形式必须失败。
        assert verify_password("  secret  ", cfg["password_hash"]) is True
        assert verify_password("secret", cfg["password_hash"]) is False

    def test_empty_string_treated_as_unset(self, monkeypatch):
        """Empty string is treated the same as missing — opt-in via setting them."""
        monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "")
        monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "")
        monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", "")
        cfg, reason = _console_auth_config_from_env()
        assert cfg is None
        assert "no UNIHIVE_CONSOLE_*" in reason
