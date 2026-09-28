"""per-source 池配置解析 (启动期一次性读入)。

优先级: ``config.db / yaml (cfg.upstreams.<name>.<field>)`` >
         ``env (UNIHIVE_MOOTDX2_* / UNIHIVE_TDX_QUANT_*)`` > code default。

启动期由 :func:`GatewayServer._apply_pool_config` 调用, 把解析结果注入
``core.blocking_pool._PoolBundle.set_size_override``, 让池创建时优先走
cfg 值。mootdx2 client 的 Reader 闸门在 ``MooTDX2Client.__init__`` 内走
相同优先级 (settings.reader_limit > env > default)。

**Why**: 让用户能在控制台编辑 / YAML 文件里调池容量 + Reader 闸门,
不必改代码或记 env 变量名; env 仍作为临时覆盖逃生口 (优先级低于 cfg,
避免改完控制台后被环境变量盖回去)。
**How to apply**: 加新可调参数时, 在 :class:`PoolConfig` 加字段 +
对应 env 名 + 默认值 + resolve 函数; 网关 init 重放 :func:`_apply_pool_config`
即生效 (启动期一次性, 运行时改需重启)。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# === 默认值 (与 blocking_pool / mootdx2_client 内部常量保持一致) ===
DEFAULT_MOOTDX2_READER_LIMIT = 6
DEFAULT_MOOTDX2_WORKER_THREADS = 16
DEFAULT_TDX_QUANT_WORKER_THREADS = 10

# === env 命名 (兼容历史 env 名, 新加可调参数也走 UNIHIVE_<NAME>_<FIELD> 命名) ===
ENV_MOOTDX2_READER_LIMIT = "UNIHIVE_MOOTDX2_READER_LIMIT"
ENV_MOOTDX2_WORKER_THREADS = "UNIHIVE_MOOTDX2_WORKER_THREADS"
ENV_TDX_QUANT_WORKER_THREADS = "UNIHIVE_TDX_QUANT_WORKER_THREADS"


@dataclass(frozen=True)
class PoolConfig:
    """per-source 池配置 (启动期读入, immutable)。"""

    mootdx2_reader_limit: int
    mootdx2_worker_threads: int
    tdx_quant_worker_threads: int
    # 字段名 → 来源 ("cfg" | "env" | "default"); 控制台展示用, 不影响 resolve 逻辑
    sources: dict[str, str] = field(default_factory=dict)


def _resolve_int(
    cfg_value: object,
    env_var: str,
    default: int,
    *,
    minimum: int = 1,
) -> tuple[int, str]:
    """``cfg > env > default`` 三级回退 + 钳到 ``minimum``; 返 (值, 来源)。

    - ``cfg_value`` 是 None / 缺失: 走 env;
    - ``cfg_value`` 非法 (非 int / 不可转换): WARNING + 走 env;
    - ``env`` 缺失 / 非法: 走 default;
    - 最终结果 max(1, n) 钳到 ≥ minimum。
    """
    if cfg_value is not None:
        try:
            n = int(cfg_value)  # type: ignore[arg-type]
            return max(minimum, n), "cfg"
        except (TypeError, ValueError):
            logger.warning(
                "[pool-config] cfg value %r for %s is invalid; falling back to env",
                cfg_value, env_var,
            )
    env_val = os.environ.get(env_var)
    if env_val is not None:
        try:
            n = int(env_val)
            return max(minimum, n), "env"
        except (TypeError, ValueError):
            logger.warning(
                "[pool-config] env %s=%r is invalid; falling back to default %d",
                env_var, env_val, default,
            )
    return max(minimum, default), "default"


def resolve_pool_config(cfg: dict | None) -> PoolConfig:
    """从 cfg (config_db 装配后的 upstreams dict, 或 yaml 加载结果) 解析池配置。

    ``cfg=None`` 时回落 env / default (yaml 逃生模式但 cfg 尚未装配的场景)。"""
    upstreams = (cfg or {}).get("upstreams") or {}
    mootdx_cfg = upstreams.get("mootdx2") or {}
    tdxq_cfg = upstreams.get("tdx_quant") or {}

    reader_limit, src = _resolve_int(
        mootdx_cfg.get("reader_limit"),
        ENV_MOOTDX2_READER_LIMIT,
        DEFAULT_MOOTDX2_READER_LIMIT,
    )
    mootdx_worker, src2 = _resolve_int(
        mootdx_cfg.get("worker_threads"),
        ENV_MOOTDX2_WORKER_THREADS,
        DEFAULT_MOOTDX2_WORKER_THREADS,
    )
    tdxq_worker, src3 = _resolve_int(
        tdxq_cfg.get("worker_threads"),
        ENV_TDX_QUANT_WORKER_THREADS,
        DEFAULT_TDX_QUANT_WORKER_THREADS,
    )
    return PoolConfig(
        mootdx2_reader_limit=reader_limit,
        mootdx2_worker_threads=mootdx_worker,
        tdx_quant_worker_threads=tdxq_worker,
        sources={
            "mootdx2_reader_limit": src,
            "mootdx2_worker_threads": src2,
            "tdx_quant_worker_threads": src3,
        },
    )


def validate_pool_config(cfg: PoolConfig) -> list[str]:
    """启动期校验, 返 WARNING 列表 (不抛, 让用户选择是否继续)。

    - ``mootdx2_reader_limit > worker_threads``: Reader 永远跑不满
      (Reader 在 worker 上跑, worker 不够 reader 也跑不了)。不阻止启动
      但建议 ``reader_limit ≤ worker_threads``。
    """
    warnings: list[str] = []
    if cfg.mootdx2_reader_limit > cfg.mootdx2_worker_threads:
        warnings.append(
            f"mootdx2 reader_limit ({cfg.mootdx2_reader_limit}) > "
            f"worker_threads ({cfg.mootdx2_worker_threads}): "
            "Reader 在 worker 上跑, worker 不够时闸门永远跑不满; "
            "建议 reader_limit ≤ worker_threads (重启后生效)"
        )
    return warnings


def apply_pool_config(
    cfg: dict | None,
    *,
    mootdx2_bundle,
    tdx_quant_bundle,
    logger: logging.Logger | None = None,
) -> PoolConfig:
    """解析 cfg → 校验 → 注入 blocking_pool 单例; 返解析结果。

    由 :class:`GatewayServer._apply_pool_config` 在 4 个入口 (init / yaml /
    db / config_upgrade) 重放。运行时改需重启 (Semaphore / ThreadPool 不
    支持扩缩)。

    ``mootdx2_bundle`` / ``tdx_quant_bundle`` duck-typed (只要有
    ``set_size_override(int)``), 避免 pool_config 模块直接 import
    blocking_pool 形成循环。
    """
    pool_cfg = resolve_pool_config(cfg)
    for warning in validate_pool_config(pool_cfg):
        if logger is not None:
            logger.warning("[pool-config] %s", warning)
    mootdx2_bundle.set_size_override(pool_cfg.mootdx2_worker_threads)
    tdx_quant_bundle.set_size_override(pool_cfg.tdx_quant_worker_threads)
    if logger is not None:
        logger.info(
            "[pool-config] mootdx2 worker_threads=%d reader_limit=%d | "
            "tdx_quant worker_threads=%d (来源: cfg > env > default)",
            pool_cfg.mootdx2_worker_threads,
            pool_cfg.mootdx2_reader_limit,
            pool_cfg.tdx_quant_worker_threads,
        )
    return pool_cfg