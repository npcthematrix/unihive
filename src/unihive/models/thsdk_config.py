"""THSDK 配置管理。

配置来源：config/upstreams.yaml 的 thsdk 段。
凭证（THS_USERNAME / THS_PASSWORD）与写开关（ALLOW_WATCHLIST_WRITE）
只从进程环境变量读取，不写进 YAML——与 thsdk 包自身的环境变量约定一致，
也避免凭证落盘到配置仓库。
"""
from dataclasses import dataclass
from typing import Any

from ..utils.env_utils import env_flag

WRITE_ENV_FLAG = "ALLOW_WATCHLIST_WRITE"
USERNAME_ENV = "THS_USERNAME"
PASSWORD_ENV = "THS_PASSWORD"

# 客户端 asyncio 超时相对路由 per_upstream_timeout 的余量：必须保证
# "客户端超时 < 路由超时"，否则路由先取消 call_tool
# （CancelledError 是 BaseException，客户端 except Exception 捕获不到，
#  熔断计数会失明）。
CLIENT_TIMEOUT_MARGIN_SEC = 1.0


def resolve_thsdk_call_timeout(
    configured_sec: float, router_timeout_sec: float | None
) -> float:
    """钳制客户端超时到路由预算内（保证小于路由超时）。

    2026-09-22 事故实证：config.db 里旧值 30s、路由 10s，注入的原生超时
    变成 29s，板块调用 DLL 不先返回，路由取消后单 worker 僵尸、全线超时。
    """
    if router_timeout_sec is None:
        return float(configured_sec)
    return min(
        float(configured_sec),
        max(1.0, float(router_timeout_sec) - CLIENT_TIMEOUT_MARGIN_SEC),
    )


@dataclass
class ThsdkConfig:
    """THSDK 上游配置。"""

    name: str = "thsdk"
    # 单次 thsdk 调用的 asyncio 超时（秒），底层同步调用在线程池执行
    call_timeout_sec: int = 30
    # 相邻两次 thsdk 调用的最小间隔（毫秒），规避服务端 50ms 限频
    throttle_ms: int = 60

    @classmethod
    def from_dict(
        cls,
        name: str,
        data: dict[str, Any],
        router_timeout_sec: float | None = None,
    ) -> "ThsdkConfig":
        # 构造即钳制：config.db 旧值（30s）可能反超当前路由预算，
        # 不在这里收口就只能散落到每个 client 构造点。
        call_timeout_sec = int(
            resolve_thsdk_call_timeout(
                int(data.get("call_timeout_sec", 30)), router_timeout_sec
            )
        )
        return cls(
            name=name,
            call_timeout_sec=call_timeout_sec,
            throttle_ms=int(data.get("throttle_ms", 60)),
        )

    @property
    def allow_watchlist_write(self) -> bool:
        """以进程环境变量为唯一来源，刻意不提供运行时开关后门。"""
        return env_flag(WRITE_ENV_FLAG)
