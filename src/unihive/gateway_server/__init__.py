"""
UniHive MCP Gateway Server - 统一 MCP 网关
同时 serve MCP 接口和管理控制台。
"""
import argparse
import asyncio
import logging
import os
from collections import deque
import socket  # noqa: F401  (审计测试读 gs_mod.socket; 用法在 http.py)
import sqlite3
import sys
from pathlib import Path

import yaml

import uvicorn  # noqa: F401  (审计测试读 gs_mod.uvicorn; 用法在 http.py)
from fastmcp import FastMCP

from ..api.fuyao_client import FuyaoClient  # noqa: F401 — 测试 monkeypatch gs.FuyaoClient
from ..api.mootdx2_client import MooTDX2Client  # noqa: F401 — type hint (server.upstreams: dict[...])
from ..api.tdx_quant_client import TdxQuantClient  # noqa: F401 — type hint
from ..api.upstream_client import UpstreamClient  # noqa: F401 — type hint
from ..core.config_db import resolve_project_path
# register_tool: 历史 facade 入口 (测试依赖), 子模块内部走 registry.register_tool。
from ..core.registry import register_tool  # noqa: F401
from ..core.router import Router
from ..storage.cache import Cache
from ..storage.config_store import ConfigStore
from ..utils.config_loader import (
    load_config,
    merge_config_overlay,
    validate_config,
)
from ..utils.log_config import configure_logging

logger = logging.getLogger(__name__)


# 模块级函数/常量 (版本号/servers 解析/console 鉴权配置) 已拆到 ._helpers,
# 这里 re-export 保持历史导入路径不变 (测试与外部脚本依赖)。
from ._helpers import (  # noqa: F401
    GATEWAY_VERSION,
    RESERVED_TOOL_NAMES,
    _configure_stdio_utf8,
    _console_auth_config_from_env,
    _get_console_auth_config,
    _parse_mootdx2_servers,
    load_config_yaml_overlay,
    coerce_float_field,
    coerce_int_field,
)
from .cached_calls import CachedExecutionMixin
from .config_edits import ConfigEditsMixin
from .config_upgrade import ConfigUpgradeMixin
from .hot_toggle import HotToggleMixin
from .http import HttpServeMixin
from .lifecycle import LifecycleMixin
from .mcp_tools import McpToolsMixin
from .openapi_import import OpenApiImportMixin
from .upstreams import UpstreamControlMixin


