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

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_value(value: Any, *, strict: bool, missing: list[str] | None = None) -> Any:
    """递归替换字符串中的 ${VAR} 占位符。

    Args:
        value: 任意值；只对 str 类型做替换
        strict: True 时 env var 缺失抛 KeyError；False 时保留占位符
        missing: 内部递归用，记录所有缺失变量名
    """
    if missing is None:
        missing = []

    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var_name = m.group(1)
            env_val = os.getenv(var_name)
            if env_val is None:
                missing.append(var_name)
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
        (resolved_config, list_of_missing_var_names)
    """
    missing: list[str] = []
    resolved = _resolve_value(config, strict=strict, missing=missing)
    return resolved, missing


def load_config(
    path: str | Path = "config/upstreams.yaml",
    *,
    strict_env: bool = True,
) -> dict:
    """加载 YAML 并解析 env var。

    Args:
        path: YAML 文件路径
        strict_env: True 时 env var 缺失抛 KeyError；False 时保留 ${VAR} 占位符

    Raises:
        FileNotFoundError: 配置不存在
        KeyError: strict_env=True 且 env var 缺失
    """
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
        elif cfg.get("type") == "npx":
            if not cfg.get("command") and not cfg.get("package"):
                errors.append(f"upstream {name!r} type=npx missing 'command' or 'package'")
        else:
            errors.append(f"upstream {name!r} unknown type: {cfg.get('type')!r}")

    return errors
