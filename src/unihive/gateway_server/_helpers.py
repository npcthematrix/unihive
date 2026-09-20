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

    Returns ``(cfg, reason)`` where cfg is None only when **none** of the
    three vars is set (= explicit opt-out, console runs open).

    Round-13 fail-closed: any var set but the config incomplete/short/using
    known public defaults raises ConsoleAuthConfigError (a ValueError, so
    main() maps it to exit code 2) instead of silently disabling auth —
    a half-written .env used to mean "open /api/* with no login at all".

    Empty string is treated as unset. Username/secret strip surrounding
    whitespace. The password is preserved verbatim (leading/trailing spaces
    are legitimate password characters); a whitespace-only password is
    treated as unset (fail-closed as a missing var).
    """
    username = os.getenv("UNIHIVE_CONSOLE_USER", "").strip() or None
    raw_password = os.getenv("UNIHIVE_CONSOLE_PASSWORD")
    password = (
        raw_password
        if raw_password and not raw_password.isspace()
        else None
    )
    secret = os.getenv("UNIHIVE_CONSOLE_SESSION_SECRET", "").strip() or None

    # Three empty => no env vars set at all => auth intentionally disabled.
    if not (username or password or secret):
        return None, "disabled: no UNIHIVE_CONSOLE_* env vars set"

    # Partial => fail-closed with the exact missing var(s).
    missing = []
    if not username:
        missing.append("UNIHIVE_CONSOLE_USER")
    if not password:
        missing.append("UNIHIVE_CONSOLE_PASSWORD")
    if not secret:
        missing.append("UNIHIVE_CONSOLE_SESSION_SECRET")
    if missing:
        from ..console_auth import ConsoleAuthConfigError

        raise ConsoleAuthConfigError(
            "console auth partially configured via environment "
            f"variable(s) (missing: {', '.join(missing)}). Set all three "
            "UNIHIVE_CONSOLE_* vars to enable console auth, or leave all "
            "three unset to run without it"
        )

    if len(secret) < 32:
        from ..console_auth import ConsoleAuthConfigError

        raise ConsoleAuthConfigError(
            "UNIHIVE_CONSOLE_SESSION_SECRET must be at least 32 characters "
            f"(got {len(secret)})"
        )

    # H4 (round 9 audit): env 路径就地 hash 明文 password → password_hash。
    # setup_console_auth 现在拒绝明文 (要 PBKDF2 password_hash), 旧实现直接
    # 塞明文 → serve_http 路由装配段崩。就地 hash 后 env 路径与 YAML 路径
    # 同态, 不再把明文留进程内存 (hash 是被 setup 持久化的对象)。
    from ..console_auth import (
        hash_password,
        reject_known_default_credentials,
        reject_weak_password,
    )

    # 弱口令在哈希前查 (O(1) 集合比对, 也省掉无谓的 PBKDF2)。
    reject_weak_password({"username": username}, plaintext_password=password)
    cfg = {
        "username": username,
        "password_hash": hash_password(password),
        "session_secret": secret,
    }
    # 照抄旧出厂公开 secret/口令 → fail-closed。
    reject_known_default_credentials(cfg)
    return cfg, "ok"


# console.enabled=false 的内部信号 (区别于 "没有 console 段, 继续找下一源")。
_CONSOLE_EXPLICITLY_DISABLED = object()


def _get_console_auth_config(
    config: dict | None = None,
    *,
    config_yaml_path: str | Path | None = None,
) -> tuple[dict | None, str]:
    """Read console auth config from config.yaml, upstreams.yaml or env vars.

    Returns ``(None, reason)`` only when console auth is *intentionally*
    disabled (no console section anywhere AND no UNIHIVE_CONSOLE_* env vars).
    Any explicit-but-incomplete/insecure config raises
    ``ConsoleAuthConfigError`` (Round-13 fail-closed: a half-written config
    used to silently leave every /api/* write endpoint unauthenticated).

    Config priority (first source with a non-empty console section wins):
      1. config/config.yaml (console section) — ``config_yaml_path`` overrides
         the location (serve_http passes the --config-anchored directory,
         Round-13 review L5); defaults to the layout root.
      2. config/upstreams.yaml (console section) — passed in via ``config``
      3. Environment variables (UNIHIVE_CONSOLE_USER / _PASSWORD / _SESSION_SECRET)

    Each source is parsed independently — there is deliberately no merge
    across sources. YAML sources only accept PBKDF2 ``password_hash``;
    plaintext passwords go through the UNIHIVE_CONSOLE_PASSWORD env var and
    are hashed at the boundary.

    Round-13 review M1: a config.yaml that *fails to parse* used to be
    swallowed by a broad ``except Exception`` and the gateway silently came
    up unauthenticated. Parse failure is now fail-closed unless a complete
    UNIHIVE_CONSOLE_* env config provides the escape hatch.
    """
    from ..console_auth import (
        ConsoleAuthConfigError,
        reject_known_default_credentials,
        reject_weak_password,
    )

    def _extract_console(console_cfg: dict, source: str) -> dict:
        """Return normalized console sub-config (each field None if unset).

        Non-string scalars (PyYAML 1.1 octal ints, bool, list) must fail with
        a readable ConsoleAuthConfigError instead of an AttributeError out of
        ``.strip()`` (which previously surfaced as a raw traceback / exit 1).
        """

        def _str_field(key: str) -> str | None:
            value = console_cfg.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ConsoleAuthConfigError(
                    f"{source}: console.{key} 必须是字符串, got "
                    f"{value!r} ({type(value).__name__})"
                )
            return value.strip() or None

        return {
            "username": _str_field("username"),
            "password": _str_field("password"),
            "password_hash": _str_field("password_hash"),
            "secret": _str_field("session_secret"),
        }

    def _resolve_section(console_cfg: dict, source: str) -> dict | None:
        """None = 该源没有 console 段; 否则返回完整 cfg 或 fail-closed 抛错。"""
        if not isinstance(console_cfg, dict) or not console_cfg:
            return None
        # 显式开关: enabled: false = 即使凭证齐全也关闭 (本地逃生)。
        # enabled 键给了就必须是布尔, 拒绝 "yes"/0 等歧义值。
        enabled = console_cfg.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConsoleAuthConfigError(
                f"{source}: console.enabled 必须是布尔值 true/false, got "
                f"{enabled!r} ({type(enabled).__name__})"
            )
        if enabled is False:
            return _CONSOLE_EXPLICITLY_DISABLED
        fields = _extract_console(console_cfg, source)
        if not any(fields.values()):
            return None

        missing = []
        if not fields["username"]:
            missing.append("username")
        if not (fields["password"] or fields["password_hash"]):
            missing.append("password_hash")
        if not fields["secret"]:
            missing.append("session_secret")
        if missing:
            raise ConsoleAuthConfigError(
                f"partial console config in {source} (missing: "
                f"{', '.join(missing)}). Complete the console section and "
                "restart, or remove it and set all three UNIHIVE_CONSOLE_* "
                "env vars; an explicitly partial config is rejected instead "
                "of silently running the console unauthenticated"
            )
        if len(fields["secret"]) < 32:
            raise ConsoleAuthConfigError(
                f"{source}: console.session_secret must be at least 32 "
                f"characters (got {len(fields['secret'])})"
            )
        if not fields["password_hash"]:
            # 明文 YAML 自 MED-6 起就会被 setup 拒绝; 这里给可操作的报错,
            # 而不是让装配段抛 "must not include plaintext 'password'"。
            raise ConsoleAuthConfigError(
                f"{source}: plaintext console.password is not accepted — "
                "use the UNIHIVE_CONSOLE_PASSWORD env var (hashed at "
                "startup) or set console.password_hash generated by "
                "src.unihive.console_auth.hash_password()"
            )
        cfg = {
            "username": fields["username"],
            "password_hash": fields["password_hash"],
            "session_secret": fields["secret"],
        }
        # 照抄旧出厂公开默认凭证 → fail-closed。
        reject_known_default_credentials(cfg)
        # 哈希对应的弱口令 (随机盐使固定哈希黑名单失效) → fail-closed。
        reject_weak_password(cfg)
        return cfg

    def _from_section(console_cfg: object, source: str) -> tuple[dict | None, str | None]:
        """统一处理一个 console 段 → (cfg, reason)。

        cfg 非 None = 启用; reason 非 None = 显式关闭; 两者皆 None =
        该源没有 console 段, 继续下一源。
        """
        if not isinstance(console_cfg, dict) or not console_cfg:
            return None, None
        resolved = _resolve_section(console_cfg, source)
        if resolved is _CONSOLE_EXPLICITLY_DISABLED:
            return None, f"disabled: console.enabled=false in {source}"
        return (resolved, "ok") if resolved is not None else (None, None)

    # 1. config/config.yaml (standalone, runtime console config)
    if config_yaml_path is None:
        config_yaml_path = (
            resolve_layout_root(Path(__file__)) / "config" / "config.yaml"
        )
    config_yaml_path = Path(config_yaml_path)
    if config_yaml_path.exists():
        try:
            with open(config_yaml_path, encoding="utf-8") as f:
                config_yaml = yaml.safe_load(f) or {}
        except ConsoleAuthConfigError:
            raise
        except (OSError, yaml.YAMLError) as e:
            # M1 (round-13 review): 文件解析失败 = console 鉴权意图不可判定,
            # 旧实现宽 except 吞掉后静默开放 /api/*。完整 env 配置是唯一
            # 逃生路径; 否则 fail-closed (退出码 2)。
            env_cfg, env_reason = _console_auth_config_from_env()
            if env_cfg is not None:
                logger.warning(
                    "config/config.yaml failed to parse (%s); falling back "
                    "to UNIHIVE_CONSOLE_* env config",
                    _safe_config_error_detail(e),
                )
                return env_cfg, env_reason
            raise ConsoleAuthConfigError(
                "failed to parse config/config.yaml "
                f"({_safe_config_error_detail(e)}): cannot determine console "
                "auth intent. Fix the YAML syntax, or set all three "
                "UNIHIVE_CONSOLE_* env vars to start; refusing to run with "
                "an unauthenticated /api/* by default"
            ) from e
        cfg, reason = _from_section(
            config_yaml.get("console"), "config/config.yaml"
        )
        if cfg is not None:
            return cfg, reason
        if reason is not None:
            return None, reason

    # 2. config/upstreams.yaml (passed in as ``config``).
    if config:
        cfg, reason = _from_section(
            config.get("console"), "config/upstreams.yaml"
        )
        if cfg is not None:
            return cfg, reason
        if reason is not None:
            return None, reason

    # 3. Environment variables — helper handles partial-config + length
    #    + known-default checks and raises ConsoleAuthConfigError itself.
    return _console_auth_config_from_env()


def _has_top_level_console_key(raw_text: str) -> bool:
    """原始 YAML 文本是否含顶层 ``console:`` 键 (列首, 无缩进)。

    解析失败时无法判断 console 段内容, 但这个正则能回答 "部署者是否
    表达过 console 配置意图", 用于决定 fail-closed 还是仅告警降级。
    缩进的 console: (某上游/表的子键) 不匹配。
    """
    return re.search(r"(?m)^console\s*:", raw_text or "") is not None


def _safe_config_error_detail(e: Exception) -> str:
    """配置解析异常的安全摘要: 类型 + YAML 行号, 不含出错行文本。

    ScannerError 的 str() 会回显源码行内容; 若语法错误恰好落在
    session_secret 行, 32+ 字符密钥就会进日志/报错 (review L1)。
    """
    mark = getattr(e, "problem_mark", None)
    line = getattr(mark, "line", None)
    if isinstance(line, int):
        return f"{type(e).__name__} at line {line + 1}"
    return type(e).__name__


def load_config_yaml_overlay(
    config_yaml_path: "str | Path", *, strict_env: bool
) -> "dict | None":
    """加载 config.yaml 覆盖层; 文件缺失返回 None。

    Review M1/L1 (round-13): 解析失败时按文件是否含顶层 ``console:`` 键分流 ——
    含则抛 :class:`ConsoleAuthConfigError` (鉴权意图不可判定, 不允许吞错后
    静默开放 /api/*); 不含则只告警并返回 None (db 模式下 config.db 是事实源,
    yaml 模式由调用方决定是否继续传播)。异常只摘要类型+行号, 不回显源码行
    (ScannerError 会带出出错文本, 可能是 session_secret 行)。
    """
    path = Path(config_yaml_path)
    if not path.exists():
        return None
    try:
        from ..utils.config_loader import load_config

        return load_config(str(path), strict_env=strict_env)
    except Exception as e:
        from ..console_auth import ConsoleAuthConfigError

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError:
            raw_text = ""
        detail = _safe_config_error_detail(e)
        if _has_top_level_console_key(raw_text):
            raise ConsoleAuthConfigError(
                f"failed to parse config/config.yaml ({detail}) while a "
                "top-level 'console:' section is present: cannot determine "
                "console auth intent. Fix the YAML, or set all three "
                "UNIHIVE_CONSOLE_* env vars"
            ) from e
        logger.warning("Failed to load config.yaml: %s", detail)
        return None


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