class GatewayServer(
    LifecycleMixin,
    HttpServeMixin,
    UpstreamControlMixin,
    HotToggleMixin,
    ConfigEditsMixin,
    ConfigUpgradeMixin,
    OpenApiImportMixin,
    CachedExecutionMixin,
    McpToolsMixin,
):
    """UniHive MCP 网关 - 聚合多个上游 MCP Server"""

    DEFAULT_UPSTREAM_START_TIMEOUT = 30.0
    DEFAULT_REQUEST_TIMEOUT = 30.0  # 单 request router.route() 总超时 (MED 3)

    def __init__(
        self,
        config_path: str = "config/upstreams.yaml",
        *,
        strict_env: bool = True,
        strict_validation: bool = True,
        upstream_start_timeout: float | None = None,
    ):
        # L3 (round 10 audit): 相对 config_path 锚定项目根, 与从任意 CWD 启动
        # 无关。旧实现 Path(relative) 锚定 CWD → 从别的工作目录启动时找不到
        # upstreams.yaml, db 模式空库首启会拿不到出厂 YAML 模板而 seed 空配置;
        # 绝对路径原样保留 (resolve_project_path 第三语义)。
        self.config_path = Path(
            resolve_project_path(config_path, "config/upstreams.yaml")
        )
        self._strict_env = strict_env

        # v2 (2026-09-18): 配置事实源默认是 data/config.db 实体表;
        # UNIHIVE_CONFIG_SOURCE=yaml 回到纯 YAML 出厂模式 (故障逃生通道);
        # UNIHIVE_CONFIG_DB 可覆盖库路径。db 模式下, 这里的 YAML 加载只是
        # 预检基线, _do_initialize 的 _load_db_config() 会用库内实体装配结果
        # 整体替换 self.config。
        source = os.environ.get("UNIHIVE_CONFIG_SOURCE", "db").strip().lower()
        # L1 (round 9 audit): 任何拼写错误 (如 "deeb") 旧实现静默当作 db 模式,
        # 用户以为逃生到了 yaml 却进了 db。fail-closed: 仅接受 db/yaml/空。
        if source not in ("", "db", "yaml"):
            raise ValueError(
                f"UNIHIVE_CONFIG_SOURCE must be 'db' or 'yaml' (got {source!r})"
            )
        self._db_mode = source != "yaml"
        # M10: 库路径锚定项目根, 从任意 CWD 启动都指向同一个 config.db,
        # 杜绝在 CWD 下静默生成空库重 seed 的裂脑; 绝对路径原样保留。
        from ..core.config_db import resolve_config_db_path

        self._db_path = str(
            resolve_config_db_path(os.environ.get("UNIHIVE_CONFIG_DB"))
        )

        # YAML 预检基线。db 模式下真正的事实源是 config.db, YAML 损坏不得阻止
        # 健康库启动 (M9): 预检失败只告警并以空 dict 占位, _load_db_config()
        # 会用库内实体整体替换; 空库 seed 坏模板仍 fail-closed (H4); yaml
        # 逃生模式保持严格: 加载/校验失败直接抛出。
        try:
            # L3 (round 10 audit): 必须用锚定后的 self.config_path 而非原始
            # config_path 参数 — 后者锚定 CWD, yaml 逃生模式 FileNotFoundError,
            # db 预检基线落空则空库首启拿不到出厂模板。
            self.config = load_config(str(self.config_path), strict_env=strict_env)

            # Load config.yaml and merge (config.yaml takes precedence for
            # overlapping keys). M1/L1 语义见 load_config_yaml_overlay:
            # 解析失败 + 顶层 console: 键 → fail-closed; 其余只告警。
            config_yaml_path = self.config_path.parent / "config.yaml"
            config_yaml = load_config_yaml_overlay(
                config_yaml_path, strict_env=strict_env
            )
            if config_yaml is not None:
                # Merge: config.yaml overrides upstreams.yaml (浅层合并,
                # 与 `unihive config lint` 共用 merge_config_overlay)
                merge_config_overlay(self.config, config_yaml)
            errors = validate_config(self.config)
            if errors:
                for e in errors:
                    logger.warning(f"Config validation: {e}")
                if strict_validation:
                    raise ValueError(
                        f"Config validation failed ({len(errors)} error(s)): "
                        + "; ".join(errors)
                    )
        except Exception:
            if not self._db_mode:
                raise
            logger.warning(
                "YAML config preflight failed; continuing in db mode — "
                "config.db is authoritative (an empty DB will still fail "
                "closed when seeding from this template)",
                exc_info=True,
            )
            self.config = {}
        # 与 console_api 共用同一份 config，避免双轨不一致
        from ..utils import console_api as _ca
        _ca.set_config(self.config)
        self.upstreams: dict[str, UpstreamClient | FuyaoClient | MooTDX2Client | TdxQuantClient] = {}
        self.cache: Cache | None = None
        self.router: Router | None = None
        self.mcp: FastMCP | None = None
        # 上游启用/禁用热开关 (2026-09-18):
        # tool_specs 存全量 spec (validate 对全部生效, 与 enabled 无关);
        # _published_tools 是真正发布到 /mcp 的 spec 工具名集合 (不含手写
        # get_server_status/get_health), 热增删以此为真来源; toggle 锁串行化。
        self.tool_specs: list[dict] = []
        self._published_tools: set[str] = set()
        # publish_mode=hidden 的工具名 (仅 db 模式有内容): 不发布,
        # 上游热开关与启动注册都要跳过, 防止 backer 变动时被重新放出。
        self._db_hidden_tools: set[str] = set()
        # v2 配置实体库 (data/config.db), _do_initialize 早期装配;
        # None 表示 UNIHIVE_CONFIG_SOURCE=yaml 逃生模式或尚未初始化。
        self.config_store: ConfigStore | None = None
        self._upstream_toggle_lock = asyncio.Lock()
        # M2 (round 11): start() 超时后的后台自愈任务 (按上游名分组)。
        # 阻塞型 client (mootdx2/tdx_quant) 的初始化在专用线程池不可中断,
        # wait_for 超时后由这些任务驱动 client 完成延迟启动+健康循环;
        # disable / stop / init 失败清理时按名取消, 不复活已停 client。
        self._late_start_tasks: dict[str, set[asyncio.Task]] = {}
        self._running = False
        # initialize() 幂等位 (旧实现在 start/serve_http 各调一次, 部分 client
        # 重入状态混乱) + stop() 取消 health loop 的事件。
        self._initialized = False
        self._shutdown_event = asyncio.Event()
        # 在途请求计数 + 优雅关闭
        self._active_requests = 0
        self._requests_lock = asyncio.Lock()
        # /api/runtime: initialize 就绪时刻 (单调钟) + 近 5 分钟调用时间戳环; stop() 复位。
        self._started_monotonic: float | None = None
        self._call_timestamps: deque[float] = deque()
        # _execute_cached 的 per-key 单飞 dict (HIGH 1, 2026-09-07):
        # 同 key 并发 miss 只触发一次 router.route()。
        self._in_flight_requests: dict[str, asyncio.Future] = {}
        # initialize() 并发保护: 两次 await initialize() 必须串行化,
        # 否则 cache / upstream 会被重复初始化 (HIGH #1)。
        self._init_lock = asyncio.Lock()
        # 周期 cache 清理任务 (MCP 二次审视 M4): cleanup_expired() 从不调度,
        # 过期条目只能等 LRU; stop() 时取消。
        self._cache_cleanup_task: asyncio.Task | None = None
        # 单个 upstream 启动超时, 防止某个卡死的 upstream 让整个 init 跟着挂 (MED #4)。
        self._upstream_start_timeout = (
            upstream_start_timeout
            if upstream_start_timeout is not None
            else self.DEFAULT_UPSTREAM_START_TIMEOUT
        )
        # gateway 调参 (per_upstream_timeout + 限流器): 先按 YAML 基线应用;
        # db 模式 _load_db_config 装配出库内配置后会再跑一次。
        self._apply_gateway_tuning()

    def _apply_gateway_tuning(self) -> None:
        """从 self.config 应用 gateway 调参 (per_upstream_timeout + QPS)。

        __init__ 按 YAML 基线跑一次; db 模式下库内实体装配完成后再跑一次,
        让控制台对 gateway 调参段的修改无需重启即可被 router/limiter 读到。
        """
        gateway_cfg = self.config.get("gateway", {})
        # Round-12 簇E: 裸强转坏值以前抛无名错误, coerce_* 助手带点路径
        self._per_upstream_timeout = coerce_float_field(
            gateway_cfg.get("per_upstream_timeout", self.DEFAULT_REQUEST_TIMEOUT),
            "gateway.per_upstream_timeout",
            default=self.DEFAULT_REQUEST_TIMEOUT,
            minimum=0.0,
            exclusive_min=True,
        )
        from ..core.rate_limiter import get_default_limiter
        self.limiter = get_default_limiter()
        # 清掉旧 per-key: 本次未列出的条目回到默认兜底 (热升级/热编辑重跑关键)
        self.limiter.reset_configurations()
        rate_cfg = gateway_cfg.get("rate_limit", {})
        for upstream_name, params in rate_cfg.items():
            field = f"gateway.rate_limit.{upstream_name}"
            if isinstance(params, dict):
                rate = coerce_float_field(
                    params.get("rate", 20.0), f"{field}.rate",
                    minimum=0.0, exclusive_min=True,
                )
                capacity = coerce_float_field(
                    params.get("capacity", 40.0), f"{field}.capacity",
                    minimum=0.0, exclusive_min=True,
                )
                self.limiter.configure(upstream_name, rate=rate, capacity=capacity)
            elif isinstance(params, (int, float)) and not isinstance(params, bool):
                rate = coerce_float_field(
                    params, field, minimum=0.0, exclusive_min=True,
                )
                self.limiter.configure(upstream_name, rate=rate)
            else:
                raise ValueError(
                    f"{field} 必须是正数或含 rate/capacity 的 object, "
                    f"got {params!r} ({type(params).__name__})"
                )

        # per-source 池容量 + Reader 闸门 (cfg > env > default, 启动期一次性)
        self._apply_pool_config()

    def _apply_pool_config(self) -> None:
        """解析 cfg → 注入 blocking_pool 单例 (启动期一次性, 运行时改需重启)。"""
        from ..core.pool_config import apply_pool_config
        from ..core.blocking_pool import _mootdx2, _tdx_quant  # type: ignore
        self._pool_config = apply_pool_config(
            self.config,
            mootdx2_bundle=_mootdx2,
            tdx_quant_bundle=_tdx_quant,
            logger=logger,
        )


