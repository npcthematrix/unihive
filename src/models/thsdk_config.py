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


@dataclass
class ThsdkConfig:
    """THSDK 上游配置。"""

    name: str = "thsdk"
    # 单次 thsdk 调用的 asyncio 超时（秒），底层同步调用在线程池执行
    call_timeout_sec: int = 30
    # 相邻两次 thsdk 调用的最小间隔（毫秒），规避服务端 50ms 限频
    throttle_ms: int = 60

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "ThsdkConfig":
        return cls(
            name=name,
            call_timeout_sec=int(data.get("call_timeout_sec", 30)),
            throttle_ms=int(data.get("throttle_ms", 60)),
        )

    @property
    def allow_watchlist_write(self) -> bool:
        """以进程环境变量为唯一来源，刻意不提供运行时开关后门。"""
        return env_flag(WRITE_ENV_FLAG)
