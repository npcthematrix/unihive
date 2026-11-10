"""
配置加载器
- 加载 YAML 配置
- 递归解析 ${VAR} 环境变量占位符
- 启动时校验必需配置项
"""
import copy
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv as _load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    """只 load 一次 .env，避免每次 load_config 都 IO 一次。"""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    if _HAS_DOTENV:
        _load_dotenv()
    _DOTENV_LOADED = True


def _resolve_value(
    value: Any,
    *,
    strict: bool,
    missing: set[str] | None = None,
    skip_disabled_upstreams: bool = False,
    _key: str | None = None,
) -> Any:
    """递归替换字符串中的 ${VAR} 占位符。

    Args:
        value: 任意值；只对 str 类型做替换
        strict: True 时 env var 缺失抛 KeyError；False 时保留占位符
        missing: 内部递归用，记录所有缺失变量名（set 自动去重）
        skip_disabled_upstreams: True 时, ``upstreams`` 段下 ``enabled`` 为假
            的块跳过解析 (深拷贝原样返回), 不把其 ${VAR} 缺失计入 missing、
            不在 strict 下抛 KeyError。与 :func:`validate_config` 同语义:
            禁用上游不参与启动校验 (M6, round 9 audit)。
    """
    if missing is None:
        missing = set()

    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var_name = m.group(1)
            env_val = os.getenv(var_name)
            if env_val is None:
                missing.add(var_name)
                if strict:
                    raise KeyError(f"Required env var not set: {var_name}")
                return m.group(0)
            return env_val

        return _ENV_PATTERN.sub(_replace, value)
    elif isinstance(value, dict):
        # M6: upstreams 段下 enabled 为假的块整块跳过 (保留 ${VAR} 占位,
        # 不解析、不计 missing、不抛 KeyError), 与 validate_config 同语义。
        if skip_disabled_upstreams and _key == "upstreams":
            result: dict[Any, Any] = {}
            for k, v in value.items():
                if isinstance(v, dict) and not v.get("enabled", False):
                    result[k] = copy.deepcopy(v)
                else:
                    result[k] = _resolve_value(
                        v, strict=strict, missing=missing,
                        skip_disabled_upstreams=True, _key=k,
                    )
            return result
        return {
            k: _resolve_value(
                v, strict=strict, missing=missing,
                skip_disabled_upstreams=skip_disabled_upstreams, _key=k,
            )
            for k, v in value.items()
        }
    elif isinstance(value, list):
        return [
            _resolve_value(
                v, strict=strict, missing=missing,
                skip_disabled_upstreams=skip_disabled_upstreams,
            )
            for v in value
        ]
    return value


def resolve_env_vars(
    config: dict,
    *,
    strict: bool = True,
    skip_disabled_upstreams: bool = False,
) -> tuple[dict, list[str]]:
    """对整个 config dict 递归解析 env var。

    Args:
        strict: True 时 env var 缺失抛 KeyError；False 时保留占位符
        skip_disabled_upstreams: True 时禁用上游块的 ${VAR} 不参与解析/校验
            (M6), 默认 False 保持向后兼容。

    Returns:
        (resolved_config, sorted_unique list_of_missing_var_names)
    """
    missing: set[str] = set()
    resolved = _resolve_value(
        config, strict=strict, missing=missing,
        skip_disabled_upstreams=skip_disabled_upstreams,
    )
    return resolved, sorted(missing)


def load_config(
    path: str | Path = "config/upstreams.yaml",
    *,
    strict_env: bool = True,
) -> dict:
    """加载 YAML 并解析 env var。

    自动从项目根目录的 .env 加载环境变量（如 python-dotenv 可用），
    使 config 中的 ${VAR} 占位符能解析到 .env 中定义的值。

    Args:
        path: YAML 文件路径
        strict_env: True 时 env var 缺失抛 KeyError；False 时保留 ${VAR} 占位符

    Raises:
        FileNotFoundError: 配置不存在
        KeyError: strict_env=True 且 env var 缺失
    """
    if _HAS_DOTENV:
        _load_dotenv_once()

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raw = {}

    resolved, missing = resolve_env_vars(raw, strict=strict_env)
    if missing and not strict_env:
        logger.warning(f"Missing env vars (kept as placeholders): {missing}")
    return resolved


