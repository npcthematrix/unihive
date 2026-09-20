#!/usr/bin/env python3
"""UniHive Gateway 健康检查 — 探活运行中的网关进程。

行为:
  GET http://127.0.0.1:<port>/health (stdlib urllib, 无项目内依赖)。
  端口解析链与 scripts/start_gateway.ps1 一致:
    config/config.yaml gateway.port → config/upstreams.yaml gateway.port → 18080
  退出码:
    0  进程存活且 /health 200, status ∈ {ok, healthy, degraded}
       (degraded = 进程活着但部分上游降级, 进程级探活仍算通过)
    1  连不上 / 超时 / 非 200 / JSON 非法 / status=unhealthy|error 或缺字段

旧版 (2026-09-19 Round-12 前) `from upstream_client import ...` import 即崩,
且启用上游循环体为 pass, 无论真实状态恒返 0。

用法:
  python scripts/healthcheck.py                # 自动解析端口
  python scripts/healthcheck.py --port 19090
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # stdlib-only 降级: 跳过 YAML 端口解析, 用默认端口
    yaml = None  # type: ignore[assignment]

DEFAULT_PORT = 18080
REQUEST_TIMEOUT = 5.0
# 网关 /health 实际返回 "ok"/"degraded" (console_api.get_health);
# MCP get_server_status 工具返回 "healthy"/"degraded"/"unhealthy"。
# 两种词形都接受, 只要进程在应答即视为存活。
ALIVE_STATUSES = frozenset({"ok", "healthy", "degraded"})
DEAD_STATUSES = frozenset({"unhealthy", "error"})

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_YAML = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_UPSTREAMS_YAML = PROJECT_ROOT / "config" / "upstreams.yaml"


class HealthCheckError(RuntimeError):
    """探活失败 (连不上/非200/响应非法)。"""


def _read_gateway_port(path: str | Path) -> int | None:
    """从单个 YAML 读 gateway.port; 任何异常/非法值都返回 None (回退链语义)。"""
    if yaml is None:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw_port = (data.get("gateway") or {}).get("port")
        port = int(raw_port)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return None
    return port if 0 < port < 65536 else None


def resolve_port(
    config_yaml: str | Path = DEFAULT_CONFIG_YAML,
    upstreams_yaml: str | Path = DEFAULT_UPSTREAMS_YAML,
) -> int:
    """config.yaml → upstreams.yaml → 18080。"""
    for candidate in (config_yaml, upstreams_yaml):
        port = _read_gateway_port(candidate)
        if port is not None:
            return port
    return DEFAULT_PORT


def fetch_health(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout: float = REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """GET /health, 返回 JSON dict。失败抛 HealthCheckError。"""
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise HealthCheckError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise HealthCheckError(f"unreachable {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise HealthCheckError(f"unreachable {url}: {exc}") from exc

    try:
        payload = json.loads(body.decode(charset))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthCheckError(f"invalid JSON response from {url}") from exc
    if not isinstance(payload, dict):
        raise HealthCheckError(f"unexpected response type from {url}: {type(payload).__name__}")
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UniHive Gateway 健康检查")
    parser.add_argument("--port", type=int, default=None, help="网关端口 (默认读配置, 再回退 18080)")
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT, help="HTTP 超时秒数")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    port = args.port if args.port is not None else resolve_port()

    try:
        payload = fetch_health(port, timeout=args.timeout)
        status = payload.get("status")
    except HealthCheckError as exc:
        print(f"FAIL: {exc}")
        return 1

    if status in ALIVE_STATUSES:
        print(f"OK: gateway alive at 127.0.0.1:{port} (status={status})")
        return 0
    if status in DEAD_STATUSES:
        print(f"FAIL: gateway reports status={status} at 127.0.0.1:{port}")
        return 1
    print(f"FAIL: unexpected status={status!r} in /health payload")
    return 1


if __name__ == "__main__":
    sys.exit(main())
