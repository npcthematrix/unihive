"""TdxQuant 配置管理。

配置来源：config/upstreams.yaml 的 tdx_quant 段。
结构对齐 mootdx2_config.py 的 MooTDX2Config + MooTDX2Settings。
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

MAX_STRATEGY_INSTANCES = 8
ENV_STRATEGY_INSTANCES = "UNIHIVE_TDX_QUANT_STRATEGY_INSTANCES"


def clamp_strategy_instances(value: object | None = None, *, default: int = 1) -> int:
    """cfg > env > default, 钳到 [1, MAX_STRATEGY_INSTANCES]。"""
    raw: object = value
    if raw is None or raw == "":
        raw = os.environ.get(ENV_STRATEGY_INSTANCES, default)
    try:
        n = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = default
    return max(1, min(MAX_STRATEGY_INSTANCES, n))


@dataclass
class TdxQuantSettings:
    """TdxQuant 运行时设置。"""

    # 通达信安装目录（含 PYPlugins/user/tqcenter.py）
    tdx_root: str = ""

    # 策略唯一标识，传给 tq.initialize() 作为策略 ID
    # 默认空字符串 → 运行时用 __file__ 填充
    strategy_id: str = ""

    # 探活周期（秒）
    health_check_interval_sec: int = 60

    # 单次 tq.* 调用超时（秒）
    call_timeout_sec: int = 10

    # 同时进入 DLL 的 get 调用上限。TPythClient/TdxW 实测有效并行约 2,
    # 超过的请求只是制造超时后的僵尸 worker。
    call_limit: int = 2

    # 官方 1.3.0 起允许同一 py 多实例; tqcenter.tq 仍是进程内单例,
    # 所以 N>1 时网关拉 N 个子进程, 各 initialize 一次。默认 1 保持
    # 进程内直调, 不打现有测试。上限见 MAX_STRATEGY_INSTANCES。
    strategy_instances: int = 1

    # 连续探活失败 N 次后触发 reconnect
    reconnect_threshold: int = 3

    # 连续失败 N 次后降级为 UNAVAILABLE，停止自动重连
    unavailable_threshold: int = 10

    # 熔断后禁止探活把状态拉回 HEALTHY 的冷却秒数
    unavailable_cooldown_sec: int = 30

    # TQ 策略本地端口 (TQ Center DLL 通信端口)
    tdx_remote_port: int = 17709

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TdxQuantSettings":
        return cls(
            tdx_root=data.get("tdx_root", ""),
            strategy_id=data.get("strategy_id", ""),
            health_check_interval_sec=int(data.get("health_check_interval_sec", 60)),
            call_timeout_sec=int(data.get("call_timeout_sec", 10)),
            call_limit=max(1, int(data.get("call_limit", 2))),
            strategy_instances=clamp_strategy_instances(data.get("strategy_instances")),
            reconnect_threshold=int(data.get("reconnect_threshold", 3)),
            unavailable_threshold=int(data.get("unavailable_threshold", 10)),
            unavailable_cooldown_sec=max(0, int(data.get("unavailable_cooldown_sec", 30))),
            tdx_remote_port=int(data.get("tdx_remote_port", 17709)),
        )


@dataclass
class TdxQuantConfig:
    """TdxQuant 上游配置（与 MooTDX2Config 结构对齐）。"""

    name: str
    market: str = "std"
    settings: TdxQuantSettings = field(default_factory=TdxQuantSettings)

    @property
    def tdx_root_path(self) -> Path:
        """通达信安装目录的 Path 对象。"""
        return Path(self.settings.tdx_root) if self.settings.tdx_root else Path()

    @property
    def tqcenter_path(self) -> Path:
        """tqcenter.py 的预期路径。"""
        # 先尝试 user 目录，再尝试 sys 目录
        user_path = self.tdx_root_path / "PYPlugins" / "user" / "tqcenter.py"
        sys_path = self.tdx_root_path / "PYPlugins" / "sys" / "tqcenter.py"
        if sys_path.exists():
            return sys_path
        return user_path

    @property
    def tqcenter_dir(self) -> Path:
        """需要加入 sys.path 的目录（含 tqcenter.py）。"""
        # 先尝试 user 目录，再尝试 sys 目录
        user_dir = self.tdx_root_path / "PYPlugins" / "user"
        sys_dir = self.tdx_root_path / "PYPlugins" / "sys"
        if sys_dir.exists():
            return sys_dir
        return user_dir

    @property
    def tdx_exe_path(self) -> Path:
        """TdxW.exe 路径（保留字段，未来可能用于 UI/诊断展示）。"""
        return Path()
