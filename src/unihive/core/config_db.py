"""v2 配置库的高层编排: YAML 模板 → seed, 实体 → 装配 (网关与 CLI 共用)。

存储层 (storage/config_store.py) 只认 dict bundle; 归一化/装配/明文密钥
扫描这些业务规则集中在本模块, 避免网关与 CLI 两条路径漂移。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_upgrade import (
    RESOLUTION_ACCEPT_NEW,
    RESOLUTION_KEEP_CUR,
    UpgradeMergeResult,
    merge_bundles,
    section_origins,
    unresolved_conflicts,
)
from .db_assembler import (
    assemble_config,
    entities_from_json,
    merge_bootstrap,
)
from .template_normalize import (
    find_plaintext_secrets,
    load_raw_config,
    load_raw_template,
    normalize_template,
    template_version,
)
from .tool_loader import filter_specs_by_env

if TYPE_CHECKING:
    from ..storage.config_store import ConfigStore
    from .template_normalize import NormalizedTemplate

logger = logging.getLogger(__name__)

_VALID_RESOLUTIONS = {RESOLUTION_KEEP_CUR, RESOLUTION_ACCEPT_NEW}


def resolve_layout_root(file_path: str | Path) -> Path:
    """解析运行时资源根, 兼容 dev (src 布局) 与 installed (site-packages) 布局。

    M1 (round 10 audit): 旧实现固定取 ``parents[3]``, 只在 dev
    (``<root>/src/unihive/core/config_db.py``, parents[3]=<root>) 正确。
    wheel 安装后路径为 ``site-packages/unihive/core/config_db.py``,
    parents[3] 多爬一层 (venv/Lib 等) → cache.db/config 与控制台侧路径
    split-brain。以 ``<root>/src/unihive`` 标记识别 src 布局, 否则回落
    parents[2] (site-packages)。
    """
    f = Path(file_path).resolve()
    src_root = f.parents[3]
    if (src_root / "src" / "unihive").exists():
        return src_root
    return f.parents[2]


def project_root() -> Path:
    """运行时资源根 (dev=仓库根; installed=site-packages, console.html/static
    经 hatchling force-include 落在此处)。"""
    return resolve_layout_root(Path(__file__))


def resolve_config_db_path(path: str | Path | None) -> Path:
    """config.db 路径统一锚定项目根 (M10)。

    - None / 相对路径: 相对**项目根**解析, 与启动时 CWD 无关 —— 否则从别的
      工作目录启动会在 CWD 下静默生成空库并重新 seed, 用户配置看似"丢失"
      (裂脑);
    - 绝对路径: 原样返回 (显式指向库外实例的逃生通道)。
    """
    # L1 (round 9 audit): 空串视为未设置 —— Path("") 是 ".", project_root()/"" 会
    # 解析成项目根**目录**而非文件, 后续 sqlite 打开报难读错。显式当作 None。
    if path is None or (isinstance(path, str) and path.strip() == ""):
        return project_root() / "data" / "config.db"
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root() / candidate


def resolve_project_path(path: str | Path | None, default: str | Path) -> Path:
    """锚定相对路径到项目根 (M9, round 9 audit)。

    config.db 已由 ``resolve_config_db_path`` 锚定, 但其它 runtime 状态路径
    (cache.db / board.db / tdx_quant strategy-id / proxy_settings.yaml) 仍按
    CWD 解析 → 从非 repo 根启动会读写到另一个文件 (split-brain: config.db
    指向 repo 根, cache.db 指向 CWD, 状态文件分裂; tdx_quant strategy-id 的
    UUID 还会 churn, 破坏 TdxW.exe 跨重启连续性)。

    - None / 空串 → ``default``, 再按相对锚定 (Path('')='.' 会把项目根目录当
      文件, 显式当作未设置);
    - 相对路径: 相对**项目根**解析, 与启动 CWD 无关;
    - 绝对路径: 原样返回 (显式指向库外实例的逃生通道)。
    """
    if path is None or (isinstance(path, str) and path.strip() == ""):
        path = default
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root() / candidate


def validate_template_for_seed(
    template: "NormalizedTemplate",
    config_path: str | Path | None = "config/upstreams.yaml",
) -> None:
    """空库 seed 提交前的准入门 (H4)。

    模板按启动同一条装配链 (实体 → assemble → bootstrap → env 保留占位)
    组装候选运行态, 必须同时过 ``validate_config`` 与 lint ERROR 门;
    否则抛 ValueError / LintRejected, 调用方在 seed 事务**之前**拒绝,
    坏模板不会粘进空库 —— 修好出厂 YAML 后下次启动即可正常首灌。
    """
    from ..utils.config_loader import validate_config
    from .config_lint import ERROR as LINT_ERROR
    from .config_lint import LintRejected, lint_config

    candidate, candidate_specs, _hidden = assemble_runtime_candidate(
        template.to_json(), config_path=config_path, strict_env=False
    )
    errors = validate_config(candidate)
    if errors:
        raise ValueError(
            "factory template failed validation before seeding config.db "
            f"({len(errors)} error(s)): " + "; ".join(errors)
        )
    lint_errors = [
        issue
        for issue in lint_config(candidate, candidate_specs)
        if issue.severity == LINT_ERROR
    ]
    if lint_errors:
        raise LintRejected(lint_errors)


def validate_resolutions(resolutions: Any) -> dict[str, str]:
    """校验 dry-run/apply 请求携带的冲突裁决表形态。

    形态: ``{"<kind>:<name>:<path>": "accept_new"|"keep_cur"}``。
    非法形态抛 ValueError (HTTP 层映射 400)。
    """
    if resolutions is None:
        return {}
    if not isinstance(resolutions, dict):
        raise ValueError("resolutions must be an object keyed by conflict path")
    normalized: dict[str, str] = {}
    for key, value in resolutions.items():
        if not isinstance(key, str) or not key:
            raise ValueError("resolution keys must be non-empty strings")
        if value not in _VALID_RESOLUTIONS:
            raise ValueError(
                f"resolution for {key!r} must be one of "
                f"{sorted(_VALID_RESOLUTIONS)}, got {value!r}"
            )
        normalized[key] = value
    return normalized


def build_template(config_path: str | Path = "config/upstreams.yaml") -> "NormalizedTemplate":
    """读出厂 YAML 模板 (未解析 env) 并归一化为实体 bundle。

    发现疑似明文密钥 (非 ${VAR} 占位的 api_key/password/secret/token) 直接
    ValueError fail-closed, 拒绝把密钥写进 config.db。
    """
    merged, specs, provenance, _path = load_raw_template(config_path)
    template = normalize_template(merged, specs, provenance)
    leaked = find_plaintext_secrets(template)
    if leaked:
        raise ValueError(
            "Refusing to seed config.db with plaintext secret fields: " + ", ".join(leaked)
        )
    return template


async def ensure_seeded(
    store: "ConfigStore",
    config_path: str | Path = "config/upstreams.yaml",
) -> "NormalizedTemplate":
    """空库自动 seed; 已 seed 则直接读出库内实体。返回当前实体模板。

    坏模板在 seed 事务前被 :func:`validate_template_for_seed` 拒绝 (H4),
    库保持空, 修复出厂 YAML 后重试即可, 无需删库/force 重灌。
    """
    if await store.is_seeded():
        return await load_template_from_store(store)
    template = build_template(config_path)
    validate_template_for_seed(template, config_path)
    version = template_version(template)
    # M1 (round 9 audit): is_seeded() 与 seed_from_bundle() 之间无跨进程锁,
    # 两进程同时首启空库时, B 在 check-then-seed 竞赛窗口内拿锁、复查发现
    # 已有实体抛 AlreadySeededError (RuntimeError, 非 ValueError, main 不做
    # 干净退出映射)。配置完全正确的 B 进程不应仅因"输了首启竞赛"而启动失败
    # —— 把它当作"已 seed, 读现有库继续"。
    from ..storage.config_store import AlreadySeededError

    try:
        await store.seed_from_bundle(version, template.to_json())
        logger.info(
            "Seeded config.db from %s: %d upstreams, %d tools, template=%s",
            config_path,
            len(template.upstreams),
            len(template.tools),
            version[:12],
        )
        return template
    except AlreadySeededError:
        logger.info(
            "config.db already seeded by a concurrent process; "
            "loading existing entities instead"
        )
        return await load_template_from_store(store)


async def seed_database(
    store: "ConfigStore",
    config_path: str | Path = "config/upstreams.yaml",
    *,
    force: bool = False,
    expected_revision: int | None = None,
) -> int:
    """CLI ``config seed-db`` 入口: 从 YAML 模板 (重)灌库, 返回新 revision。

    提交前同样过 seed 准入门 (H4): 坏模板连 ``--force`` 也拒绝, 避免清空
    好库后写入坏实体。
    """
    template = build_template(config_path)
    validate_template_for_seed(template, config_path)
    return await store.seed_from_bundle(
        template_version(template),
        template.to_json(),
        force=force,
        expected_revision=expected_revision,
    )


async def load_template_from_store(store: "ConfigStore") -> "NormalizedTemplate":
    """读库内实体并还原为 NormalizedTemplate (装配器输入)。"""
    return entities_from_json(await store.load_bundle())


# ----------------------------------------------------------------------
# P3: 出厂模板更新 (字段级三方合并 dry-run / apply 的纯编排, 网关与 CLI 共用)
# ----------------------------------------------------------------------

def plan_config_upgrade(
    old_bundle: dict[str, Any] | None,
    cur_bundle: dict[str, Any],
    *,
    config_path: str | Path = "config/upstreams.yaml",
    resolutions: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any], "UpgradeMergeResult"]:
    """重建最新出厂 YAML 模板并与库内现状做字段级三方合并。

    返回 ``(new_version, new_bundle, merge_result)``:

    - ``old_bundle`` 取 config_template 快照 (上次 seed/升级的出厂基线);
      历史空快照时传 None/{} —— 等价 cur 全量视为用户新增;
    - 出厂 YAML 含疑似明文密钥时 build_template fail-closed ValueError;
    - ``resolutions`` 形如 ``{"tool:foo:spec.description": "accept_new"}``,
      形态由 :func:`validate_resolutions` 先守。

    本函数纯读 + 纯计算, 不写库、不碰运行态。
    """
    resolutions = validate_resolutions(resolutions)
    template = build_template(config_path)
    new_bundle = template.to_json()
    result = merge_bundles(
        old_bundle or {}, cur_bundle, new_bundle, resolutions=resolutions
    )
    return template_version(template), new_bundle, result


def assemble_runtime_candidate(
    bundle: dict[str, Any],
    *,
    config_path: str | Path | None = "config/upstreams.yaml",
    strict_env: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    """合并后 bundle → 候选运行时 config (与启动/单工具编辑同一条装配链)。

    返回 ``(resolved_config, env 门控后 specs, hidden 工具名集)``。
    bootstrap (console + 监听键, 来自同目录 config.yaml) 仅在 config_path
    给定时合并; __new__ 旁路 __init__ 的测试可传 None 跳过。
    """
    from ..utils.config_loader import resolve_env_vars

    template = entities_from_json(bundle)
    candidate = assemble_config(template)
    if config_path is not None:
        bootstrap_path = Path(config_path).parent / "config.yaml"
        merge_bootstrap(candidate, load_raw_config(bootstrap_path))
    candidate, _missing = resolve_env_vars(candidate, strict=bool(strict_env))
    candidate_specs = filter_specs_by_env(list(candidate.get("tools") or []))
    hidden = {tool.name for tool in template.tools if tool.publish_mode == "hidden"}
    return candidate, candidate_specs, hidden


def write_upgrade_backup(
    backup_dir: str | Path,
    cur_bundle: dict[str, Any],
    *,
    revision: int,
    template_version_seeded: str | None,
) -> Path:
    """apply 前把库内当前 bundle 落一份 JSON 备份, 返回备份文件路径。

    同步文件 IO (一次性, 备份是升级回滚的唯一凭据, 必须落盘成功才继续)。
    """
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"config-upgrade-{stamp}-rev{revision}.json"
    payload = {
        "kind": "unihive-config-upgrade-backup",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revision_before": revision,
        "template_version_before": template_version_seeded,
        "bundle": cur_bundle,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


async def upgrade_database(
    store: "ConfigStore",
    *,
    config_path: str | Path = "config/upstreams.yaml",
    resolutions: dict[str, str] | None = None,
    expected_revision: int | None = None,
    backup_dir: str | Path | None = None,
    strict_env: bool = False,
) -> dict[str, Any]:
    """CLI 升级路径: 纯库内 apply (不触运行态; 在跑的网关需重启或走控制台热升级)。

    步骤: 读 old 快照 + cur → 重建出厂 YAML 三方合并 → 未裁决冲突拒绝 →
    候选装配 + lint error 拒绝 → cur bundle JSON 备份落盘 → 单事务全量
    替换实体并把出厂快照前推到新模板, revision +1。

    返回 ``{changed, revision, old_version, new_version, summary,
    backup_path, result}``; 无出厂变化时 no-op (不备份、不 bump)。
    """
    from .config_lint import ERROR as LINT_ERROR
    from .config_lint import LintRejected, lint_config

    resolutions = validate_resolutions(resolutions)
    snapshot = await store.template_snapshot()
    if snapshot is None:
        raise RuntimeError("config.db has no factory template snapshot; seed first")
    old_version, old_bundle = snapshot
    cur_bundle = await store.load_bundle()

    new_version, new_bundle, result = plan_config_upgrade(
        old_bundle,
        cur_bundle,
        config_path=config_path,
        resolutions=resolutions,
    )
    summary = result.summary()
    changed = bool(summary["added"] or summary["removed"] or summary["updated"])
    if not changed:
        return {
            "changed": False,
            "revision": await store.revision(),
            "old_version": old_version,
            "new_version": new_version,
            "summary": summary,
            "backup_path": None,
            "result": result,
        }
    pending = unresolved_conflicts(result, resolutions)
    if pending:
        keys = ", ".join(c.key for c in pending[:10])
        raise ValueError(
            f"{len(pending)} unresolved field conflict(s): {keys}; "
            "supply resolutions to choose keep_cur/accept_new per conflict"
        )

    candidate, candidate_specs, _hidden = assemble_runtime_candidate(
        result.bundle, config_path=config_path, strict_env=strict_env
    )
    lint_errors = [
        issue
        for issue in lint_config(candidate, candidate_specs)
        if issue.severity == LINT_ERROR
    ]
    if lint_errors:
        raise LintRejected(lint_errors)

    revision_before = await store.revision()
    seeded_version = await store.template_version_seeded()
    if backup_dir is None:
        backup_dir = Path(str(getattr(store, "db_path", "data/config.db"))).parent / "backups"
    backup_path = await asyncio.to_thread(
        write_upgrade_backup,
        Path(backup_dir),
        copy.deepcopy(cur_bundle),
        revision=revision_before,
        template_version_seeded=seeded_version,
    )

    revision = await store.apply_upgrade_bundle(
        new_version,
        new_bundle,
        result.bundle,
        section_origins=section_origins(new_bundle, result.bundle),
        expected_revision=expected_revision,
    )
    logger.info(
        "config.db upgraded to template %s (rev %d->%d, backup=%s)",
        new_version[:12],
        revision_before,
        revision,
        backup_path,
    )
    return {
        "changed": True,
        "revision": revision,
        "old_version": old_version,
        "new_version": new_version,
        "summary": summary,
        "backup_path": str(backup_path),
        "result": result,
    }
