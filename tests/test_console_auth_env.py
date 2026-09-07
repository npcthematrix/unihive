"""Unit tests for console auth env var wiring (HIGH #4)."""
from __future__ import annotations

import pytest

from src.gateway_server import _console_auth_config_from_env


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

    def test_disabled_when_only_user_set(self):
        import os
        os.environ["UNIHIVE_CONSOLE_USER"] = "admin"
        cfg, reason = _console_auth_config_from_env()
        assert cfg is None
        assert "missing" in reason
        assert "UNIHIVE_CONSOLE_PASSWORD" in reason
        assert "UNIHIVE_CONSOLE_SESSION_SECRET" in reason

    def test_disabled_when_user_and_password_only(self):
        import os
        os.environ["UNIHIVE_CONSOLE_USER"] = "admin"
        os.environ["UNIHIVE_CONSOLE_PASSWORD"] = "secret"
        cfg, reason = _console_auth_config_from_env()
        assert cfg is None
        assert "missing" in reason
        assert "UNIHIVE_CONSOLE_SESSION_SECRET" in reason

    def test_disabled_when_secret_too_short(self):
        import os
        os.environ["UNIHIVE_CONSOLE_USER"] = "admin"
        os.environ["UNIHIVE_CONSOLE_PASSWORD"] = "secret"
        os.environ["UNIHIVE_CONSOLE_SESSION_SECRET"] = "short"
        cfg, reason = _console_auth_config_from_env()
        assert cfg is None
        assert "32 characters" in reason

    def test_enabled_when_all_three_set(self):
        import os
        os.environ["UNIHIVE_CONSOLE_USER"] = "admin"
        os.environ["UNIHIVE_CONSOLE_PASSWORD"] = "secret"
        os.environ["UNIHIVE_CONSOLE_SESSION_SECRET"] = _SECRET_32
        cfg, reason = _console_auth_config_from_env()
        assert cfg is not None, f"expected enabled; reason={reason!r}"
        assert cfg["username"] == "admin"
        assert cfg["password"] == "secret"
        assert cfg["session_secret"] == _SECRET_32

    def test_strips_whitespace(self):
        import os
        os.environ["UNIHIVE_CONSOLE_USER"] = "  admin  "
        os.environ["UNIHIVE_CONSOLE_PASSWORD"] = "  secret  "
        os.environ["UNIHIVE_CONSOLE_SESSION_SECRET"] = _SECRET_32
        cfg, _ = _console_auth_config_from_env()
        assert cfg["username"] == "admin"
        assert cfg["password"] == "secret"

    def test_empty_string_treated_as_unset(self):
        """Empty string is treated the same as missing — opt-in via setting them."""
        import os
        os.environ["UNIHIVE_CONSOLE_USER"] = ""
        os.environ["UNIHIVE_CONSOLE_PASSWORD"] = ""
        os.environ["UNIHIVE_CONSOLE_SESSION_SECRET"] = ""
        cfg, reason = _console_auth_config_from_env()
        assert cfg is None
        assert "no UNIHIVE_CONSOLE_*" in reason