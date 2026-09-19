"""``python -m unihive`` 命令行入口。

当前子命令:

- ``config lint`` — 不启动网关/不连上游, 对合并后的完整配置做静态校验,
  退出码非零表示存在 error (或开启 ``--warnings-as-errors`` 时含 warning)。
  可挂提交前钩子与 CI。
- ``config seed-db`` — 把出厂 YAML 模板全量解构灌入 SQLite (v2, 空库首灌)。
- ``config export`` — 把 config.db 当前实体导出为 YAML/JSON (备份/审计)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from .core.config_db import (
    load_template_from_store,
    resolve_config_db_path,
    resolve_project_path,
    seed_database,
)
from .core.config_lint import ERROR, WARNING, LintRejected, lint_config
from .core.db_assembler import assemble_config
from .core.tool_loader import load_all_tools
from .storage.config_store import (
    AlreadySeededError,
    ConfigStore,
    RevisionConflict,
)
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

    seed_parser = config_actions.add_parser(
        "seed-db",
        help="把出厂 YAML 模板全量解构灌入 config.db (空库首灌; --force 重灌)",
    )
    seed_parser.add_argument("--config", default="config/upstreams.yaml", help="主配置路径")
    seed_parser.add_argument("--db", default=None, help="SQLite 配置库路径 (相对路径锚定项目根; 默认 data/config.db)")
    seed_parser.add_argument("--force", action="store_true", help="非空库也清空重灌")

    export_parser = config_actions.add_parser(
        "export",
        help="导出 config.db 当前配置 (yaml=装配后的配置形态, json=实体 bundle)",
    )
    export_parser.add_argument("--db", default=None, help="SQLite 配置库路径 (相对路径锚定项目根; 默认 data/config.db)")
    export_parser.add_argument(
        "--format",
        choices=("yaml", "json"),
        default="yaml",
        help="导出格式 (默认 yaml)",
    )
    export_parser.add_argument("--output", default="-", help="输出文件 (默认 stdout)")

    upgrade_parser = config_actions.add_parser(
        "upgrade-db",
        help=(
            "按最新出厂 YAML 对 config.db 做字段级三方合并 (默认 dry-run; "
            "--apply 落库, 不触运行态, 在跑的网关需重启或走控制台热升级)"
        ),
    )
    upgrade_parser.add_argument("--config", default="config/upstreams.yaml", help="主配置路径")
    upgrade_parser.add_argument("--db", default=None, help="SQLite 配置库路径 (相对路径锚定项目根; 默认 data/config.db)")
    upgrade_parser.add_argument(
        "--apply",
        action="store_true",
        help="实际落库 (默认只预览); 落库前自动写 JSON 备份到 --backup-dir",
    )
    upgrade_parser.add_argument(
        "--resolutions",
        help=(
            "冲突裁决 JSON 文件: {\"<kind>:<name>:<path>\": "
            "\"keep_cur\"|\"accept_new\"}"
        ),
    )
    upgrade_parser.add_argument(
        "--expected-revision",
        type=int,
        default=None,
        help="乐观锁: 落库时库内 revision 必须等于该值",
    )
    upgrade_parser.add_argument(
        "--strict-env",
        action="store_true",
        help="候选装配时 ${VAR} 占位符缺少环境变量按错误处理",
    )
    upgrade_parser.add_argument(
        "--backup-dir",
        default=None,
        help="升级前备份目录 (默认 <db 同级>/backups)",
    )
    return parser


def _resolve_config_path(raw: str) -> Path:
    """L4 (round 11 audit): 相对 --config 与网关同契约锚定项目根, 不锚 CWD。

    否则从非 repo 目录跑 ``unihive config lint`` 默认值会报 not found, 或
    ``--config foo/upstreams.yaml`` 解析到与网关不同的另一套文件。
    """
    return Path(resolve_project_path(raw, "config/upstreams.yaml"))


def _cmd_config_lint(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
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


def _cmd_config_seed_db(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 2

    db_path = resolve_config_db_path(args.db)

    async def _run() -> int:
        store = ConfigStore(str(db_path))
        try:
            await store.initialize()
            if await store.is_seeded() and not args.force:
                print(
                    f"ERROR: {db_path} already seeded (use --force to reseed)",
                    file=sys.stderr,
                )
                return 2
            revision = await seed_database(store, config_path, force=args.force)
            counts = await store.load_bundle()
            print(
                f"seeded {db_path} from {config_path}: "
                f"{len(counts['upstreams'])} upstreams, {len(counts['tools'])} tools, "
                f"{len(counts['sections'])} sections, revision={revision}"
            )
            return 0
        except AlreadySeededError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        finally:
            await store.close()

    try:
        return asyncio.run(_run())
    except ValueError as e:
        # 归一化/明文密钥 fail-closed
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _cmd_config_export(args: argparse.Namespace) -> int:
    db_path = resolve_config_db_path(args.db)

    async def _run() -> int:
        store = ConfigStore(str(db_path))
        try:
            await store.initialize()
            if not await store.is_seeded():
                print(f"ERROR: {db_path} is empty (run `config seed-db` first)", file=sys.stderr)
                return 2
            template = await load_template_from_store(store)
            version = await store.template_version_seeded()
            revision = await store.revision()
            if args.format == "json":
                payload = {
                    "template_version": version,
                    "revision": revision,
                    "bundle": template.to_json(),
                }
                text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            else:
                payload = assemble_config(template)
                text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=10000)
            if args.output == "-":
                print(text)
            else:
                Path(args.output).write_text(text, encoding="utf-8")
                print(f"exported to {args.output} ({args.format})")
            return 0
        finally:
            await store.close()

    return asyncio.run(_run())


def _print_upgrade_preview(
    *, old_version: str, new_version: str, result, lint_errors: list, lint_warnings: list
) -> None:
    """dry-run 人类可读输出: 台账 + 冲突 (含三向值) + lint。"""
    summary = result.summary()
    print(
        f"factory template: {old_version[:12]} -> {new_version[:12]} "
        f"(added={summary['added']} removed={summary['removed']} "
        f"updated={summary['updated']} kept_user={summary['kept_user']} "
        f"conflicts={summary['conflicts']})"
    )
    for group in (result.sections, result.upstreams, result.tools):
        for change in group:
            if change.action == "unchanged":
                continue
            fields = f" fields={list(change.fields)}" if change.fields else ""
            conflicts = (
                f" conflicts={list(change.conflicts)}" if change.conflicts else ""
            )
            print(f"  {change.action:<9} {change.kind:<8} {change.name}{fields}{conflicts}")
    if result.conflicts:
        print("unresolved field conflicts (old | cur | new):")
        for conflict in result.conflicts:
            # to_json() 把缺失侧的 MISSING 哨兵渲染成 null, 避免 CLI 打印哨兵 repr
            payload = conflict.to_json()
            print(
                f"  [{conflict.key}] {payload['conflict']}  "
                f"old={payload['old']!r} cur={payload['cur']!r} "
                f"new={payload['new']!r}"
            )
    for issue in lint_errors:
        print(f"  ERROR   [{issue.code}] {issue.message}", file=sys.stderr)
    for issue in lint_warnings:
        print(f"  WARNING [{issue.code}] {issue.message}")


def _cmd_config_upgrade_db(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 2

    resolutions: dict[str, str] | None = None
    if args.resolutions:
        try:
            resolutions = json.loads(Path(args.resolutions).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: cannot read resolutions file: {e}", file=sys.stderr)
            return 2

    db_path = resolve_config_db_path(args.db)

    async def _run() -> int:
        from .core.config_db import (
            assemble_runtime_candidate,
            plan_config_upgrade,
            upgrade_database,
            validate_resolutions,
        )
        from .core.config_upgrade import unresolved_conflicts

        store = ConfigStore(str(db_path))
        try:
            await store.initialize()
            if not await store.is_seeded():
                print(
                    f"ERROR: {db_path} is empty (run `config seed-db` first)",
                    file=sys.stderr,
                )
                return 2
            snapshot = await store.template_snapshot()
            old_version, old_bundle = snapshot or ("", {})
            cur_bundle = await store.load_bundle()
            try:
                resolutions_clean = validate_resolutions(resolutions)
                new_version, _new_bundle, result = plan_config_upgrade(
                    old_bundle,
                    cur_bundle,
                    config_path=config_path,
                    resolutions=resolutions_clean,
                )
                candidate, candidate_specs, _hidden = assemble_runtime_candidate(
                    result.bundle,
                    config_path=config_path,
                    strict_env=args.strict_env,
                )
                issues = lint_config(candidate, candidate_specs)
            except ValueError as e:
                # 明文密钥 / 裁决形态错误 / 未裁决冲突
                print(f"ERROR: {e}", file=sys.stderr)
                return 1

            lint_errors = [i for i in issues if i.severity == ERROR]
            lint_warnings = [i for i in issues if i.severity != ERROR]
            _print_upgrade_preview(
                old_version=old_version,
                new_version=new_version,
                result=result,
                lint_errors=lint_errors,
                lint_warnings=lint_warnings,
            )

            pending = unresolved_conflicts(result, resolutions_clean)
            if not args.apply:
                if pending:
                    print(
                        f"\ndry-run only: {len(pending)} unresolved conflict(s); "
                        "provide --resolutions file to choose keep_cur/accept_new "
                        "per conflict, then re-run with --apply"
                    )
                return 0

            if pending:
                print(
                    f"\nERROR: {len(pending)} unresolved field conflict(s); "
                    "provide --resolutions to choose keep_cur/accept_new per conflict",
                    file=sys.stderr,
                )
                return 1
            if lint_errors:
                print(
                    f"\nERROR: {len(lint_errors)} lint error(s); upgrade aborted",
                    file=sys.stderr,
                )
                return 1
            outcome = await upgrade_database(
                store,
                config_path=config_path,
                resolutions=resolutions_clean,
                expected_revision=args.expected_revision,
                backup_dir=args.backup_dir,
                strict_env=args.strict_env,
            )
            if not outcome["changed"]:
                print("no factory changes: config.db already up to date")
                return 0
            print(
                f"applied: revision -> {outcome['revision']}, "
                f"backup: {outcome['backup_path']}"
            )
            print(
                "note: running gateways are not touched by CLI apply; restart the "
                "gateway or use the console update center for hot apply"
            )
            return 0
        finally:
            await store.close()

    try:
        return asyncio.run(_run())
    except RevisionConflict as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except LintRejected as e:
        print(f"ERROR: lint rejected candidate config: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        # 落库阶段的 fail-closed 守卫 (明文密钥 / 未裁决冲突等)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.domain == "config" and args.action == "lint":
        return _cmd_config_lint(args)
    if args.domain == "config" and args.action == "seed-db":
        return _cmd_config_seed_db(args)
    if args.domain == "config" and args.action == "export":
        return _cmd_config_export(args)
    if args.domain == "config" and args.action == "upgrade-db":
        return _cmd_config_upgrade_db(args)
    parser.error(f"unknown command: {args.domain} {args.action}")
    return 2  # pragma: no cover - argparse.error exits
