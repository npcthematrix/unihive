"""ConfigUpgradeMixin — 出厂 YAML 三方合并升级 (dry-run + 热应用 + 回滚)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用 (工具摘挂/上游差集/锁) 一律经 ``self``; ``register_tool`` 用
模块限定形式 ``registry.register_tool`` 以便 monkeypatch 命中本模块 binding。
"""
import logging
from pathlib import Path

from ..core import registry

from ._helpers import switch_runtime_locked

logger = logging.getLogger(__name__)


class ConfigUpgradeMixin:
    """出厂更新方法簇 (GatewayServer 组装时混入)。"""

    # ------------------------------------------------------------------
    # P3: 出厂模板更新 (三方合并 dry-run / 热 apply + 回滚)
    # ------------------------------------------------------------------

    async def _prepare_config_upgrade(self, resolutions: dict[str, str] | None) -> dict:
        """出厂更新计划 (纯读): old 快照 + cur 实体 ↔ 新出厂 YAML 三方合并,

        装配候选运行态并跑全量 lint。不写库、不碰运行态/provider。
        """
        from ..core import config_db as _cdb
        from ..core.config_lint import ERROR as LINT_ERROR
        from ..core.config_lint import WARNING as LINT_WARNING
        from ..core.config_lint import lint_config

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "factory upgrade requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        resolutions = _cdb.validate_resolutions(resolutions)
        snapshot = await store.template_snapshot()
        if snapshot is None:
            raise RuntimeError(
                "config.db has no factory template snapshot; seed first"
            )
        old_version, old_bundle = snapshot
        # bundle 与 revision 同快照: apply 缺省 expected 时钉住此值 (H5)。
        cur_bundle, plan_revision = await store.read_bundle()

        config_path = getattr(self, "config_path", None)
        new_version, new_bundle, result = _cdb.plan_config_upgrade(
            old_bundle,
            cur_bundle,
            config_path=config_path,
            resolutions=resolutions,
        )
        candidate, candidate_specs, candidate_hidden = (
            _cdb.assemble_runtime_candidate(
                result.bundle,
                config_path=config_path,
                strict_env=bool(getattr(self, "_strict_env", False)),
            )
        )
        issues = lint_config(candidate, candidate_specs)
        lint_errors = [i for i in issues if i.severity == LINT_ERROR]
        lint_warnings = [i for i in issues if i.severity == LINT_WARNING]
        summary = result.summary()
        changed = bool(summary["added"] or summary["removed"] or summary["updated"])
        return {
            "resolutions": resolutions,
            "revision": plan_revision,
            "old_version": old_version,
            "new_version": new_version,
            "new_bundle": new_bundle,
            "result": result,
            "summary": summary,
            "changed": changed,
            "candidate": candidate,
            "candidate_specs": candidate_specs,
            "candidate_hidden": candidate_hidden,
            "lint_errors": lint_errors,
            "lint_warnings": lint_warnings,
        }

    @staticmethod
    def _upgrade_issue_json(issue) -> dict:
        return {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
        }

    def _upgrade_tool_impact(
        self,
        old_specs: list[dict],
        candidate_specs: list[dict],
        candidate_config: dict,
        candidate_hidden: set[str],
    ) -> tuple[set[str], set[str], set[str]]:
        """全局工具发布差集 → (to_add, to_remove, republish)。

        republish = 新旧都应发布但 spec 内容变化的工具 (先摘后注)。
        wanted 按候选 config 的 enabled 链 + 候选 hidden 集计算, 不依赖
        self.config 已切换 (dry-run 也要能算)。
        """
        from ..core.tool_loader import filter_specs_by_enabled

        # 与 _publishable_specs 同规则: 这里不能直接调它 (dry-run 要能在
        # 候选 config 上计算), 必须复刻 hidden + disable_dangerous 两重过滤,
        # 否则 kill switch 开启时出厂升级/OpenAPI 导入会把 dangerous 工具
        # 重新发布回来。
        dangerous_blocked = bool(candidate_config.get("disable_dangerous", False))
        wanted = {
            spec["name"]
            for spec in filter_specs_by_enabled(candidate_specs, candidate_config)
            if spec.get("name") not in candidate_hidden
            and not (dangerous_blocked and spec.get("dangerous"))
        }
        old_by_name = {s["name"]: s for s in old_specs}
        new_by_name = {s["name"]: s for s in candidate_specs}
        old_published = set(getattr(self, "_published_tools", set()))
        republish = {
            name
            for name in old_published & wanted
            if self._canonical_config(old_by_name.get(name))
            != self._canonical_config(new_by_name.get(name))
        }
        to_remove = (old_published - wanted) | republish
        to_add = (wanted - old_published) | republish
        return to_add, to_remove, republish

    def _upgrade_upstream_impact(
        self, candidate_config: dict
    ) -> tuple[list[str], list[str], list[str]]:
        """据 live 已解析连接块与已挂载 client, 算 (attach, reconnect, detach)。"""
        old_blocks = self.config.get("upstreams") or {}
        new_blocks = candidate_config.get("upstreams") or {}
        attach: list[str] = []
        reconnect: list[str] = []
        detach: list[str] = []
        for name in sorted(set(old_blocks) | set(new_blocks)):
            old_block = old_blocks.get(name)
            new_block = new_blocks.get(name)
            attached = name in self.upstreams
            if not (new_block and new_block.get("enabled")):
                if attached:
                    detach.append(name)
                continue
            if not attached:
                attach.append(name)
            elif self._canonical_config(old_block) != self._canonical_config(
                new_block
            ):
                reconnect.append(name)
        return attach, reconnect, detach

    async def preview_config_upgrade(
        self, resolutions: dict[str, str] | None = None
    ) -> dict:
        """出厂更新 dry-run: 台账 + 冲突三向值 + lint + 运行态影响面, 零写入。"""
        plan = await self._prepare_config_upgrade(resolutions)
        result = plan["result"]
        old_specs = list(getattr(self, "tool_specs", []))
        to_add, to_remove, republish = self._upgrade_tool_impact(
            old_specs,
            plan["candidate_specs"],
            plan["candidate"],
            plan["candidate_hidden"],
        )
        attach, reconnect, detach = self._upgrade_upstream_impact(plan["candidate"])
        return {
            "changed": plan["changed"],
            "revision": plan["revision"],
            "old_version": plan["old_version"],
            "new_version": plan["new_version"],
            "template_changed": plan["old_version"] != plan["new_version"],
            "summary": plan["summary"],
            "sections": [c.to_json() for c in result.sections],
            "upstreams_changes": [c.to_json() for c in result.upstreams],
            "tools_changes": [c.to_json() for c in result.tools],
            "conflicts": [c.to_json() for c in result.conflicts],
            "resolutions_applied": sorted(plan["resolutions"]),
            "lint_errors": [self._upgrade_issue_json(i) for i in plan["lint_errors"]],
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in plan["lint_warnings"]
            ],
            "impact": {
                "tools_added": sorted(to_add - republish),
                "tools_removed": sorted(to_remove - republish),
                "tools_republished": sorted(republish),
                "upstreams_attach": attach,
                "upstreams_reconnect": reconnect,
                "upstreams_detach": detach,
            },
        }

    async def apply_config_upgrade(
        self,
        *,
        resolutions: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        """热应用出厂更新: 合并 → lint 门 → 备份 → 运行态切换 → 单事务落库。

        - 仍有未裁决字段冲突 → ValueError (HTTP 400), 什么都不动;
        - lint error → LintRejected (HTTP 422), 什么都不动;
        - 无出厂变化 → no-op;
        - 运行态: live config/specs/hidden/router 引用替换, 按连接块差集
          detach/attach 上游 (建连失败仅警告, 与开机"配了但连不上"同态),
          按发布差集摘除/重发/重注册工具;
        - provider 切换或落库失败 → 运行态/已挂 client 尽力回滚, 库不动;
        - 落库同时把 config_template 快照前推到新出厂 bundle (下次合并基线)。
        """
        import asyncio

        from ..core import config_db as _cdb
        from ..core.config_lint import LintRejected
        from ..core.config_upgrade import section_origins, unresolved_conflicts
        from ..utils import console_api as _ca

        plan = await self._prepare_config_upgrade(resolutions)
        result = plan["result"]
        pending = unresolved_conflicts(result, plan["resolutions"])
        if pending:
            keys = ", ".join(c.key for c in pending[:10])
            raise ValueError(
                f"{len(pending)} unresolved field conflict(s): {keys}; "
                "resolve each with keep_cur/accept_new before applying"
            )
        if plan["lint_errors"]:
            raise LintRejected(plan["lint_errors"])
        if not plan["changed"]:
            return {
                "changed": False,
                "revision": plan["revision"],
                "old_version": plan["old_version"],
                "new_version": plan["new_version"],
                "summary": plan["summary"],
            }

        store = self.config_store
        cur_bundle = await store.load_bundle()
        seeded_version = await store.template_version_seeded()
        backup_dir = Path(store.db_path).parent / "backups"
        # 备份是升级回滚的唯一凭据: 任何状态切换前先落盘。
        backup_path = await asyncio.to_thread(
            _cdb.write_upgrade_backup,
            backup_dir,
            cur_bundle,
            revision=plan["revision"],
            template_version_seeded=seeded_version,
        )

        candidate = plan["candidate"]
        candidate_specs = plan["candidate_specs"]
        candidate_hidden = plan["candidate_hidden"]

        async with self._upstream_toggle_lock:
            old_config = self.config
            old_specs = list(self.tool_specs)
            old_hidden = set(self._db_hidden_tools)
            old_blocks = old_config.get("upstreams") or {}
            new_blocks = candidate.get("upstreams") or {}

            # 影响面必须在引用切换前算: 差集基于当前 live 块与已挂 client。
            to_add, to_remove, republish = self._upgrade_tool_impact(
                old_specs, candidate_specs, candidate, candidate_hidden
            )
            attach_names, reconnect_names, detach_names = (
                self._upgrade_upstream_impact(candidate)
            )

            # 1) 运行态引用切换 (独立新 dict, 回滚只是换回引用)。
            switch_runtime_locked(
                self, candidate, candidate_specs, candidate_hidden
            )
            # gateway 调参 (per_upstream_timeout / rate_limit) 也要热生效:
            # 旧链路换了 config 却不重跑 _apply_gateway_tuning, 改超时/限流
            # 要重启才生效, 与其 docstring 矛盾。回滚路径同样重跑一次复原。
            self._apply_gateway_tuning()

            warnings: list[str] = []
            attached: list[str] = []
            reconnected: list[str] = []
            detached: list[str] = []
            errors: list[str] = []

            # 2) 先摘工具 (M8): 任何 client 断开前, 失去 backer / spec 变更的
            #    工具必须先从 provider 摘除, 杜绝切换窗内新调用被路由到正在
            #    关闭的 client。此时 client 全部未动, 回滚只需补注册。
            removed = self._unpublish_tools_locked(to_remove, errors)
            if errors:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=set(),
                    to_remove=to_remove,
                    switched_upstreams=[],
                    old_blocks=old_blocks,
                )
                self._raise_if_toggle_errors("factory-upgrade", errors)

            # 3) 上游 client 差集收敛 (先断后连; 失败只警告, 不阻断升级)。
            for name in sorted(set(detach_names) | set(reconnect_names)):
                if name in self.upstreams:
                    try:
                        await self._detach_upstream_client_locked(name)
                    except asyncio.CancelledError:
                        # 与 attach 侧同守卫 (Round-9 M3): detach 被 cancel 时
                        # live config/specs 已换, 任由 BaseException 逃出锁 =
                        # 长期半迁移。先把本笔记入切换清单, 回滚后重抛。
                        (detached if name in detach_names else reconnected).append(name)
                        await self._rollback_config_upgrade_locked(
                            old_config=old_config,
                            old_specs=old_specs,
                            old_hidden=old_hidden,
                            to_add=to_add,
                            to_remove=to_remove,
                            switched_upstreams=attached + reconnected + detached,
                            old_blocks=old_blocks,
                        )
                        raise
                    if name in detach_names:
                        detached.append(name)
            for name in sorted(set(attach_names) | set(reconnect_names)):
                new_block = new_blocks.get(name)
                try:
                    _client, connected = await self._attach_upstream_client_locked(
                        name, new_block
                    )
                except asyncio.CancelledError:
                    # M3 (round 9 audit): CancelledError 是 BaseException, 旧
                    # ``except Exception`` 接不住 → 直接逃出锁, live config/specs
                    # 已换、部分 client 已 detach, DB 未变 → 长期半迁移。回滚后重抛。
                    (reconnected if name in old_blocks else attached).append(name)
                    await self._rollback_config_upgrade_locked(
                        old_config=old_config,
                        old_specs=old_specs,
                        old_hidden=old_hidden,
                        to_add=to_add,
                        to_remove=to_remove,
                        switched_upstreams=attached + reconnected + detached,
                        old_blocks=old_blocks,
                    )
                    raise
                except Exception as e:  # noqa: BLE001 - 建连失败按开机语义降级
                    warnings.append(
                        f"upstream {name}: attach failed: "
                        f"{type(e).__name__}: {e}"
                    )
                    logger.exception("factory upgrade: attach upstream %r failed", name)
                    # 仍记入切换清单: 失败的 client 可能已登记, 回滚路径要尝试复原。
                    (reconnected if name in old_blocks else attached).append(name)
                    continue
                if name in reconnect_names or name in old_blocks:
                    reconnected.append(name)
                else:
                    attached.append(name)
                if not connected:
                    warnings.append(
                        f"upstream {name}: configured but not connected "
                        "(tools stay published; same semantics as startup)"
                    )

            # 4) 新 client 就位后再发布新工具/重发布 (单工具失败收集, 必须全
            #    成功才落库; 失败 → client 与 provider 整体回滚)。
            added = self._publish_tools_locked(to_add, errors)
            if errors:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=attached + reconnected + detached,
                    old_blocks=old_blocks,
                )
                self._raise_if_toggle_errors("factory-upgrade", errors)

            # 4) 单事务落库 + 快照前推 (乐观锁)。失败 → 运行态尽力回滚。
            # expected 缺省钉住 plan 时刻 revision: plan→apply 之间若有并发
            # 实体写入, 全表重写必须失败回滚, 不得物理抹掉并发改动 (H5)。
            expected = (
                plan["revision"]
                if expected_revision is None
                else expected_revision
            )
            try:
                revision = await store.apply_upgrade_bundle(
                    plan["new_version"],
                    plan["new_bundle"],
                    result.bundle,
                    section_origins=section_origins(
                        plan["new_bundle"], result.bundle
                    ),
                    expected_revision=expected,
                )
            except BaseException:  # noqa: BLE001 - M3: 含 CancelledError, 回滚后重抛
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=attached + reconnected + detached,
                    old_blocks=old_blocks,
                )
                raise

        _ca.set_config_db(store, revision)
        _ca.invalidate_config_caches()
        logger.info(
            "Factory upgrade applied: template %s -> %s, rev=%d, "
            "tools +%d/-%s/republish %d, upstreams attached=%s reconnected=%s "
            "detached=%s, backup=%s",
            plan["old_version"][:12],
            plan["new_version"][:12],
            revision,
            len(added),
            len(removed),
            len(republish),
            attached,
            reconnected,
            detached,
            backup_path,
        )
        return {
            "changed": True,
            "revision": revision,
            "old_version": plan["old_version"],
            "new_version": plan["new_version"],
            "summary": plan["summary"],
            "sections": [c.to_json() for c in result.sections],
            "upstreams_changes": [c.to_json() for c in result.upstreams],
            "tools_changes": [c.to_json() for c in result.tools],
            "resolutions_applied": sorted(plan["resolutions"]),
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in plan["lint_warnings"]
            ],
            "tools_added": sorted(set(added) - republish),
            "tools_removed": sorted(set(removed) - republish),
            "tools_republished": sorted(republish),
            "upstreams_attached": attached,
            "upstreams_reconnected": reconnected,
            "upstreams_detached": detached,
            "warnings": warnings,
            "backup_path": str(backup_path),
        }

    async def _rollback_config_upgrade_locked(
        self,
        *,
        old_config: dict,
        old_specs: list[dict],
        old_hidden: set[str],
        to_add: set[str],
        to_remove: set[str],
        switched_upstreams: list[str],
        old_blocks: dict,
    ) -> None:
        """升级失败回滚 (正向顺序的逆序): 摘新工具 → client 复原 → 补注册
        旧工具 → 运行态引用复位。

        与正向 "先摘工具 → client 切换 → 再发布" 严格互逆: 旧 client 复原
        之后才补注册旧工具, 杜绝补回的工具瞬间路由到已关闭 client。所有
        补发/重连均尽力而为, 失败只记录 (原异常正在传播); 复位后
        _published_tools 以编辑前快照为准, 漂移留待上游 reconcile 自愈。
        """
        from ..utils import console_api as _ca

        for name in sorted(to_add):
            try:
                self.mcp.local_provider.remove_tool(name)
            except KeyError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("upgrade rollback: remove %r failed", name)
            self._published_tools.discard(name)

        for name in switched_upstreams:
            old_block = old_blocks.get(name)
            try:
                if name in self.upstreams:
                    await self._detach_upstream_client_locked(name)
                if old_block and old_block.get("enabled"):
                    await self._attach_upstream_client_locked(name, old_block)
            except Exception:  # noqa: BLE001
                logger.exception("upgrade rollback: restore upstream %r failed", name)

        old_by_name = {s["name"]: s for s in old_specs}
        for name in sorted(to_remove):
            spec = old_by_name.get(name)
            if spec is None:
                continue
            try:
                registry.register_tool(self, spec)
                self._published_tools.add(name)
            except Exception:  # noqa: BLE001
                logger.exception("upgrade rollback: re-register %r failed", name)
                self._published_tools.discard(name)

        switch_runtime_locked(self, old_config, old_specs, old_hidden)
        # 复原 gateway 调参 (per_upstream_timeout / rate_limit)。
        self._apply_gateway_tuning()
