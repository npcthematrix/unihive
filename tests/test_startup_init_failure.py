"""Regression tests for startup-path failures (MCP startup audit 2026-09-07).

HIGH #1 (C1): when an upstream's start() raises a non-timeout exception
(e.g. FileNotFoundError because the binary doesn't exist), the client
must STILL be added to ``self.upstreams`` so that ``stop()`` can call
``client.stop()`` and release the subprocess / HTTP connection.

Before the fix: only ``asyncio.TimeoutError`` was caught, so any other
exception propagated up, the client was never added to ``self.upstreams``,
and the cleanup loop in the outer ``except Exception`` skipped it —
subprocess leaked.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from src.unihive.gateway_server import GatewayServer


def _make_server(tmp_path, *, command):
    """Build a GatewayServer whose only upstream launches a nonexistent binary.

    Uses the stdio branch (no `type` key) so UpstreamClient tries to spawn
    the command's first element as a subprocess. Pointing at a clearly-
    nonexistent name makes start() raise FileNotFoundError reliably.
    """
    cfg_path = tmp_path / "upstreams.yaml"
    cfg_path.write_text(
        "upstreams:\n"
        "  bad:\n"
        "    enabled: true\n"
        f"    command: {command!r}\n"
        "    package: ''\n"
        "    env: {}\n",
        encoding="utf-8",
    )
    return GatewayServer(
        config_path=str(cfg_path),
        strict_validation=False,  # don't reject for missing fields
        upstream_start_timeout=5.0,
    )


@pytest.mark.asyncio
async def test_failed_upstream_is_added_to_upstreams_for_cleanup(tmp_path):
    """Non-timeout exception from start() must register the client so stop() works.

    Pre-fix: client not in self.upstreams → stop() skipped it → leaked subprocess.
    Post-fix: client added with status marked unavailable, stop() reachable.
    """
    server = _make_server(
        tmp_path,
        command=["definitely_not_a_real_binary_xyz_unihive_test", "arg"],
    )
    # _do_initialize will try to start the upstream, get FileNotFoundError
    # (UpstreamClient.start catches and returns False; the broader fix still
    # ensures the client is registered for cleanup).
    try:
        await server._do_initialize()
    except Exception as e:
        pytest.fail(f"_do_initialize should not propagate the per-upstream "
                    f"FileNotFoundError; got {type(e).__name__}: {e}")

    assert "bad" in server.upstreams, (
        f"failed upstream must still be registered for cleanup; "
        f"got upstreams={list(server.upstreams.keys())}"
    )
    # Verify the cleanup path actually calls stop() on the failed client.
    stopped = []
    real_client = server.upstreams["bad"]
    real_stop = real_client.stop

    async def _recording_stop():
        stopped.append(True)
        await real_stop()

    real_client.stop = _recording_stop  # type: ignore[assignment]
    await server.stop()
    assert stopped, (
        "stop() must invoke client.stop() on the failed upstream; otherwise "
        "the spawned subprocess (if any) and any half-open handles leak"
    )