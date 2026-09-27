"""ConfigEditsMixin — 单工具/上游实体热编辑 (lint 门 → 热重注册 → 失败回滚)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用一律经 ``self``; ``register_tool`` 用模块限定形式
``registry.register_tool`` 以便 monkeypatch 命中本模块 binding。
"""
import copy
import json
import logging

from ..api.omni_client import OmniClient
from ..core import registry
from ..storage.config_store import ConfigStore

from ._helpers import RESERVED_TOOL_NAMES, switch_runtime_locked

logger = logging.getLogger(__name__)


class ConfigEditsMixin:
    """工具/上游热编辑方法簇 (GatewayServer 组装时混入)。"""

    # ------------------------------------------------------------------
    # P3: 单工具实体热编辑 (config.db → lint 门 → 热重注册 → 失败回滚)
    # ------------------------------------------------------------------

    async def apply_tool_edit(
        self,
        name: str,
        patch: dict,
        *,
        confirm_dangerous: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """编辑一个工具实体并立即热生效到对外 /mcp。

        流程 (设计 §6, docs/design/2026-09-18-sqlite-config-full-database.md):

        1. 字段白名单/形态校验 + 描述层/契约层分层: 触及 name/routing/
           requires_env/mapping 必须 ``confirm_dangerous=True``;
        2. 本地构造候选实体 → 装配出候选运行时 config (与启动同一条
           assemble → bootstrap → resolve env 链) → ``lint_config`` 全量
           静态校验, 有 error 抛 :class:`LintRejected`, 运行态/库均不动;
        3. toggle 锁内先切运行态 (config/tool_specs/router/hidden 集),
           再做 provider 换工具 (remove 旧名 → register 新 spec);
        4. 注册成功后写库 (乐观锁); 注册或写库任一步失败, 运行态与 provider
           全部回滚到编辑前, 异常原样抛给 HTTP 层;
        5. publish_mode=hidden / requires_env 门控 / 全 backer 禁用的候选
           只摘除不注册 (行保留, 可恢复)。

        Returns:
            ``{changed, name, revision, published, previously_published,
            contract_fields}``; 净效果无变化时 no-op 短路 (不含
            contract_fields 键), 不 bump revision、不碰 provider。
        """
        from ..core.config_lint import ERROR as LINT_ERROR
        from ..core.config_lint import LintRejected, lint_config
        from ..core.entity_edit import (
            DangerousConfirmationRequired,
            apply_tool_patch,
            contract_fields_in_patch,
        )
        from ..utils import console_api as _ca

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "tool editing requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        entity = await store.get_tool(name)
        if entity is None:
            raise KeyError(name)

        contract_fields = sorted(contract_fields_in_patch(patch))
        if contract_fields and not confirm_dangerous:
            raise DangerousConfirmationRequired(contract_fields)

        # bundle 与其 revision 在同一快照读出 (plan 时刻): 即使调用方省略
        # expected_revision, 也以该 revision 为乐观锁基准 — plan 之后若有别的
        # 写入落库, 锁内落库必撞 RevisionConflict 并整笔回滚, 杜绝"锁外旧
        # bundle 构造候选 → 覆盖并发编辑" (H5)。
        bundle, plan_revision = await store.read_bundle()
        existing_names = {row["name"] for row in bundle["tools"]}
        updated = apply_tool_patch(entity, patch, existing_names=existing_names)
        old_name, new_name = name, updated["name"]
        if new_name != old_name and new_name in RESERVED_TOOL_NAMES:
            raise ValueError(
                f"tool name {new_name!r} is reserved for gateway built-in tools"
            )

        if ConfigStore._tool_entity_unchanged(entity, updated):
            currently_published = old_name in self._published_tools
            return {
                "changed": False,
                "name": old_name,
                "revision": await store.revision(),
                "published": currently_published,
                "previously_published": currently_published,
            }

        candidate, candidate_specs, candidate_hidden = self._build_tool_edit_candidate(
            bundle, old_name, updated
        )
        lint_errors = [
            issue
            for issue in lint_config(candidate, candidate_specs)
            if issue.severity == LINT_ERROR
        ]
        if lint_errors:
            raise LintRejected(lint_errors)

        async with self._upstream_toggle_lock:
            # 运行态快照 (全是引用替换, 无需深拷贝: 候选 config 是独立新 dict,
            # 回滚只是把引用换回来)。
            old_config = self.config
            old_specs = list(self.tool_specs)
            old_hidden = set(self._db_hidden_tools)
            previously_published = old_name in self._published_tools

            # 1) 切运行态: live config / env 门控 spec / hidden 集 / router。
            switch_runtime_locked(
                self, candidate, candidate_specs, candidate_hidden
            )

            # 2) provider 换工具: 先摘旧名 (KeyError 幂等), 再注册候选。
            if previously_published:
                try:
                    self.mcp.local_provider.remove_tool(old_name)
                except KeyError:
                    logger.warning(
                        "tool edit: %r not on local provider (idempotent)",
                        old_name,
                    )
                self._published_tools.discard(old_name)

            new_spec = next(
                (s for s in candidate_specs if s["name"] == new_name), None
            )
            will_publish = new_name in {
                s["name"] for s in self._publishable_specs(candidate_specs)
            }
            published = False
            if new_spec is not None and will_publish:
                try:
                    registry.register_tool(self, new_spec)
                except Exception as e:
                    self._rollback_tool_edit_locked(
                        old_config=old_config,
                        old_specs=old_specs,
                        old_hidden=old_hidden,
                        old_name=old_name,
                        new_name=new_name,
                        candidate_published=False,
                        previously_published=previously_published,
                    )
                    raise RuntimeError(f"re-register failed: {e}") from e
                self._published_tools.add(new_name)
                published = True

            # 3) 写库 (乐观锁)。失败 → 运行态 + provider 全量回滚。
            # expected 缺省钉住 plan 时刻 revision (非锁内重读, 锁内重读会
            # 让校验恒通过, 并发编辑被旧候选物理覆盖)。
            expected = (
                plan_revision
                if expected_revision is None
                else expected_revision
            )
            try:
                final_name, revision = await store.update_tool(
                    old_name, patch, expected_revision=expected
                )
            except Exception:
                self._rollback_tool_edit_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    old_name=old_name,
                    new_name=new_name,
                    candidate_published=published,
                    previously_published=previously_published,
                )
                raise

            # 4) 成功: console_api 共享同一 store 对象, 刷 revision 与缓存。
            _ca.set_config_db(store, revision)
            _ca.invalidate_config_caches()
            logger.info(
                "Tool %r edited (final=%r, rev=%d, published=%s, contract=%s)",
                old_name,
                final_name,
                revision,
                published,
                contract_fields or "-",
            )
            return {
                "changed": True,
                "name": final_name,
                "revision": revision,
                "published": published,
                "previously_published": previously_published,
                "contract_fields": contract_fields,
            }

    def _build_tool_edit_candidate(
        self, bundle: dict, old_name: str, updated: dict
    ) -> tuple[dict, list[dict], set[str]]:
        """把单个候选实体行换入 bundle, 走与启动同构的装配链产出候选运行态。

        返回 ``(resolved_config, env 门控后 specs, hidden 工具名集)``。
        不做 IO 写; bootstrap 仅在正常启动路径 (有 config_path) 时合并,
        __new__ 旁路 __init__ 的测试场景自动跳过。
        """
        from ..core.config_db import assemble_runtime_candidate

        row_keys = (
            "name",
            "routing",
            "description",
            "cache_ttl_key",
            "requires_env",
            "publish_mode",
            "mapping",
            "spec",
            "source_file",
            "origin",
        )
        cand_bundle = copy.deepcopy(bundle)
        for idx, row in enumerate(cand_bundle.get("tools") or []):
            if row["name"] == old_name:
                cand_bundle["tools"][idx] = {
                    key: updated.get(key) for key in row_keys
                }
                break

        # 装配链 (entities → assemble → bootstrap → env 解析 → env 门控 →
        # hidden 集) 与启动/升级共用同一实现, 避免两处漂移。
        return assemble_runtime_candidate(
            cand_bundle,
            config_path=getattr(self, "config_path", None),
            strict_env=bool(getattr(self, "_strict_env", False)),
        )

    def _rollback_tool_edit_locked(
        self,
        *,
        old_config: dict,
        old_specs: list[dict],
        old_hidden: set[str],
        old_name: str,
        new_name: str,
        candidate_published: bool,
        previously_published: bool,
    ) -> None:
        """热编辑失败回滚: provider 摘候选 → 运行态复位 → 补发旧工具。

        补发旧工具若再失败只记录不抛 (原异常正在传播), 集合按 provider
        真实状态保留, 后续上游 reconcile 可自愈。
        """
        from ..utils import console_api as _ca

        if candidate_published:
            try:
                self.mcp.local_provider.remove_tool(new_name)
            except KeyError:
                pass
            self._published_tools.discard(new_name)

        switch_runtime_locked(self, old_config, old_specs, old_hidden)

        if previously_published:
            old_spec = next(
                (s for s in old_specs if s["name"] == old_name), None
            )
            if old_spec is not None:
                try:
                    registry.register_tool(self, old_spec)
                    self._published_tools.add(old_name)
                except Exception:
                    logger.exception(
                        "tool edit rollback: failed to re-register %r", old_name
                    )
                    self._published_tools.discard(old_name)

    async def apply_upstream_edit(
        self,
        name: str,
        config: dict,
        *,
        type: str | None = None,
        confirm_dangerous: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """整体替换上游连接块 (可选改类型), 热生效。

        连接块（base_url / api_key / servers / 超时等）是上游建连契约,
        一律按契约层处理: 必须 ``confirm_dangerous=True``。明文密钥字段
        (非 ``${VAR}`` 占位) 在触碰运行态前 fail-closed 拒绝。

        顺序 (全部破坏性动作集中在 toggle 锁内):

        1. 锁外静态门: 形态校验 → 明文密钥扫描 → ``${VAR}`` 解析 (仅启用上游
           要求变量齐备; 库内永远只存占位) → 候选 config 全量 lint;
        2. 锁内, 启用且有变更: **先构造新 client** (纯构造失败 = 零损伤),
           启动登记新 client 后再停旧 client (先接后断, 无"无 client"空窗),
           换 live 块, 差集自愈重放;
        3. **落库最后做**: RevisionConflict 或发布失败 → 运行态整体回滚
           (摘新 client、复原 live 块、按旧块重建等价 client);
        4. 禁用上游: 只换 live 块 (保留 ``${VAR}`` 占位) + 落库, 不碰 client。

        建连本身失败 (超时/不可用) 按启动语义登记不可用 client、工具仍发布,
        不回滚 (与开机"配了但连不上"同态, connected=False 提示)。

        Returns:
            ``{changed, name, revision, enabled, connected, contract_fields}``。
        """
        from ..core.config_lint import ERROR as LINT_ERROR
        from ..core.config_lint import LintRejected, lint_config
        from ..core.entity_edit import (
            DangerousConfirmationRequired,
            validate_upstream_config,
        )
        from ..core.template_normalize import find_plaintext_secrets_in
        from ..utils import console_api as _ca
        from ..utils.config_loader import resolve_env_vars

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "upstream editing requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        bundle, plan_revision = await store.read_bundle()
        entity = next(
            (u for u in bundle.get("upstreams") or [] if u["name"] == name), None
        )
        if entity is None:
            raise KeyError(name)

        contract_fields = ["upstream.config"]
        new_type = type or entity["type"]
        if type is not None and type != entity["type"]:
            contract_fields.append("upstream.type")
        if not confirm_dangerous:
            raise DangerousConfirmationRequired(contract_fields)

        # 边界形态 + 明文密钥: 任何运行态/库变更之前先拒 (存储层还会再守)。
        clean_config = validate_upstream_config(config)
        leaked = find_plaintext_secrets_in(clean_config, f"upstreams.{name}")
        if leaked:
            raise ValueError(
                "Refusing to write plaintext secret field(s): " + ", ".join(leaked)
            )

        enabled = bool(entity["enabled"])
        changed = new_type != entity["type"] or self._canonical_config(
            entity.get("config") or {}
        ) != self._canonical_config(clean_config)

        # live 块形态; ${VAR} 只在运行态解析 —— 库内事实源永远保留占位 (H2)。
        candidate_block = {
            **copy.deepcopy(clean_config),
            "enabled": enabled,
            "type": new_type,
        }
        resolved_block, missing_env = resolve_env_vars(candidate_block, strict=False)
        live_block = resolved_block if enabled else candidate_block

        # lint 门: 只替换本块的候选完整 config 跑全量静态校验 (M7)。
        candidate_config = copy.deepcopy(self.config)
        candidate_config.setdefault("upstreams", {})[name] = live_block
        lint_errors = [
            issue
            for issue in lint_config(candidate_config, list(self.tool_specs))
            if issue.severity == LINT_ERROR
        ]
        if lint_errors:
            raise LintRejected(lint_errors)

        if not changed:
            # 仍然过一遍乐观锁: 过期 revision 的 no-op 也要 409; 等价写入不 bump、
            # 不翻 origin、不重连。
            revision = await store.update_upstream_config(
                name,
                clean_config,
                type=type,
                expected_revision=(
                    plan_revision if expected_revision is None else expected_revision
                ),
            )
            _ca.set_config_db(store, revision)
            _ca.invalidate_config_caches()
            current_client = self.upstreams.get(name)
            connected = bool(
                current_client is not None
                and getattr(current_client, "is_available", False)
            )
            return {
                "changed": False,
                "name": name,
                "revision": revision,
                "enabled": enabled,
                "connected": connected,
                "contract_fields": contract_fields,
            }

        # 启用上游但新块引用的环境变量缺失 = 新 client 必败 (如确定性 401),
        # 在任何状态变更前拒绝 (H2)。
        if enabled and missing_env:
            raise ValueError(
                "upstream config references env var(s) not set in the gateway "
                f"environment: {', '.join(missing_env)}; set them before applying "
                "(the stored block keeps ${VAR} placeholders)"
            )

        expected = plan_revision if expected_revision is None else expected_revision
        connected = False
        async with self._upstream_toggle_lock:
            old_live_block = (self.config.get("upstreams") or {}).get(name)
            old_client = self.upstreams.get(name)
            new_client = None
            if enabled:
                # 纯构造先于一切状态变更: 构造异常时旧 client/live/库全部不动。
                new_client = self._construct_upstream_client(name, resolved_block)

            self.config.setdefault("upstreams", {})[name] = live_block

            if enabled:
                old_client_stopped = False
                try:
                    # 先接后断: 新 client 启动并登记到同名槽位后再停旧 client。
                    connected = await self._start_upstream_client(
                        name, resolved_block, new_client
                    )
                    if isinstance(new_client, OmniClient):
                        _ca.set_omni_client(new_client)
                    elif isinstance(old_client, OmniClient):
                        _ca.set_omni_client(None)
                    if old_client is not None and old_client is not new_client:
                        await self._stop_one_upstream(name, old_client)
                        # M2 (round 9 audit): 标记旧 client 已停, 供回滚决定是
                        # 复用 old 还是重建 restored (见 _rollback_upstream_edit_locked)。
                        old_client_stopped = True
                    # 新连接块可能引入新 backer 可发布的工具, 重放一次差集自愈。
                    errors: list[str] = []
                    to_add, _ = self._toggle_tool_diff_locked(name)
                    self._publish_tools_locked(to_add, errors)
                    self._raise_if_toggle_errors(name, errors)
                except BaseException:  # noqa: BLE001 - 含 CancelledError, 回滚后重抛
                    await self._rollback_upstream_edit_locked(
                        name=name,
                        old_live_block=old_live_block,
                        old_client=old_client,
                        new_client=new_client,
                        old_client_stopped=old_client_stopped,
                    )
                    raise
            else:
                connected = False

            try:
                revision = await store.update_upstream_config(
                    name,
                    clean_config,
                    type=type,
                    expected_revision=expected,
                )
            except BaseException:  # noqa: BLE001 - 落库失败 (含 409), 运行态回滚
                # 落库失败路径: 成功段已把 old 停掉 (old_client_stopped=True 由
                # 成功路径设, 失败点之后的 flag 在此 try 外不可见, 用成功语义:
                # 若走到落库, 前面重连成功则 old 已停)。
                await self._rollback_upstream_edit_locked(
                    name=name,
                    old_live_block=old_live_block,
                    old_client=old_client,
                    new_client=new_client,
                    old_client_stopped=enabled,
                )
                raise

            _ca.set_config_db(store, revision)
            _ca.invalidate_config_caches()

        logger.info(
            "Upstream %r connection block edited (rev=%d, changed=%s, enabled=%s)",
            name,
            revision,
            changed,
            enabled,
        )
        return {
            "changed": True,
            "name": name,
            "revision": revision,
            "enabled": enabled,
            "connected": connected,
            "contract_fields": contract_fields,
        }

    async def _rollback_upstream_edit_locked(
        self,
        *,
        name: str,
        old_live_block: dict | None,
        old_client,
        new_client,
        old_client_stopped: bool = False,
    ) -> None:
        """apply_upstream_edit 运行态回滚 (调用方持 toggle 锁)。

        摘除已登记的新 client → live 块复原 → 旧 client 处置:
        - 旧 client **未被停** (cancel 发生在 start new 阶段, old 仍活): 重新
          登记回槽, 不另建 restored —— 否则 old 成无引用孤儿 + restored 双活
          (M2, round 9 audit);
        - 旧 client **已被停** (重连成功后落库/发布失败): 按旧 live 块重建等价
          client 接替 (old 已死, 不能复用)。
        复原失败只记录 (原异常正在传播), 漂移留待 reconcile 自愈。
        """
        from ..utils import console_api as _ca

        if new_client is not None and self.upstreams.get(name) is new_client:
            self.upstreams.pop(name, None)
            await self._stop_one_upstream(name, new_client)

        if old_live_block is not None:
            self.config.setdefault("upstreams", {})[name] = old_live_block

        if old_client is None or old_client is new_client:
            return

        if not old_client_stopped:
            # M2: old 仍活 (其 stop 从未执行/完成) → 重新登记, 杜绝孤儿 + 重复构造。
            if self.upstreams.get(name) is not old_client:
                self.upstreams[name] = old_client
            if isinstance(old_client, OmniClient):
                _ca.set_omni_client(old_client)
            logger.warning(
                "upstream %r edit rolled back; old client still alive, re-registered",
                name,
            )
            return

        # old 已停 → 按旧 live 块重建等价 client 接替。
        try:
            restored = self._construct_upstream_client(name, old_live_block)
            await self._start_upstream_client(
                name, old_live_block, restored
            )
            if isinstance(restored, OmniClient):
                _ca.set_omni_client(restored)
            logger.warning(
                "upstream %r edit rolled back after runtime failure; "
                "client rebuilt from previous block",
                name,
            )
        except Exception:  # noqa: BLE001 - 复原失败不掩盖原异常
            logger.exception(
                "upstream %r edit rollback: previous client restore failed",
                name,
            )

    @staticmethod
    def _canonical_config(value: object) -> str:
        """连接块/spec 等价比较的规范 JSON (env 已解析, 纯本地比较用)。"""
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # P5: 全局池参数编辑 (写 config.db, 需重启生效; 不触 Semaphore/ThreadPool)
    # ------------------------------------------------------------------

    # 池字段 → 对应上游 + cfg 子键
    _POOL_FIELD_TO_UPSTREAM_KEY: dict[str, tuple[str, str]] = {
        "mootdx2_reader_limit": ("mootdx2", "reader_limit"),
        "mootdx2_worker_threads": ("mootdx2", "worker_threads"),
        "tdx_quant_worker_threads": ("tdx_quant", "worker_threads"),
    }

    async def apply_pool_config_edit(
        self,
        fields: dict,
        *,
        expected_revision: int | None = None,
    ) -> dict:
        """写池容量/Reader 闸门到 config.db (mootdx2 + tdx_quant 两个上游实体行)。

        三个字段 (mootdx2_reader_limit / mootdx2_worker_threads /
        tdx_quant_worker_threads) 必须全部为 int ≥ 1, 否则 ``ValueError``
        → handler 400; 任一上游缺失 → ``KeyError`` → 404; revision 过期
        → ``RevisionConflict`` → 409。

        落库后**不**重排 Semaphore/ThreadPool (运行时不支持扩缩), 用户
        主动重启 gateway 才生效。返回 ``persisted / running / pending_restart``
        三段, 客户端无需再发 GET 即可渲染"需重启" badge。

        partial success: write #1 (mootdx2) 落库后 write #2 (tdx_quant) 撞
        409 → 整请求 409, write #1 已落库; 与 apply_upstream_edit 同语义。
        """
        from ..utils import console_api as _ca

        # 1. gate: yaml 模式不可写
        store = getattr(self, "config_store", None)
        if store is None:
            raise RuntimeError(
                "config.db entity source required for pool config edit"
            )

        # 2. 形态/范围校验
        validated: dict[str, int] = {}
        for k in self._POOL_FIELD_TO_UPSTREAM_KEY:
            if k not in fields:
                raise ValueError(f"missing pool field: {k!r}")
            v = fields[k]
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(
                    f"pool field {k!r} must be int (got {type(v).__name__}: {v!r})"
                )
            if v < 1:
                raise ValueError(f"pool field {k!r} must be ≥ 1 (got {v})")
            validated[k] = v

        # 3. 读 bundle 一次性拿 plan_revision + 两个 upstream 实体行
        bundle = await store.read_bundle()
        current_rev = int(bundle.get("revision") or 0)
        if expected_revision is not None and expected_revision != current_rev:
            from ..storage.config_store import RevisionConflict
            raise RevisionConflict(expected_revision, current_rev)

        ups_by_name: dict[str, dict] = {}
        for u in bundle.get("upstreams", []) or []:
            ups_by_name[u.get("name")] = u
        # 缺失检查
        for fk, (upstream_name, _key) in self._POOL_FIELD_TO_UPSTREAM_KEY.items():
            if upstream_name not in ups_by_name:
                raise KeyError(upstream_name)

        # 4. shallow-merge: 保留现有 keys, 仅覆盖池字段
        merged_by_name: dict[str, dict] = {}
        for upstream_name in {n for n, _ in self._POOL_FIELD_TO_UPSTREAM_KEY.values()}:
            existing_cfg = dict(ups_by_name[upstream_name].get("config") or {})
            for fk, (uname, key) in self._POOL_FIELD_TO_UPSTREAM_KEY.items():
                if uname == upstream_name:
                    existing_cfg[key] = validated[fk]
            merged_by_name[upstream_name] = existing_cfg

        # 5. 顺序写两个 upstream 实体行 (共享 expected_revision)
        # update_upstream_config 自身有 no-op 短路 (new==current → return current)
        final_rev = current_rev
        for upstream_name, new_cfg in merged_by_name.items():
            new_rev = await store.update_upstream_config(
                upstream_name, new_cfg,
                type=None, expected_revision=current_rev,
            )
            final_rev = new_rev
            current_rev = new_rev  # 下一轮基于已 bump 的 rev

        # 6. 缓存失效 + console_api db revision 同步
        _ca.set_config_db(store, final_rev)
        _ca.invalidate_config_caches()

        # 7. diff persisted vs running, 构造 pending_restart
        running_snap = self.get_pool_config_snapshot() or {}
        running_vals = {
            "mootdx2_reader_limit": running_snap.get("mootdx2_reader_limit"),
            "mootdx2_worker_threads": running_snap.get("mootdx2_worker_threads"),
            "tdx_quant_worker_threads": running_snap.get("tdx_quant_worker_threads"),
        }
        pending_restart = {
            fk: (validated[fk] != running_vals[fk])
            for fk in self._POOL_FIELD_TO_UPSTREAM_KEY
        }
        changed = any(pending_restart.values())

        logger.info(
            "Pool config edited (rev=%d, changed=%s, pending_restart=%s)",
            final_rev, changed, pending_restart,
        )
        return {
            "changed": changed,
            "revision": final_rev,
            "persisted": {
                "mootdx2_reader_limit": validated["mootdx2_reader_limit"],
                "mootdx2_worker_threads": validated["mootdx2_worker_threads"],
                "tdx_quant_worker_threads": validated["tdx_quant_worker_threads"],
            },
            "running": {
                "mootdx2_reader_limit": running_vals["mootdx2_reader_limit"],
                "mootdx2_worker_threads": running_vals["mootdx2_worker_threads"],
                "tdx_quant_worker_threads": running_vals["tdx_quant_worker_threads"],
                "sources": dict(running_snap.get("sources") or {}),
            },
            "pending_restart": pending_restart,
        }
