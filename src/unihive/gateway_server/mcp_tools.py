"""McpToolsMixin — 工具 spec 加载、FastMCP 构造/注册与健康聚合。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用一律经 ``self`` (mixin 方法名彼此无交集, 由 facade 的
GatewayServer 多继承组装)。
"""
import logging
import os
import time

from fastmcp import FastMCP

from ..api.omni_client import OmniClient
from ..core import registry
from ._helpers import GATEWAY_VERSION

logger = logging.getLogger(__name__)


class McpToolsMixin:
    """工具加载/注册 + 上游健康摘要 (GatewayServer 的方法簇之一)。"""

    def _load_all_tools(self) -> list[dict]:
        """全量工具 spec (运行时 env 门控后)。

        db 模式: 实体装配时 293 条已在 config['tools'], 这里只补运行时
        requires_env 门控 (与 yaml 模式 load_all_tools 默认行为一致),
        不再回读任何 tools_*.yaml。
        yaml 模式: 委托 tool_loader 合并内联 + 生成文件。
        """
        from ..core.tool_loader import filter_specs_by_env, load_all_tools

        if self._uses_db_source():
            return filter_specs_by_env(list(self.config.get("tools") or []))
        return load_all_tools(self.config_path, self.config)

    def _uses_db_source(self) -> bool:
        """配置事实源是否为 config.db。__init__ 正常路径读实例属性;
        __new__ 旁路 __init__ 的测试回落环境变量 (autouse fixture 钉 yaml)。"""
        flag = getattr(self, "_db_mode", None)
        if flag is not None:
            return bool(flag)
        return os.environ.get("UNIHIVE_CONFIG_SOURCE", "db").strip().lower() != "yaml"

    async def _create_and_register_gateway_mcp(
        self, tool_specs: list[dict] | None = None
    ) -> FastMCP:
        """Single entry point for constructing the gateway FastMCP server.

        M2 (2026-09-07 audit) + 2026-09-08 Refactor 1: 三步（构造 + 注册工具 +
        capability filter 安装）全部委派给 ``src.core.mcp_factory.create_and_register_gateway_mcp``，
        防止未来 refactor 在 _do_initialize 之外构造裸 FastMCP 时漏掉 capability
        filter (HIGH #3, 2026-09-07 audit)。
        """
        from ..core.mcp_factory import create_and_register_gateway_mcp

        def _on_constructed(mcp: FastMCP) -> None:
            # version 显式传 GATEWAY_VERSION, 避免 FastMCP 默认 fallback 到框架
            # 版本 (4.0.3), 让 client 把 framework 升级误判成 server 升级。
            self.mcp = mcp
            self._register_tools(specs=tool_specs)

        return await create_and_register_gateway_mcp(
            tool_specs,
            version=GATEWAY_VERSION,
            on_constructed=_on_constructed,
        )

    def _register_tools(self, specs: list[dict]):
        # 特殊工具：手写（带特殊逻辑或网关内部）
        self._register_status_tools()

        if specs:
            # validate 对全量 spec 生效 —— 配置写错与上游是否启用无关,
            # 禁用上游不能把 routing/mapping 配置错误静默藏起来。
            strict = self.config.get("strict_registry", True)
            # Round-12 簇G: 启动门额外校验 mapping/chain 引用的上游确实存在
            # 于 config.upstreams (禁用也算, 那是合法隐藏; 名字根本不存在才是
            # 悬空配置错, 旧链路拖到运行时才报 "Unknown upstream")。
            known_upstreams = set(
                (self.config.get("upstreams") or {}).keys()
            )
            registry.validate_specs(
                specs,
                self.config.get("routing", {}),
                self.config.get("upstream_tool_mapping", {}),
                strict=strict,
                known_upstreams=known_upstreams,
            )
            # 发布集 = 至少一个启用 backer 且未被 publish_mode=hidden 下架。
            # 热开关见 set_upstream_enabled(), 启动语义与其差集计算一致。
            publishable = self._publishable_specs(specs)
            registered = registry.register_tools_from_config(self, publishable)
            self._published_tools = set(registered)
            disable_dangerous = bool(self.config.get("disable_dangerous", False))
            blocked_dangerous = sum(
                1 for s in specs if disable_dangerous and s.get("dangerous")
            )
            hidden = len(specs) - len(registered) - blocked_dangerous
            logger.info(
                f"Registered {len(registered)} tools from config "
                f"(dangerous={any(s.get('dangerous') for s in publishable)}, "
                f"blocked_by_disable_dangerous={blocked_dangerous}, "
                f"hidden_by_disabled_upstream={hidden})"
            )

    def _register_status_tools(self):
        @self.mcp.tool()
        async def get_server_status() -> dict:
            """获取网关状态"""
            # Round-12 簇G: gateway 字段复用 aggregate_health, 不再硬编码
            # "healthy" —— 上游全挂/未登记时必须如实报告。
            expected = self._expected_upstream_names()
            health = self.aggregate_health(self.upstreams, expected=expected)
            return {
                "gateway": health["status"],
                "upstreams": self._build_upstream_summary(
                    self.upstreams, expected=expected
                ),
                "timestamp": health["timestamp"],
            }

        @self.mcp.tool()
        async def get_health() -> dict:
            """健康检查 — 聚合上游状态 (含配置启用但未登记的上游)。"""
            return self.aggregate_health(
                self.upstreams, expected=self._expected_upstream_names()
            )

    def _expected_upstream_names(self) -> set[str]:
        """配置中 enabled 的上游名集合, 即启动后期望登记进 self.upstreams 的。

        db/yaml 两模式装配后的 config 同构; 缺 upstreams 段时返回空集
        (与 aggregate_health 的"显式空 expected = healthy"语义一致)。
        """
        from ..core.tool_loader import enabled_upstream_names

        return enabled_upstream_names(getattr(self, "config", None) or {})

    @staticmethod
    def _build_upstream_summary(upstreams: dict, *, expected: set[str] | None = None) -> dict:
        """L5 (2026-09-07 5th-round audit): 共享 upstream → {status, available}
        收集, get_server_status 与 aggregate_health 都用。

        Round-12 簇G: expected 中配置启用但未登记的上游 (构造/启动失败)
        补 ``{"status": "unregistered", "available": False}``, 不再静默缺席。
        """
        summary = {
            name: {
                "status": client.status.value,
                "available": client.is_available,
            }
            for name, client in upstreams.items()
        }
        if expected:
            for name in sorted(expected - set(upstreams)):
                summary[name] = {"status": "unregistered", "available": False}
        return summary

    @staticmethod
    def aggregate_health(
        upstreams: dict, expected: set[str] | None = None
    ) -> dict:
        """LOW8 + L4 (2026-09-07 5th-round audit): 聚合 upstream 状态为整体
        health. 改 staticmethod (不用 cls)。

        Round-12 簇G: 增加 expected (配置启用的上游名集合)。配置启用却未
        登记进 upstreams 的上游记为 ``unregistered`` —— 旧实现 {}/{"a":"unavailable"}
        等场景下"无 upstream 即 healthy"会在全部上游启动失败时谎报健康。

        - 无任何状态可报告 (upstreams 空且 expected 空/None): healthy
        - 全 healthy: healthy
        - 全 unavailable/unregistered: unhealthy
        - 其他 (有 degraded / 部分缺失): degraded

        expected=None 保持旧的单参调用合同 (以登记集合为准)。
        """
        upstream_states = {
            name: client.status.value
            for name, client in upstreams.items()
        }
        if expected:
            for name in sorted(expected - set(upstreams)):
                upstream_states[name] = "unregistered"
        if not upstream_states:
            overall = "healthy"
        elif all(s == "healthy" for s in upstream_states.values()):
            overall = "healthy"
        elif all(s in ("unavailable", "unregistered") for s in upstream_states.values()):
            overall = "unhealthy"
        else:
            overall = "degraded"
        return {
            "status": overall,
            "upstreams": upstream_states,
            "timestamp": int(time.time()),
        }

    def _find_omni_client(self) -> "OmniClient | None":
        """从 self.upstreams 里找到 OmniClient 实例 (按类型, 不依赖配置里的 name)

        提升到类方法是为了让 ``_do_initialize`` 也能调。原先是 ``serve_http``
        内部的嵌套函数, commit 399b18c 把调用挪进 ``_do_initialize`` 时忘了
        同步上提, 启动会 AttributeError。
        """
        for client in self.upstreams.values():
            if isinstance(client, OmniClient):
                return client
        return None
