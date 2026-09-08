"""
配置加载器
- 加载 YAML 配置
- 递归解析 ${VAR} 环境变量占位符
- 启动时校验必需配置项
"""
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


def _resolve_value(value: Any, *, strict: bool, missing: set[str] | None = None) -> Any:
    """递归替换字符串中的 ${VAR} 占位符。

    Args:
        value: 任意值；只对 str 类型做替换
        strict: True 时 env var 缺失抛 KeyError；False 时保留占位符
        missing: 内部递归用，记录所有缺失变量名（set 自动去重）
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
        return {k: _resolve_value(v, strict=strict, missing=missing) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_value(v, strict=strict, missing=missing) for v in value]
    return value


def resolve_env_vars(config: dict, *, strict: bool = True) -> tuple[dict, list[str]]:
    """对整个 config dict 递归解析 env var。

    Returns:
        (resolved_config, sorted_unique list_of_missing_var_names)
    """
    missing: set[str] = set()
    resolved = _resolve_value(config, strict=strict, missing=missing)
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
        elif cfg.get("type") == "http_jsonrpc":
            if not cfg.get("base_url"):
                errors.append(f"upstream {name!r} type=http_jsonrpc missing 'base_url'")
        elif cfg.get("type") == "python":
            pass  # TokenWave TDX client, no extra fields required
        elif cfg.get("type") == "mootdx2":
            pass  # MooTDX2 client, no extra fields required
        elif cfg.get("type") == "tdx_quant":
            if not cfg.get("tdx_root"):
                errors.append(f"upstream {name!r} type=tdx_quant missing 'tdx_root'")
        elif cfg.get("type") == "npx":
            if not cfg.get("command") and not cfg.get("package"):
                errors.append(f"upstream {name!r} type=npx missing 'command' or 'package'")
        else:
            errors.append(f"upstream {name!r} unknown type: {cfg.get('type')!r}")

    return errors
