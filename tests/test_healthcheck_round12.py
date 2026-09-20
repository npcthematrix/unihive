"""Round-12: scripts/healthcheck.py 必须真实探活 /health。

旧版 import 即崩 (from upstream_client import ...), 且 enabled 上游 pass
恒返 healthy。新版: GET http://127.0.0.1:<port>/health, 按 HTTP/JSON 判退出码。
"""
from __future__ import annotations

import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("unihive_healthcheck", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hc():
    return _load_module()


class _Handler(BaseHTTPRequestHandler):
    status_code = 200
    payload = {"status": "healthy"}

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(self.payload).encode()
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int, *, status_code: int = 200, payload=None):
    handler = type(
        "H",
        (_Handler,),
        {"status_code": status_code, "payload": payload or {"status": "healthy"}},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_module_imports(hc):
    # 旧版 ModuleNotFoundError: No module named 'upstream_client'
    assert hasattr(hc, "main")


def test_healthy_returns_zero(hc):
    port = _free_port()
    server, thread = _serve(port, payload={"status": "ok", "gateway_reachable": True})
    try:
        assert hc.main(["--port", str(port)]) == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unhealthy_payload_returns_one(hc):
    port = _free_port()
    server, thread = _serve(port, payload={"status": "unhealthy", "upstreams": {}})
    try:
        assert hc.main(["--port", str(port)]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_error_returns_one(hc):
    port = _free_port()
    server, thread = _serve(port, status_code=500, payload={"error": "boom"})
    try:
        assert hc.main(["--port", str(port)]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_connection_refused_returns_one(hc):
    port = _free_port()  # 无监听
    assert hc.main(["--port", str(port)]) == 1


def test_degraded_treated_as_zero(hc):
    """degraded = 网关活着 (部分上游降级), 进程级健康检查应通过。"""
    port = _free_port()
    server, thread = _serve(port, payload={"status": "degraded"})
    try:
        assert hc.main(["--port", str(port)]) == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_malformed_json_returns_one(hc):
    port = _free_port()

    class RawHandler(_Handler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"not-json{")

    server = ThreadingHTTPServer(("127.0.0.1", port), RawHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert hc.main(["--port", str(port)]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resolve_port_prefers_config_yaml(tmp_path, hc):
    config_yaml = tmp_path / "config.yaml"
    upstreams_yaml = tmp_path / "upstreams.yaml"
    config_yaml.write_text("gateway:\n  port: 19090\n", encoding="utf-8")
    upstreams_yaml.write_text("gateway:\n  port: 17070\n", encoding="utf-8")
    assert hc.resolve_port(config_yaml, upstreams_yaml) == 19090


def test_resolve_port_fallback_chain_and_default(tmp_path, hc):
    upstreams_yaml = tmp_path / "upstreams.yaml"
    upstreams_yaml.write_text("gateway:\n  port: 17070\n", encoding="utf-8")
    missing = tmp_path / "nope.yaml"
    assert hc.resolve_port(missing, upstreams_yaml) == 17070
    assert hc.resolve_port(missing, tmp_path / "nope2.yaml") == 18080


def test_resolve_port_bad_yaml_falls_back(tmp_path, hc):
    bad = tmp_path / "config.yaml"
    bad.write_text("gateway: : :\n  port: not-yaml\n", encoding="utf-8")
    assert hc.resolve_port(bad, tmp_path / "nope.yaml") == 18080
