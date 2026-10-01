"""HotToggleMixin — 上游启用/禁用热开关与工具发布差集收敛。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
所有内部步骤要求调用方持 ``_upstream_toggle_lock``; ``register_tool`` 用
模块限定形式 ``registry.register_tool`` 以便 monkeypatch 命中本模块 binding。
"""
import logging
from contextlib import contextmanager

from ..api.omni_client import OmniClient
from ..core import registry

logger = logging.getLogger(__name__)


class HotToggleMixin:
    """热开关方法簇 (GatewayServer 组装时混入)。"""

    async def set_upstream_enabled(self, name: str, enabled: bool) -> dict:
        """热切换单个上游启用态, 实时增删对外 /mcp 工具并连接/断开 client。

        - 禁用: 先把该上游参与的、失去全部启用 backer 的工具从 /mcp 摘除,
          再从 self.upstreams pop 并 stop() —— 先摘工具后断连, 杜绝新请求
          落到正在关闭的 client。
        - 启用: 先构造+启动 client 登记进 self.upstreams, 再把新获得启用
          backer 的工具发布到 /mcp。发布只取决于 enabled 而非连接健康,
          与启动时"配了但连不上工具仍注册"同态; connected=False 由前端提示。
        - 多 backer 工具只在全部 backer 禁用时才隐藏 (filter_specs_by_enabled)。
        - 单工具发布/摘除失败不中断整批: 错误收集后继续, 末尾抛 RuntimeError
          (yaml 已落盘, HTTP 层返 500 带 version); 下次同态点击走 reconcile
          自愈补完, 不再被 no-op 短路。

        持久化 (config.db overlay) 由调用方 console_api 先完成, 本方法只改运行态
        self.config (与 console_api 共享同一 dict) 与已发布工具集。
        """
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        async with self._upstream_toggle_lock:
            # H3: cfg 必须锁内从 live config 现读。锁外捕获的 cfg 可能在等锁期间
            # 被 apply_upstream_edit/出厂升级整块替换成孤儿 dict —— 在孤儿上翻
            # enabled 会返回 200 但 live 真值未变, 且按旧连接块建 client。
            upstreams_cfg = self.config.get("upstreams") or {}
            if name not in upstreams_cfg:
                raise KeyError(f"Unknown upstream: {name}")
            cfg = upstreams_cfg[name]

            current = bool(cfg.get("enabled", False))
            if current == enabled:
                # 状态已一致: 常规零动作 no-op; 同时承担上次部分失败后的运行态自愈
                return await self._reconcile_upstream_locked(name, cfg, enabled)

            cfg["enabled"] = bool(enabled)
            if enabled:
                live_block = self._resolve_live_block_locked(name, cfg)
                return await self._enable_upstream_locked(name, live_block)
            return await self._disable_upstream_locked(name)

    def _resolve_live_block_locked(self, name: str, cfg: dict) -> dict:
        """把 live 块的 ${VAR} 占位解析为实际值 (启用上游时)。

        库内事实源永远保留占位; 运行态与启动链一致使用解析值, 因此
        /api/config 读到的与开机时相同, 后续 reconcile 不会再看到占位。
        """
        from ..utils.config_loader import resolve_env_vars

        resolved, missing_env = resolve_env_vars(cfg, strict=False)
        if missing_env:
            raise ValueError(
                f"upstream {name!r} env var(s) not set in the gateway "
                f"environment: {', '.join(missing_env)}"
            )
        self.config.setdefault("upstreams", {})[name] = resolved
        return resolved

    async def set_disable_dangerous(self, disabled: bool) -> dict:
        """热切换 dangerous kill switch, 实时批量摘除/补回危险工具。

        - 封禁: 把已发布的 dangerous 工具全部从 provider 摘除 (不断任何
          client; 非 dangerous 工具不受影响);
        - 解封: 只补回当前本就可发布 (有启用 backer、非 hidden) 的 dangerous
          工具, 判定全部经 ``_publishable_specs``, 不会连带放出被其他理由
          隐藏的工具;
        - 单工具失败收集后继续, 末尾抛 RuntimeError; 持久化由 console_api
          先完成, 重启必然收敛到目标态。
        """
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        async with self._upstream_toggle_lock:
            current = bool(self.config.get("disable_dangerous", False))
            if current == bool(disabled):
                return {
                    "disabled": current,
                    "tools_added": [],
                    "tools_removed": [],
                    "changed": False,
                }

            self.config["disable_dangerous"] = bool(disabled)
            dangerous_names = {
                s["name"] for s in self.tool_specs if s.get("dangerous")
            }
            with self._toggle_errors("disable-dangerous") as errors:
                if disabled:
                    to_remove = dangerous_names & self._published_tools
                    tools_removed = self._unpublish_tools_locked(to_remove, errors)
                    tools_added: list[str] = []
                else:
                    wanted = {s["name"] for s in self._publishable_specs(self.tool_specs)}
                    to_add = (wanted & dangerous_names) - self._published_tools
                    tools_added = self._publish_tools_locked(to_add, errors)
                    tools_removed = []
            logger.info(
                "disable_dangerous=%s: dangerous tools added=%d removed=%d",
                disabled,
                len(tools_added),
                len(tools_removed),
            )
            return {
                "disabled": bool(disabled),
                "tools_added": tools_added,
                "tools_removed": tools_removed,
                "changed": True,
            }

    # ------------------------------------------------------------------
    # 热开关内部步骤 (调用方须持 _upstream_toggle_lock)
    # ------------------------------------------------------------------

    def _publishable_specs(self, specs: list[dict] | None) -> list[dict]:
        """当前真正应发布到对外 /mcp 的 spec: env 门控后再过

        ``filter_specs_by_enabled`` (多 backer: 至少一个启用才保留) 与
        ``publish_mode=hidden`` 下架集, 最后按 ``disable_dangerous`` 全局
        kill switch 滤掉 ``dangerous: true`` 工具。启动注册、上游热开关
        差集、单工具热编辑三处共用同一判定, 保证被隐藏/被封禁的工具不会
        从任何路径漏回。
        """
        from ..core.tool_loader import filter_specs_by_enabled

        if not specs:
            return []
        # getattr 兜底: __new__ 旁路 __init__ 的热开关测试不构造该属性。
        hidden = getattr(self, "_db_hidden_tools", None) or set()
        dangerous_blocked = bool(self.config.get("disable_dangerous", False))
        return [
            spec
            for spec in filter_specs_by_enabled(specs, self.config)
            if spec.get("name") not in hidden
            and not (dangerous_blocked and spec.get("dangerous"))
        ]

    def _toggle_tool_diff_locked(self, name: str) -> tuple[set[str], set[str]]:
        """计算单个上游背书工具的发布差集 → (to_add, to_remove)。

        全局期望集经 filter_specs_by_enabled (多 backer 语义), 再与
        backing_upstreams 含 name 的工具取交 —— 自愈重放时只碰本上游的
        工具, 不顺手摘补其他上游的漂移。
        """
        from ..core.tool_loader import backing_upstreams

        wanted = {s["name"] for s in self._publishable_specs(self.tool_specs)}
        affected: set[str] = set()
        for spec in self.tool_specs:
            routing_key = spec.get("routing") or spec["name"]
            if name in backing_upstreams(routing_key, self.config):
                affected.add(spec["name"])
        to_add = (wanted & affected) - self._published_tools
        to_remove = (self._published_tools & affected) - wanted
        return to_add, to_remove

    def _publish_tools_locked(self, tool_names: set[str], errors: list[str]) -> list[str]:
        """逐个发布, 单工具失败收集进 errors 后继续 (不中断整批)。"""
        spec_by_name = {s["name"]: s for s in self.tool_specs}
        added: list[str] = []
        for tool_name in sorted(tool_names):
            spec = spec_by_name.get(tool_name)
            if spec is None:
                continue
            try:
                registry.register_tool(self, spec)
            except Exception as e:  # noqa: BLE001 — 收集后继续, 末尾统一抛
                errors.append(f"publish {tool_name}: {type(e).__name__}: {e}")
                logger.exception("toggle: publish tool %r failed", tool_name)
                continue
            self._published_tools.add(tool_name)
            added.append(tool_name)
        return added

    def _unpublish_tools_locked(
        self, tool_names: set[str], errors: list[str]
    ) -> list[str]:
        """逐个摘除, KeyError (provider 已无此工具) 幂等视为成功;
        其他异常收集后继续, 且不把该工具移出 _published_tools (集合仍反映
        provider 真实状态, 下次 reconcile 重试)。"""
        removed: list[str] = []
        for tool_name in sorted(tool_names):
            try:
                self.mcp.local_provider.remove_tool(tool_name)
            except KeyError:
                logger.warning(
                    "toggle: tool %r not on local provider (idempotent)", tool_name
                )
            except Exception as e:  # noqa: BLE001
                errors.append(f"unpublish {tool_name}: {type(e).__name__}: {e}")
                logger.exception("toggle: remove tool %r failed", tool_name)
                continue
            self._published_tools.discard(tool_name)
            removed.append(tool_name)
        return removed

    @contextmanager
    def _toggle_errors(self, name: str):
        """上下文管理器：进入 yield 一个 ``errors: list[str]``；退出时若非空则 raise。

        替换 ``errors: list[str] = []`` + ``self._raise_if_toggle_errors(name, errors)``
        的样板。block 内失败时把消息 ``errors.append(...)`` 即可, 末尾 raise
        统一处理。多 errors 源 (如 reconcile 的 add/remove) 也走这个上下文,
        block 起始 ``errors.extend(add_errors)`` + ``errors.extend(remove_errors)``
        即可, 退出时一并 raise。
        """
        errors: list[str] = []
        try:
            yield errors
        finally:
            if errors:
                detail = "; ".join(errors[:5])
                if len(errors) > 5:
                    detail += f"; …(+{len(errors) - 5} more)"
                raise RuntimeError(
                    f"runtime toggle {name!r} partial failure: {detail}"
                )

    @staticmethod
    def _raise_if_toggle_errors(name: str, errors: list[str]) -> None:
        """历史 API 兼容: 测试 (test_startup_audit_round9_batch_a 等) 用
        monkeypatch.setattr 替换本方法为 lambda/None 来压制错误, 或期望它以
        旧 raise RuntimeError 语义抛出。保留作为 _toggle_errors 的同步等价
        入口, 内部委托同一错误格式化逻辑。
        """
        if errors:
            detail = "; ".join(errors[:5])
            if len(errors) > 5:
                detail += f"; …(+{len(errors) - 5} more)"
            raise RuntimeError(
                f"runtime toggle {name!r} partial failure: {detail}"
            )

    async def _attach_upstream_client_locked(self, name: str, cfg: dict):
        """用已解析 live 块构造+启动 client 并切换 console_api omni 指针。

        返回 client, connected。调用方负责先解析 ``${VAR}`` —— 热开关路径经
        ``_resolve_live_block_locked``、升级路径经 ``assemble_runtime_candidate``
        (其内部跑 resolve_env_vars); 这里不再重复解析。
        """
        client = self._construct_upstream_client(name, cfg)
        connected = await self._start_upstream_client(name, cfg, client)
        if isinstance(client, OmniClient):
            from ..utils import console_api as _ca

            _ca.set_omni_client(client)
        return client, connected

    async def _detach_upstream_client_locked(self, name: str) -> None:
        """pop + stop client, 并清 console_api 的 omni 指针。先摘工具后断连。

        _stop_one_upstream 内部已吞掉超时/异常, client 一律按已脱离处理。
        在途请求不做 drain: HTTP client 关池错误转成 ToolResult(success=False)
        由 Router 回落下一 backer (人工低频管理动作, 可接受)。
        """
        # M2 (round 11): 先取消该上游在途的 late-start 自愈任务, 再 pop/stop;
        # 否则任务可能在 client 已停后才把它重新拉起 (新 factory/新健康循环)。
        await self._cancel_late_starts(name)
        client = self.upstreams.pop(name, None)
        if client is not None:
            await self._stop_one_upstream(name, client)
        # console_api 单独持有的 OmniClient 指针也要清, 否则禁用后
        # /api/omni* (含 POST sync) 仍打在已停 client 上, 与启动时
        # enabled:false 注入 None 的语义对齐。
        if isinstance(client, OmniClient):
            from ..utils import console_api as _ca

            _ca.set_omni_client(None)

    async def _enable_upstream_locked(self, name: str, cfg: dict) -> dict:
        with self._toggle_errors(name) as errors:
            _, connected = await self._attach_upstream_client_locked(name, cfg)
            to_add, _ = self._toggle_tool_diff_locked(name)
            tools_added = self._publish_tools_locked(to_add, errors)
        logger.info(
            "Upstream %s enabled: tools_added=%d connected=%s",
            name,
            len(tools_added),
            connected,
        )
        return {
            "name": name,
            "enabled": True,
            "connected": connected,
            "tools_added": tools_added,
            "tools_removed": [],
            "changed": True,
        }

    async def _disable_upstream_locked(self, name: str) -> dict:
        with self._toggle_errors(name) as errors:
            _, to_remove = self._toggle_tool_diff_locked(name)
            tools_removed = self._unpublish_tools_locked(to_remove, errors)
        # 有工具没摘下来: raise 先于 detach, 保留 client 让残存发布的工具仍可
        # 服务; 下次同态点击 (reconcile) 重试摘除, 避免半残废状态。
        await self._detach_upstream_client_locked(name)
        logger.info(
            "Upstream %s disabled: tools_removed=%d", name, len(tools_removed)
        )
        return {
            "name": name,
            "enabled": False,
            "connected": False,
            "tools_added": [],
            "tools_removed": tools_removed,
            "changed": True,
        }

    async def _reconcile_upstream_locked(
        self, name: str, cfg: dict, enabled: bool
    ) -> dict:
        """请求态与 cfg 已一致时的收敛入口。

        健康态是零动作纯 no-op (changed=False); 若上次切换在中途失败
        (HTTP 500/yaml 已落盘), 这里补做漂移: 缺发布的工具补发、应消失的
        工具补摘、缺失/残留的 client 补连/补断, 让前端的同态重试真正自愈。
        """
        add_errors: list[str] = []
        remove_errors: list[str] = []
        to_add, to_remove = self._toggle_tool_diff_locked(name)
        existing = self.upstreams.get(name)
        client_changed = False

        if enabled:
            if existing is None:
                live_block = self._resolve_live_block_locked(name, cfg)
                existing, _ = await self._attach_upstream_client_locked(
                    name, live_block
                )
                client_changed = True
            connected = bool(existing is not None and existing.is_available)
            tools_added = self._publish_tools_locked(to_add, add_errors)
            tools_removed = self._unpublish_tools_locked(to_remove, remove_errors)
        else:
            # to_add: 多 backer 工具失去本上游后仍可经其他启用 backer 发布,
            # 属合法补发; to_remove: 失去全部 backer 的工具补摘。
            tools_added = self._publish_tools_locked(to_add, add_errors)
            tools_removed = self._unpublish_tools_locked(to_remove, remove_errors)
            connected = False

        # detach 只由 unpublish 失败门控: 不相关的补发失败不得永久卡住
        # 禁用 client 的摘除 (旧实现共用一个 errors 列表会误挡)。
        if not enabled and existing is not None and not remove_errors:
            await self._detach_upstream_client_locked(name)
            client_changed = True

        # 双 errors 合并 raise: 复用 _toggle_errors 的格式化逻辑 (双 list 合并
        # 后由 contextmanager 退出时统一抛, 不重复内联 detail + 截断模板)。
        with self._toggle_errors(name) as errors:
            errors.extend(add_errors)
            errors.extend(remove_errors)
        healed = bool(tools_added or tools_removed or client_changed)
        if healed:
            logger.info(
                "Upstream %s reconcile: added=%d removed=%d client_changed=%s",
                name,
                len(tools_added),
                len(tools_removed),
                client_changed,
            )
        return {
            "name": name,
            "enabled": enabled,
            "connected": connected,
            "tools_added": tools_added,
            "tools_removed": tools_removed,
            "changed": False if not healed else True,
        }
