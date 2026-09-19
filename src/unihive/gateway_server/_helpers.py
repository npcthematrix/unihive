"""gateway_server 包的模块级工具函数与常量。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持):
- 同步日志脱敏 (路径/token)
- pyproject 版本号读取
- mootdx2 servers 配置解析
- console 鉴权配置 (config.yaml / upstreams.yaml / env 三源)

facade ``__init__.py`` re-export 这里的全部公开名字, 历史导入路径
(``from unihive.gateway_server import _parse_mootdx2_servers`` 等) 不变。
"""
import logging
import os
import re
from pathlib import Path

import yaml

from ..core.config_db import resolve_layout_root

logger = logging.getLogger(__name__)


# 同步日志里可能带回栈/路径/token, 写库前脱敏.
# 规则: Windows 绝对路径 → [PATH], 长 token (eyJ/sk-/hex32+) → [TOKEN], 单行超 500 字符截断.

# 非 raw 字符串: \\ 在 regex 里就是字面反斜杠
_PATH_RE = re.compile("[A-Za-z]:[\\\\/](?:[^\\s\\/:|*?\"<>]+[\\\\/])+[^\\s\\/:|*?\"<>]+")
_TOKEN_RE = re.compile(r"\b(?:eyJ[A-Za-z0-9_.-]{20,}|sk-[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")


def _sanitize_sync_message(msg: str) -> str:
    if not msg:
        return ""
    s = _PATH_RE.sub("[PATH]", msg)
    s = _TOKEN_RE.sub("[TOKEN]", s)
    if len(s) > 500:
        s = s[:497] + "..."
    return s


def _read_gateway_version() -> str:
    """读取 pyproject.toml 里的版本号，给 FastMCP 当 serverInfo.version。

    不依赖 importlib.metadata (开发环境不一定 pip install -e .),
    直接读源码根的 pyproject.toml。读不到时退回到 'unknown'，避免启动崩。
    """
    try:
        pyproject = resolve_layout_root(Path(__file__)) / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception as e:
        logger.warning(f"failed to read gateway version from pyproject.toml: {e}")
    return "unknown"


def _parse_mootdx2_servers(raw_servers: list) -> list:
    """解析 upstreams.yaml mootdx2.servers → ServerConfig 列表。

    mootdx2 的 server= 参数内部用 ipaddress.ip_address 校验, 只接受 IP
    字面量 (不支持域名), 这里在系统边界提前 fail fast, 避免启动后首次
    请求才报 "Server 格式错误"。

    Raises:
        ValueError: host 非 IP 字面量或 port 越界。
    """
    import ipaddress

    from ..models.mootdx2_config import ServerConfig

    servers: list[ServerConfig] = []
    for idx, item in enumerate(raw_servers):
        host = str(item.get("host", "")).strip()
        try:
            ipaddress.ip_address(host)
        except ValueError as e:
            raise ValueError(
                f"upstreams.mootdx2.servers[{idx}].host='{host}' 非法: "
                f"mootdx2 只接受 IP 字面量, 不支持域名 ({e})"
            ) from e
        port = int(item.get("port", 7709))
        if not 1 <= port <= 65535:
            raise ValueError(
                f"upstreams.mootdx2.servers[{idx}].port={port} 越界 (1-65535)"
            )
        servers.append(ServerConfig(host=host, port=port))
    return servers


GATEWAY_VERSION = _read_gateway_version()

#: 手写/状态工具名: 实体工具不得改名为这些名字 (会与
#: _register_status_tools / search_stock 的手写注册撞车, 且这些名字
#: 在 tool 实体表里本就不存在)。
RESERVED_TOOL_NAMES = frozenset({"get_server_status", "get_health", "search_stock"})