def merge_config_overlay(base: dict, overlay: dict) -> dict:
    """把 config.yaml 的覆盖合并进 upstreams.yaml (就地修改并返回 base)。

    语义刻意保持**浅层**合并 (与历史行为一致, gateway_server 与
    ``unihive config lint`` 共用, 避免启动与校验两条链路合并结果漂移):

    - 两边都是 dict 的顶层键: ``update`` 浅合并一层;
    - 其余情况 (标量/列表/类型不同): overlay 整体覆盖。
    """
    for key, value in overlay.items():
        if key not in base:
            base[key] = value
        elif isinstance(base[key], dict) and isinstance(value, dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def validate_config(config: dict) -> list[str]:
    """校验配置结构。返回错误信息列表，空表示无错。"""
    errors: list[str] = []

    upstreams = config.get("upstreams") or {}
    if not upstreams:
        errors.append("No upstreams configured")

    for name, cfg in upstreams.items():
        if not cfg.get("enabled"):
            continue
        if not cfg.get("type"):
            errors.append(f"upstream {name!r} missing 'type'")
        if cfg.get("type") == "http":
            if not cfg.get("base_url"):
                errors.append(f"upstream {name!r} type=http missing 'base_url'")
            if not cfg.get("api_key"):
                errors.append(f"upstream {name!r} type=http missing 'api_key'")
        elif cfg.get("type") == "mootdx2":
            pass  # MooTDX2 client, no extra fields required
        elif cfg.get("type") == "tdx_quant":
            if not cfg.get("tdx_root"):
                errors.append(f"upstream {name!r} type=tdx_quant missing 'tdx_root'")
        elif cfg.get("type") == "stdio":
            # Round-12 簇G: 类型表与 _construct_upstream_client 对齐。
            # http_jsonrpc/python/npx 是已下线的历史类型, 构造表里没有,
            # 旧校验却放行 → 启动构造期才 fail-fast。统一按 unknown type 拒。
            if not cfg.get("command") and not cfg.get("package"):
                errors.append(
                    f"upstream {name!r} type=stdio missing 'command' or 'package'"
                )
        elif cfg.get("type") == "omni":
            pass  # OMNIDATA client, no extra fields required
        elif cfg.get("type") == "thsdk":
            pass  # THSDK client; credentials come from THS_USERNAME/THS_PASSWORD env
        else:
            errors.append(f"upstream {name!r} unknown type: {cfg.get('type')!r}")

    # LOW-3 (2026-09-14 audit): console 段校验。
    # 与运行时 _get_console_auth_config 形成两道防线 — 这里抓明显缺字段,
    # 运行时抓跨源 partial-config 的细粒度组合。运行时是 source of truth。
    console = config.get("console") or {}
    if console:
        # enabled: false 是显式关闭开关, 凭证字段一律不校验 (与运行时
        # _get_console_auth_config 语义一致); 非布尔值才是配置错误。
        enabled = console.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append(
                f"console.enabled must be a boolean, got {enabled!r} "
                f"({type(enabled).__name__})"
            )
        elif enabled is True:
            username = console.get("username")
            if not isinstance(username, str) or not username.strip():
                errors.append("console.username missing or not a string")
            if not (console.get("password") or console.get("password_hash")):
                errors.append(
                    "console.password or console.password_hash required"
                )
            secret_raw = console.get("session_secret")
            if not isinstance(secret_raw, str) or not secret_raw.strip():
                errors.append("console.session_secret required")
            elif len(secret_raw.strip()) < 32:
                errors.append(
                    "console.session_secret must be at least 32 characters"
                )

    return errors
