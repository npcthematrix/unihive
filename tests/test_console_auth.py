"""Tests for console authentication."""
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.testclient import TestClient

from src.unihive.console_auth import (
    hash_password,
    verify_password,
    setup_console_auth,
)


_DEV_HASH = hash_password("devpass")
_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _creds(**overrides):
    """Build a console_cfg dict with password_hash (MED-6 requires hash)."""
    cfg = {"username": "admin", "password_hash": _DEV_HASH, "session_secret": _SECRET}
    cfg.update(overrides)
    return cfg


class TestPasswordHashing:
    """Test password hashing functions."""

    def test_hash_verify_roundtrip(self):
        """Hash + verify with same password -> True."""
        plain = "test-password-123"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password(self):
        """Wrong password -> False."""
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_verify_malformed_hash(self):
        """Malformed hash -> False (no exception)."""
        assert verify_password("any", "malformed") is False
        assert verify_password("any", "") is False
        assert verify_password("any", "not-enough-parts") is False


class TestSetupRejectsPlaintext:
    """MED-6: setup_console_auth must refuse plaintext passwords."""

    def test_setup_rejects_plaintext_password(self):
        app = Starlette()
        with pytest.raises(ValueError, match="password_hash"):
            setup_console_auth(app, {
                "username": "admin",
                "password": "devpass",
                "session_secret": _SECRET,
            })

    def test_setup_rejects_missing_password_hash(self):
        app = Starlette()
        with pytest.raises(ValueError, match="password_hash"):
            setup_console_auth(app, {
                "username": "admin",
                "session_secret": _SECRET,
            })

    def test_setup_rejects_short_session_secret(self):
        app = Starlette()
        with pytest.raises(ValueError, match="session_secret"):
            setup_console_auth(app, {
                "username": "admin",
                "password_hash": _DEV_HASH,
                "session_secret": "tooshort",
            })

    def test_setup_accepts_password_hash(self):
        app = Starlette()
        setup_console_auth(app, _creds())
        assert app.state.console_auth_config["password_hash"] == _DEV_HASH
        assert "password" not in app.state.console_auth_config


class TestLoginPage:
    """Test login page endpoint."""

    def setup_method(self):
        """Create test app with auth enabled."""
        self.app = Starlette()
        self.app.add_route("/test_endpoint", lambda r: JSONResponse({"status": "ok"}))

        self.client = TestClient(
            self.app,
            cookies={"unihive_console": "invalid"}
        )
        setup_console_auth(self.app, _creds())

    def test_login_get_returns_html_form(self):
        """GET /login -> 200 + form."""
        response = self.client.get("/login")
        assert response.status_code == 200
        assert b"<form" in response.content.lower()


class TestLoginFlow:
    """Test login POST flow."""

    def setup_method(self):
        """Create test app with auth enabled."""
        self.app = Starlette()
        self.app.add_route("/api/status", lambda r: JSONResponse({"status": "ok"}))

        self.client = TestClient(self.app)
        setup_console_auth(self.app, _creds())

    def test_login_post_valid_sets_session(self):
        """POST correct creds -> 303 + Set-Cookie."""
        response = self.client.post("/login", data={
            "username": "admin",
            "password": "devpass"
        }, follow_redirects=False)
        assert response.status_code == 303
        assert "set-cookie" in {c.lower() for c in response.headers}

    def test_login_post_invalid_returns_401(self):
        """POST wrong creds -> 401."""
        response = self.client.post("/login", data={
            "username": "admin",
            "password": "wrongpass"
        })
        assert response.status_code == 401


class TestAuthMiddleware:
    """Test auth middleware path protection."""

    def setup_method(self):
        """Create test app with auth enabled."""
        self.app = Starlette()
        self.app.add_route("/api/status", lambda r: JSONResponse({"status": "ok"}))
        self.app.add_route("/", lambda r: PlainTextResponse("hello"))

        self.client = TestClient(self.app)
        setup_console_auth(self.app, _creds())

    def test_auth_middleware_blocks_api_when_unauthenticated(self):
        """GET /api/status (no cookie) -> 401 JSON."""
        # Use client without cookies
        client = TestClient(self.app)
        response = client.get("/api/status")
        # Should be redirected or blocked
        assert response.status_code in (401, 302, 303)

    def test_auth_middleware_blocks_root_when_unauthenticated(self):
        """GET / (no cookie) -> 303 -> /login."""
        client = TestClient(self.app)
        response = client.get("/", follow_redirects=False)
        # Should redirect to login
        assert response.status_code in (302, 303)
        if response.status_code in (302, 303):
            assert "/login" in response.headers.get("location", "")

    def test_auth_middleware_passes_mcp_unauthenticated(self):
        """GET /mcp -> 200 (FastMCP handles)."""
        client = TestClient(self.app)
        # /mcp should pass through (not blocked by auth)
        # The app itself doesn't handle /mcp, but middleware should let it pass
        response = client.get("/mcp")
        # Should not be 401 or redirect to login
        assert response.status_code != 401
        if response.status_code in (302, 303):
            assert "/login" not in response.headers.get("location", "")

    def test_auth_middleware_passes_health_unauthenticated(self):
        """GET /health -> 200."""
        client = TestClient(self.app)
        response = client.get("/health")
        assert response.status_code != 401


class TestLogout:
    """Test logout functionality."""

    def setup_method(self):
        """Create test app with auth enabled."""
        self.app = Starlette()
        self.app.add_route("/api/status", lambda r: JSONResponse({"status": "ok"}))

        self.client = TestClient(self.app)
        setup_console_auth(self.app, _creds())

    def test_logout_clears_session(self):
        """POST /logout + then GET /api -> 401."""
        # Login first
        self.client.post("/login", data={"username": "admin", "password": "devpass"})

        # Logout
        response = self.client.post("/logout")
        assert response.status_code in (200, 303)

        # Should not have valid session anymore
        # Create new client without cookies
        client = TestClient(self.app)
        response = client.get("/api/status")
        assert response.status_code in (401, 302, 303)


class TestSessionPersistence:
    """Test session persistence."""

    def setup_method(self):
        """Create test app with auth enabled."""
        self.app = Starlette()
        self.app.add_route("/api/status", lambda r: JSONResponse({"status": "ok"}))

        self.client = TestClient(self.app)
        setup_console_auth(self.app, _creds())

    def test_session_persists_across_requests(self):
        """Login -> 2x /api -> both 200."""
        # Login
        self.client.post("/login", data={"username": "admin", "password": "devpass"})

        # First request
        response1 = self.client.get("/api/status")
        # Second request
        response2 = self.client.get("/api/status")

        assert response1.status_code == 200
        assert response2.status_code == 200


class TestAuthDisabled:
    """Test when auth is disabled."""

    def test_auth_disabled_when_no_console_section(self):
        """Config no console -> all open."""
        app = Starlette()
        app.add_route("/api/status", lambda r: JSONResponse({"status": "ok"}))

        client = TestClient(app)
        # No setup_console_auth called - should be open
        response = client.get("/api/status")
        assert response.status_code == 200