def _console_auth_config_from_env() -> tuple[dict | None, str]:
    """Read console auth config from UNIHIVE_CONSOLE_* env vars only.

    Returns ``(cfg, reason)`` where cfg is None when auth is disabled.
    Empty string is treated as unset (opt-in via setting them).
    Whitespace around values is stripped.
    """
    username = os.getenv("UNIHIVE_CONSOLE_USER", "").strip() or None
    password = os.getenv("UNIHIVE_CONSOLE_PASSWORD", "").strip() or None
    secret = os.getenv("UNIHIVE_CONSOLE_SESSION_SECRET", "").strip() or None

    # Three empty => no env vars set at all
    if not (username or password or secret):
        return None, "disabled: no UNIHIVE_CONSOLE_* env vars set"

    # Partial => tell user which var(s) are missing
    missing = []
    if not username:
        missing.append("UNIHIVE_CONSOLE_USER")
    if not password:
        missing.append("UNIHIVE_CONSOLE_PASSWORD")
    if not secret:
        missing.append("UNIHIVE_CONSOLE_SESSION_SECRET")
    if missing:
        return None, f"disabled: missing env var(s): {', '.join(missing)}"

    if len(secret) < 32:
        return None, (
            f"disabled: UNIHIVE_CONSOLE_SESSION_SECRET must be at least "
            f"32 characters (got {len(secret)})"
        )

    # H4 (round 9 audit): env 路径就地 hash 明文 password → password_hash。
    # setup_console_auth 现在拒绝明文 (要 PBKDF2 password_hash), 旧实现直接
    # 塞明文 → serve_http 路由装配段崩。就地 hash 后 env 路径与 YAML 路径
    # 同态, 不再把明文留进程内存 (hash 是被 setup 持久化的对象)。
    from ..console_auth import hash_password

    return {
        "username": username,
        "password_hash": hash_password(password),
        "session_secret": secret,
    }, "ok"


def _get_console_auth_config(config: dict | None = None) -> tuple[dict | None, str]:
    """Read console auth config from config.yaml, upstreams.yaml or env vars.

    Returns (cfg, reason) where cfg is None when auth is disabled. Caller
    logs ``reason`` at WARNING level and continues without auth; when cfg is
    not None, caller passes it to ``setup_console_auth`` on the inner app.

    Config priority (first source with a non-empty console section wins):
      1. config/config.yaml (console section)
      2. config/upstreams.yaml (console section) — passed in via ``config``
      3. Environment variables (UNIHIVE_CONSOLE_USER / _PASSWORD / _SESSION_SECRET)

    Each source is parsed independently: if a source declares a console
    section, we use *that* source's values verbatim — we don't merge across
    sources (that previously caused partial-config footguns where username
    came from yaml and password came from env, leaving setup_console_auth
    with no way to validate). The reason returned tells the caller exactly
    which field(s) are missing when the source is partial.

    config.yaml fields:
      console.username
      console.password / console.password_hash
      console.session_secret
    """

    def _extract_console(console_cfg: dict) -> dict:
        """Return normalized console sub-config (each field None if unset)."""
        return {
            "username": (console_cfg.get("username") or "").strip() or None,
            "password": (console_cfg.get("password") or "").strip() or None,
            "password_hash": (console_cfg.get("password_hash") or "").strip() or None,
            "secret": (console_cfg.get("session_secret") or "").strip() or None,
        }

    def _missing_fields_message(fields: dict, source: str) -> str:
        missing = [k for k, v in fields.items() if not v]
        return f"partial console config from {source}: missing {', '.join(missing)}"

    def _build_cfg(fields: dict) -> dict:
        # Prefer password_hash over plaintext password.
        cfg = {"username": fields["username"], "session_secret": fields["secret"]}
        if fields["password_hash"]:
            cfg["password_hash"] = fields["password_hash"]
        else:
            cfg["password"] = fields["password"]
        return cfg

    # 1. config/config.yaml (standalone, runtime console config)
    config_yaml_path = resolve_layout_root(Path(__file__)) / "config" / "config.yaml"
    if config_yaml_path.exists():
        try:
            with open(config_yaml_path, encoding="utf-8") as f:
                config_yaml = yaml.safe_load(f) or {}
            console_cfg = config_yaml.get("console", {})
            if console_cfg:
                fields = _extract_console(console_cfg)
                if any(fields.values()):
                    has_creds = bool(fields["password"] or fields["password_hash"])
                    if not has_creds or not fields["username"] or not fields["secret"]:
                        return None, _missing_fields_message(fields, "config/config.yaml")
                    if len(fields["secret"]) < 32:
                        return None, (
                            "config/config.yaml: session_secret must be at least "
                            "32 characters"
                        )
                    return _build_cfg(fields), "ok"
        except Exception as e:
            logger.warning(f"Failed to load config.yaml: {e}")

    # 2. config/upstreams.yaml (passed in as ``config``).
    if config:
        console_cfg = config.get("console", {})
        if console_cfg:
            fields = _extract_console(console_cfg)
            if any(fields.values()):
                has_creds = bool(fields["password"] or fields["password_hash"])
                if not has_creds or not fields["username"] or not fields["secret"]:
                    return None, _missing_fields_message(fields, "config/upstreams.yaml")
                if len(fields["secret"]) < 32:
                    return None, (
                        "config/upstreams.yaml: session_secret must be at least "
                        "32 characters"
                    )
                return _build_cfg(fields), "ok"

    # 3. Environment variables — helper handles partial-config + length checks.
    return _console_auth_config_from_env()
