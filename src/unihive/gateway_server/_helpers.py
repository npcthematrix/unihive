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
import sys
from pathlib import Path

import yaml

from ..core.config_db import resolve_layout_root
# 事实源在 core.registry (校验/注册/热改名共用); 这里 re-export 保持
# 历史导入路径 `from ...gateway_server import RESERVED_TOOL_NAMES` 不变。
from ..core.registry import RESERVED_TOOL_NAMES  # noqa: F401

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


_PROJECT_TABLE_RE = re.compile(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)")
_VERSION_LINE_RE = re.compile(r'^version\s*=\s*"([^"]+)"')


def _version_from_pyproject_text(text: str) -> str | None:
    """从 pyproject.toml 文本提取 [project] 表内的 version。

    Round-12 簇I: 旧实现在全文匹配首个 ``version = "..."``, 若未来某
    ``[tool.*]`` 表 (bumpversion/poetry 插件等) 出现在 [project] 之前且
    带 version 键, 会读错版本。这里先切片 [project] 表 (到下一个顶层
    表头/EOF), 再在切片内匹配。
    """
    m = _PROJECT_TABLE_RE.search(text)
    if not m:
        return None
    body = m.group(0)
    for line in body.splitlines():
        vm = _VERSION_LINE_RE.match(line.strip())
        if vm:
            return vm.group(1)
    return None


def _read_gateway_version() -> str:
    """读取网关版本号，给 FastMCP 当 serverInfo.version。

    优先 importlib.metadata (pip install 后的规范来源, wheel 内不带
    pyproject.toml 也能读到); 开发环境 (未 pip install -e .) PackageNotFound
    时退回读源码根的 pyproject.toml 的 [project].version。读不到返回
    'unknown'，避免启动崩。
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("unihive")
        except PackageNotFoundError:
            pass
        pyproject = resolve_layout_root(Path(__file__)) / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        found = _version_from_pyproject_text(text)
        if found:
            return found
    except Exception as e:
        logger.warning(f"failed to read gateway version: {e}")
    return "unknown"


def _configure_stdio_utf8() -> None:
    """stdio 模式把 stdout/stdin 切到 UTF-8 (MCP JSON-RPC 帧通道)。

    Round-12 簇I: 旧实现在解释器启动**之后**设 os.environ
    ["PYTHONIOENCODING"], 该变量只在解释器启动瞬间决定 stdio 编码,
    运行时设置对当前进程无效 (中文 Windows 仍按 cp936 编码帧里的中文 →
    UnicodeEncodeError), 纯属假动作。真正生效的两道:
      1) start_gateway.ps1 在启动 python 前 $env:PYTHONIOENCODING=utf-8
         (覆盖 ps1 启动路径);
      2) 这里 reconfigure 当前进程流 (覆盖 console script `unihive` 与
         MCP 宿主直接 `python -m` 启动、不经 ps1 的路径)。
    某些宿主替换的流没有 reconfigure, 跳过即可。
    """
    for stream_name in ("stdout", "stdin"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            logger.debug("failed to reconfigure %s to utf-8", stream_name)


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

# ---------------------------------------------------------------------------
# Round-12 簇E: 数值配置字段强转 — 报错必须带点路径字段名, 便于定位配置行
# ---------------------------------------------------------------------------


def coerce_int_field(
    raw: object,
    field: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """把配置值强转为 int。None → default; bool/字符串/浮点/其他 → ValueError。

    裸 ``int(raw)`` 会把 "100"/True/1.9 静默吞成 100/True→1/1, 配置写错时
    延迟到很远的地方才以费解错误爆炸; 这里 fail-fast 且消息带 ``field``。
    """
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            f"{field} 必须是整数, got {raw!r} ({type(raw).__name__})"
        )
    if minimum is not None and raw < minimum:
        raise ValueError(f"{field} 必须 >= {minimum}, got {raw!r}")
    if maximum is not None and raw > maximum:
        raise ValueError(f"{field} 必须 <= {maximum}, got {raw!r}")
    return raw


def coerce_float_field(
    raw: object,
    field: str,
    *,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_min: bool = False,
    exclusive_max: bool = False,
) -> float | None:
    """把配置值强转为 float。None → default; bool/非数值 → ValueError。

    exclusive_min/Max 控制边界是否开区间 (rate/timeout 必须严格 > 0)。
    """
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(
            f"{field} 必须是数字, got {raw!r} ({type(raw).__name__})"
        )
    value = float(raw)
    if minimum is not None:
        if value < minimum or (exclusive_min and value == minimum):
            op = ">" if exclusive_min else ">="
            raise ValueError(f"{field} 必须 {op} {minimum}, got {raw!r}")
    if maximum is not None:
        if value > maximum or (exclusive_max and value == maximum):
            raise ValueError(f"{field} 必须 <= {maximum}, got {raw!r}")
    return value
