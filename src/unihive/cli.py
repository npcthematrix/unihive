"""``python -m unihive`` 命令行入口。

当前子命令:

- ``config lint`` — 不启动网关/不连上游, 对合并后的完整配置做静态校验,
  退出码非零表示存在 error (或开启 ``--warnings-as-errors`` 时含 warning)。
  可挂提交前钩子与 CI。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.config_lint import ERROR, WARNING, lint_config
from .core.tool_loader import load_all_tools
from .utils.config_loader import load_config, merge_config_overlay


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m unihive",
        description="UniHive 网关命令行工具",
    )
    domains = parser.add_subparsers(dest="domain", required=True)

    config_parser = domains.add_parser("config", help="配置工具")
    config_actions = config_parser.add_subparsers(dest="action", required=True)

    lint_parser = config_actions.add_parser(
        "lint",
        help="静态校验合并后的完整配置 (upstreams.yaml + config.yaml + tools_*.yaml)",
    )
    lint_parser.add_argument(
        "--config",
        default="config/upstreams.yaml",
        help="主配置路径 (默认 config/upstreams.yaml); config.yaml 与其同目录",
    )
    lint_parser.add_argument(
        "--strict-env",
        action="store_true",
        help="${VAR} 占位符缺少环境变量时按错误处理 (默认保留占位符, 仅校验结构)",
    )
    lint_parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="存在 warning 也以非零码退出",
    )
    return parser


def _cmd_config_lint(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 2

    config = load_config(config_path, strict_env=args.strict_env)

    config_yaml = config_path.parent / "config.yaml"
    if config_yaml.exists():
        overlay = load_config(config_yaml, strict_env=args.strict_env)
        merge_config_overlay(config, overlay)

    # 目录正确性与本机环境变量无关: 连 requires_env 门控 spec 一并校验。
    specs = load_all_tools(config_path, config, include_env_gated=True)
    issues = lint_config(config, specs)

    errors = [i for i in issues if i.severity == ERROR]
    warnings = [i for i in issues if i.severity == WARNING]

    print(f"lint {config_path} ({len(specs)} tool specs)")
    if config_yaml.exists():
        print(f"overlay {config_yaml}")
    for issue in issues:
        level = "ERROR  " if issue.severity == ERROR else "WARNING"
        print(f"  {level} [{issue.code}] {issue.message}")

    if not issues:
        print("OK: no issues found")
    else:
        print(
            f"found {len(errors)} error(s), {len(warnings)} warning(s)",
            file=sys.stderr if errors else sys.stdout,
        )

    if errors or (args.warnings_as_errors and warnings):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.domain == "config" and args.action == "lint":
        return _cmd_config_lint(args)
    parser.error(f"unknown command: {args.domain} {args.action}")
    return 2  # pragma: no cover - argparse.error exits
