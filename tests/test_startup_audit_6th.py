"""Tests for 6th-round startup audit (H1/H2 + M1).

H1: Dead code in _execute_cached — a duplicated single-flight block sits
    after the finally: of the active request counter. The duplicated block
    is unreachable because the try block always returns before reaching it.
    This test catches the structural regression by asserting the source
    contains exactly one single-flight block in _execute_cached.

H2: Router.route() needs per-upstream timeout — without it, a hung upstream
    1 blocks the fallback chain until the outer 30s budget is exhausted.
    Pre-fix: a single hung upstream means upstreams 2/3/... never get tried.

M1: console_auth.py plain password comparison must use hmac.compare_digest
    to prevent timing attacks.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest


# ========== H1: dead-code structural guard ==========

class TestExecuteCachedNoDeadCode:
    def test_execute_cached_source_has_only_one_singleflight_block(self):
        """H1 (2026-09-08 6th-round audit): guard against the 5th-round
        regression where _execute_cached had two single-flight blocks —
        one canonical (inside the try) and one unreachable (after the
        finally). Inspect the AST and assert there's exactly one
        reference to self._in_flight_requests.get() at function body
        level.
        """
        from src.gateway_server import GatewayServer

        src = inspect.getsource(GatewayServer._execute_cached)
        # The canonical single-flight check uses self._in_flight_requests.get(...)
        # exactly once at the body level. The 5th-round regression had two.
        body_lines = [
            line for line in src.splitlines()
            if "self._in_flight_requests.get(" in line
        ]
        assert len(body_lines) == 1, (
            f"H1: _execute_cached has {len(body_lines)} references to "
            f"self._in_flight_requests.get(); expected exactly 1 "
            f"(duplicate single-flight block = dead code). "
            f"Found lines: {body_lines}"
        )

    def test_execute_cached_has_no_code_after_active_requests_decrement_finally(
        self,
    ):
        """H1: after the finally: block that decrements _active_requests,
        there must be no further executable statements at function-body
        indent. Code there is unreachable — the try always returns or
        re-raises.
        """
        import ast
        import textwrap

        from src.gateway_server import GatewayServer

        # inspect.getsource returns the method body with the class's
        # 4-space indent prefix; ast.parse needs module-level source.
        raw = inspect.getsource(GatewayServer._execute_cached)
        tree = ast.parse(textwrap.dedent(raw))
        func = tree.body[0]
        # Find the Try statement and check for siblings after it that are
        # not docstring/imports.
        for stmt in func.body:
            if isinstance(stmt, ast.Try):
                try_end = stmt.end_lineno
                siblings_after = [
                    s for s in func.body
                    if s.lineno > try_end and not isinstance(s, ast.Expr)
                ]
                assert siblings_after == [], (
                    f"H1: _execute_cached has reachable statements after "
                    f"the try/finally block (lines {try_end}+): "
                    f"{[s.lineno for s in siblings_after]}. The try block "
                    f"always returns or re-raises, so anything after it "
                    f"is dead code."
                )
                break
        else:
            pytest.fail("_execute_cached has no try block — cannot verify H1")


# ========== H2: per-upstream timeout ==========

class TestRouterPerUpstreamTimeout:
    async def test_hung_upstream1_does_not_block_fallback(self):
        """H2 (2026-09-08 6th-round audit): if upstream 1 hangs, upstream 2
        must still be tried. Pre-fix: Router.route() called upstream.call_tool
        with no per-call timeout, so a hung upstream 1 burned the entire
        outer 30s budget and upstream 2 never got tried.

        Post-fix: Router.route() wraps each upstream.call_tool in a
        configurable per-upstream timeout. Upstream 1 hangs past the
        budget → next upstream tried.
        """
        from src.router import Router

        @dataclass
        class _FakeResult:
            success: bool
            data: dict | None = None
            error: str | None = None

        class _HangUpstream:
            def __init__(self):
                self.calls = 0
                self.is_available = True

            async def call_tool(self, tool, params):
                self.calls += 1
                # Hang longer than the per-upstream timeout
                await asyncio.sleep(10)
                return _FakeResult(success=False, error="hung")

        class _GoodUpstream:
            def __init__(self):
                self.calls = 0
                self.is_available = True

            async def call_tool(self, tool, params):
                self.calls += 1
                return _FakeResult(success=True, data={"price": 100})

        hung = _HangUpstream()
        good = _GoodUpstream()

        router = Router(
            upstreams={"a": hung, "b": good},
            routing_config={"test_tool": {"chain": ["a", "b"]}},
            upstream_tool_mapping={"test_tool": {"a": "test_tool", "b": "test_tool"}},
        )

        # Configure a small per-upstream timeout via the constructor
        # (or default constant). The fix must let us override.
        start = time.time()
        result = await router.route(
            "test_tool",
            {"x": 1},
            per_upstream_timeout=0.5,  # type: ignore[arg-type]
        )
        elapsed = time.time() - start

        assert result.success is True, (
            f"H2: hung upstream 1 blocked fallback — good upstream 2 was "
            f"never tried. result.error={result.error}"
        )
        assert result.source == "b"
        assert hung.calls == 1
        assert good.calls == 1
        assert elapsed < 2.0, (
            f"H2: route took {elapsed:.2f}s — per-upstream timeout didn't fire"
        )

    async def test_per_upstream_timeout_falls_back_through_chain(self):
        """H2: three-upstream chain where 1 hangs, 2 returns failure,
        3 returns success. Per-upstream timeout fires on 1, the chain
        continues."""
        from src.router import Router

        @dataclass
        class _FakeResult:
            success: bool
            data: dict | None = None
            error: str | None = None

        class _HangUpstream:
            is_available = True
            async def call_tool(self, tool, params):
                await asyncio.sleep(10)
                return _FakeResult(success=False)

        class _FailUpstream:
            is_available = True
            async def call_tool(self, tool, params):
                return _FakeResult(success=False, error="upstream 2 says no")

        class _GoodUpstream:
            is_available = True
            async def call_tool(self, tool, params):
                return _FakeResult(success=True, data={"ok": True})

        router = Router(
            upstreams={"a": _HangUpstream(), "b": _FailUpstream(), "c": _GoodUpstream()},
            routing_config={"t": {"chain": ["a", "b", "c"]}},
            upstream_tool_mapping={"t": {n: "t" for n in "abc"}},
        )

        start = time.time()
        result = await router.route("t", {}, per_upstream_timeout=0.3)  # type: ignore[arg-type]
        elapsed = time.time() - start

        assert result.success is True
        assert result.source == "c"
        assert elapsed < 2.0

    async def test_all_upstreams_timeout_returns_failure(self):
        """H2: every upstream exceeds the per-upstream timeout — chain
        ends with a synthesized 'timeout' failure result."""
        from src.router import Router

        @dataclass
        class _FakeResult:
            success: bool
            error: str | None = None

        class _HangUpstream:
            is_available = True
            async def call_tool(self, tool, params):
                await asyncio.sleep(10)
                return _FakeResult(success=False)

        router = Router(
            upstreams={"a": _HangUpstream(), "b": _HangUpstream()},
            routing_config={"t": {"chain": ["a", "b"]}},
            upstream_tool_mapping={"t": {n: "t" for n in "ab"}},
        )

        result = await router.route("t", {}, per_upstream_timeout=0.2)  # type: ignore[arg-type]

        assert result.success is False
        # Hops recorded for both
        assert len(result.hops) == 2
        # Errors mention timeout
        all_errors = " ".join(h.error or "" for h in result.hops)
        assert "timeout" in all_errors.lower() or "timed out" in all_errors.lower()

    async def test_default_per_upstream_timeout_when_unspecified(self):
        """H2: when per_upstream_timeout is not passed, Router uses a
        sensible default (e.g. 10s). Verifies a non-hung upstream still
        completes successfully without explicit timeout.
        """
        from src.router import Router

        @dataclass
        class _FakeResult:
            success: bool
            data: dict | None = None
            error: str | None = None

        class _FastUpstream:
            is_available = True
            async def call_tool(self, tool, params):
                await asyncio.sleep(0.01)
                return _FakeResult(success=True, data={"ok": True})

        router = Router(
            upstreams={"a": _FastUpstream()},
            routing_config={"t": {"chain": ["a"]}},
            upstream_tool_mapping={"t": {"a": "t"}},
        )
        result = await router.route("t", {})
        assert result.success is True
        assert result.source == "a"


# ========== M1: console_auth timing-safe comparison ==========

class TestConsoleAuthTimingSafe:
    def test_console_auth_uses_hmac_compare_digest_for_plain_password(self):
        """M1 (2026-09-08 6th-round audit): console_auth.py plaintext
        password branch must use hmac.compare_digest(), not ==, to
        prevent timing attacks. Reads the source and asserts.
        """
        import inspect

        from src import console_auth

        # Find the function that does password checking
        # (it should reference both password_hash and password branches)
        src_module = inspect.getsource(console_auth)
        # The plain password check site is roughly the line that does
        # `if stored_password and password == stored_password:`. After
        # the fix it must use hmac.compare_digest.
        assert "hmac.compare_digest" in src_module, (
            "M1: console_auth.py module does not use hmac.compare_digest "
            "anywhere — must use it for both password_hash and plaintext "
            "password branches."
        )

    def test_console_auth_plain_password_branch_uses_constant_time(self):
        """M1: in console_auth.py, the plaintext branch (where
        stored_hash is None/empty) must call hmac.compare_digest on the
        password bytes. We assert by reading the source — behavioral test
        would require timing side-channels which we can't reliably assert.
        """
        import inspect
        import re

        from src import console_auth

        src = inspect.getsource(console_auth)
        # Look for the pattern: plain-password comparison site.
        # Must NOT contain `==` for password comparison (allow it for
        # other things like None checks).
        # The vulnerable pattern was something like
        #     if password == stored_password
        # The fixed pattern uses
        #     hmac.compare_digest(password.encode(...), stored_password.encode(...))
        bad_pattern = re.compile(r"password\s*==\s*stored_password")
        assert not bad_pattern.search(src), (
            "M1: console_auth.py still has `password == stored_password` "
            "comparison — replace with hmac.compare_digest() for "
            "timing-safe equality."
        )