async def async_main(transport: str = "stdio", host: str | None = None, port: int | None = None, config_path: str | None = None):
    configure_logging(transport)

    cfg_path = config_path or "config/upstreams.yaml"
    if transport == "stdio":
        _configure_stdio_utf8()
        # Round-12 簇I: stdio 只走 stdin/stdout, 显式给的 host/port 会被静默忽略。
        if host is not None or port is not None:
            logger.warning(
                "transport=stdio ignores host/port (got host=%r, port=%r); "
                "they only apply to transport=http",
                host, port,
            )
    server = GatewayServer(config_path=cfg_path)
    logger.info(
        f"unihive starting: transport={transport} config={cfg_path} "
        f"upstreams={len(server.config.get('upstreams') or {})}"
    )
    if transport == "stdio":
        await server.start()
    elif transport == "http":
        await server.serve_http(host=host, port=port)
    else:
        raise ValueError(f"Unknown transport: {transport}")


def _build_arg_parser() -> "argparse.ArgumentParser":
    parser = argparse.ArgumentParser(description="UniHive MCP Gateway")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="http",
        help="Transport mode (default: http). 'stdio' for stdio transport.",
    )
    parser.add_argument("--host", default=None, help="HTTP bind host (default: from config)")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port (default: from config)")
    parser.add_argument(
        "--config", default=None,
        help="Path to upstreams.yaml (default: config/upstreams.yaml)",
    )
    return parser


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    try:
        asyncio.run(async_main(
            transport=args.transport,
            host=args.host,
            port=args.port,
            config_path=args.config,
        ))
    except (ValueError, yaml.YAMLError, KeyError) as e:
        # 配置类失败: 校验失败 / YAML 语法错 / 必填键缺失 / 未知 transport,
        # 给一个干净消息 + 退出码 2, 不裸打 traceback (Round-12 簇E)。
        print(f"error: configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    except (OSError, sqlite3.Error, RuntimeError) as e:
        # L3 (round 11): 已知启动失败 — 端口/文件/磁盘 OSError、config.db
        # 持续 locked (sqlite3.Error, aiosqlite 透传同一异常族)、运行态
        # 自检 RuntimeError — 给干净单行 + 退出码 1, 不裸打 traceback。
        print(
            f"error: failed to start UniHive gateway: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        # stdio 模式父进程关停时正常路径, 不打 traceback
        sys.exit(0)


if __name__ == "__main__":
    main()
