"""OpenApiImportMixin — FQGate OpenAPI 实时拉取/热导入 (P4)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
工具摘挂经 ``self._publish/_unpublish_tools_locked`` (HotToggleMixin)。
"""
import asyncio
import logging
from pathlib import Path

from ._helpers import switch_runtime_locked

logger = logging.getLogger(__name__)


class OpenApiImportMixin:
    """FQGate OpenAPI 导入方法簇 (GatewayServer 组装时混入)。"""

    # ------------------------------------------------------------------
    # P4: FQGate OpenAPI 实时导入 (origin='user' 工具行热注册)
    # ------------------------------------------------------------------

    async def _prepare_openapi_import(
        self, upstream_name: str, selected_names: list[str] | None
    ) -> dict:
        """OpenAPI 导入计划 (纯读): 活拉 OpenAPI + tools/list → 转换 → 差异 →
        候选运行态 → lint → 发布差集 → YAML 片段。不写库、不碰运行态/provider。

        - 上游未在 config 注册 → KeyError (HTTP 404);
        - 已配置但当前未挂载 client (禁用/启动建连失败) → RuntimeError (503);
        - client 不具备 OpenAPI 导入能力 (非 MCP 网关型) → ValueError (400);
        - 上游拉取异常 (OpenApiFetchError / tools/list !ok) → 原样抛 (502)。
        """
        from ..core import config_db as _cdb
        from ..core import openapi_import as _oi
        from ..core.config_lint import ERROR as LINT_ERROR
        from ..core.config_lint import WARNING as LINT_WARNING
        from ..core.config_lint import lint_config

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "OpenAPI import requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        client = self.upstreams.get(upstream_name)
        if client is None:
            if upstream_name not in (self.config.get("upstreams") or {}):
                raise KeyError(upstream_name)
            raise RuntimeError(
                f"upstream {upstream_name!r} is configured but not connected "
                "(disabled or startup attach failed)"
            )
        fetch_openapi = getattr(client, "fetch_openapi", None)
        list_tools_raw = getattr(client, "list_tools_raw", None)
        if not callable(fetch_openapi) or not callable(list_tools_raw):
            raise ValueError(
                f"upstream {upstream_name!r} does not support OpenAPI import "
                "(client has no fetch_openapi/list_tools_raw)"
            )

        openapi_doc = await fetch_openapi()
        raw = await list_tools_raw()
        if not getattr(raw, "ok", False):
            raise RuntimeError(
                f"upstream {upstream_name!r} tools/list failed: "
                f"{getattr(raw, 'error', None)}"
            )

        entities, unmapped = _oi.convert_openapi_tools(
            openapi_doc, list(raw.tools or [])
        )
        # bundle 与 revision 同快照: apply 缺省 expected 时钉住此值 (H5)。
        cur_bundle, plan_revision = await store.read_bundle()
        plan = _oi.plan_openapi_import(
            cur_bundle,
            entities,
            upstream_label=upstream_name,
            unmapped=unmapped,
        )
        selected_set = set(selected_names) if selected_names is not None else None
        # change_entities 对不在 added/changed 集的名字抛 ValueError
        selected = plan.change_entities(selected_set)
        merged_bundle = _oi.merge_imported_tools(cur_bundle, selected)
        final_names = [e["name"] for e in selected]
        # 落库行取合并后的实体: 已存在行带保留的 position/门控列 (store 还会
        # 再保一次, 这里保证候选运行态与落库行同源)。
        upsert_rows = [
            row for row in merged_bundle["tools"] if row["name"] in set(final_names)
        ]

        config_path = getattr(self, "config_path", None)
        candidate, candidate_specs, candidate_hidden = (
            _cdb.assemble_runtime_candidate(
                merged_bundle,
                config_path=config_path,
                strict_env=bool(getattr(self, "_strict_env", False)),
            )
        )
        issues = lint_config(candidate, candidate_specs)
        to_add, to_remove, republish = self._upgrade_tool_impact(
            list(self.tool_specs),
            candidate_specs,
            candidate,
            candidate_hidden,
        )
        fragment = _oi.render_yaml_fragment(selected)
        return {
            "revision": plan_revision,
            "plan": plan,
            "unmapped": plan.unmapped,
            "selected": final_names,
            "upsert_rows": upsert_rows,
            "cur_bundle": cur_bundle,
            "candidate": candidate,
            "candidate_specs": candidate_specs,
            "candidate_hidden": candidate_hidden,
            "lint_errors": [i for i in issues if i.severity == LINT_ERROR],
            "lint_warnings": [i for i in issues if i.severity == LINT_WARNING],
            "to_add": to_add,
            "to_remove": to_remove,
            "republish": republish,
            "yaml_fragment": fragment,
        }

    async def preview_openapi_import(
        self,
        *,
        upstream: str = "THS-LOCAL-GATEWAY",
        tools: list[str] | None = None,
    ) -> dict:
        """OpenAPI 导入 dry-run: 差异台账 + lint + 发布影响面 + YAML 片段, 零写入。"""
        prep = await self._prepare_openapi_import(upstream, tools)
        plan = prep["plan"]
        return {
            "changed": bool(prep["selected"]),
            "revision": prep["revision"],
            "upstream": upstream,
            "summary": plan.summary(),
            "changes": [c.to_json() for c in plan.changes],
            "unmapped": [u.to_json() for u in prep["unmapped"]],
            "removed": list(plan.removed),
            "selected": prep["selected"],
            "lint_errors": [self._upgrade_issue_json(i) for i in prep["lint_errors"]],
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in prep["lint_warnings"]
            ],
            "impact": {
                "tools_added": sorted(prep["to_add"] - prep["republish"]),
                "tools_removed": sorted(prep["to_remove"] - prep["republish"]),
                "tools_republished": sorted(prep["republish"]),
                # 导入只动工具契约, 永不重连/断开上游
                "upstreams_attach": [],
                "upstreams_reconnect": [],
                "upstreams_detach": [],
            },
            "yaml_fragment": prep["yaml_fragment"],
        }

    async def apply_openapi_import(
        self,
        *,
        upstream: str = "THS-LOCAL-GATEWAY",
        tools: list[str] | None = None,
        confirm_dangerous: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """热应用 OpenAPI 导入: lint 门 → 强确认 → 备份 → 运行态切换 →
        provider 摘/注 (只 add/republish, **永不删除**) → 单事务 upsert_tools。

        provider 切换或落库失败 → 运行态/provider 尽力回滚, 库不动;
        ``plan.removed`` 仅作顾问信息展示, apply 路径不删任何工具行。
        """
        from ..core import config_db as _cdb
        from ..core.config_lint import LintRejected
        from ..core.entity_edit import DangerousConfirmationRequired
        from ..utils import console_api as _ca

        prep = await self._prepare_openapi_import(upstream, tools)
        if prep["lint_errors"]:
            raise LintRejected(prep["lint_errors"])

        plan = prep["plan"]
        selected = prep["selected"]
        if not selected:
            return {
                "changed": False,
                "revision": prep["revision"],
                "upstream": upstream,
                "summary": plan.summary(),
            }

        # 导入即新增/改写工具契约 (params/mapping/routing), 一律契约层强确认。
        if not confirm_dangerous:
            raise DangerousConfirmationRequired(
                [f"tool:{name}:contract" for name in selected]
            )

        store = self.config_store
        seeded_version = await store.template_version_seeded()
        backup_dir = Path(store.db_path).parent / "backups"
        backup_path = await asyncio.to_thread(
            _cdb.write_upgrade_backup,
            backup_dir,
            prep["cur_bundle"],
            revision=prep["revision"],
            template_version_seeded=seeded_version,
        )

        candidate = prep["candidate"]
        candidate_specs = prep["candidate_specs"]
        candidate_hidden = prep["candidate_hidden"]
        to_add, to_remove, republish = (
            prep["to_add"], prep["to_remove"], prep["republish"]
        )

        async with self._upstream_toggle_lock:
            old_config = self.config
            old_specs = list(self.tool_specs)
            old_hidden = set(self._db_hidden_tools)

            # 1) 运行态引用切换 (独立新 dict, 回滚只是换回引用)。
            switch_runtime_locked(
                self, candidate, candidate_specs, candidate_hidden
            )

            # 2) provider: 契约变化的先摘, 新增/重注的后发; 无删除语义。
            errors: list[str] = []
            removed = self._unpublish_tools_locked(to_remove, errors)
            added = self._publish_tools_locked(to_add, errors)
            if errors:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=[],
                    old_blocks={},
                )
                self._raise_if_toggle_errors("openapi-import", errors)

            # 3) 单事务批量落库 (乐观锁; origin 恒 user, 门控列 store 保留)。
            # expected 缺省钉住 prep 时刻 revision, plan→apply 之间的并发
            # 写入必须让导入失败回滚, 不得被批量 upsert 静默冲掉 (H5)。
            expected = (
                prep["revision"]
                if expected_revision is None
                else expected_revision
            )
            try:
                revision = await store.upsert_tools(
                    prep["upsert_rows"], expected_revision=expected
                )
            except Exception:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=[],
                    old_blocks={},
                )
                raise

        _ca.set_config_db(store, revision)
        _ca.invalidate_config_caches()
        logger.info(
            "OpenAPI import applied from %s: rev=%d, tools +%s/republish %s, "
            "unmapped=%d, backup=%s",
            upstream,
            revision,
            sorted(set(added) - republish),
            sorted(republish),
            len(prep["unmapped"]),
            backup_path,
        )
        return {
            "changed": True,
            "revision": revision,
            "upstream": upstream,
            "summary": plan.summary(),
            "changes": [c.to_json() for c in plan.changes],
            "unmapped": [u.to_json() for u in prep["unmapped"]],
            "removed": list(plan.removed),
            "selected": selected,
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in prep["lint_warnings"]
            ],
            "tools_added": sorted(set(added) - republish),
            "tools_removed": sorted(set(removed) - republish),
            "tools_republished": sorted(republish),
            "upstreams_attached": [],
            "upstreams_reconnected": [],
            "upstreams_detached": [],
            "warnings": [],
            "backup_path": str(backup_path),
            "yaml_fragment": prep["yaml_fragment"],
        }
