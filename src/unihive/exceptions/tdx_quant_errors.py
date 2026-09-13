"""TdxQuant 错误分类枚举与翻译层。

将 tqcenter.py 返回的 ErrorId 字符串与底层异常翻译为 TdxQuantError，
风格对齐 mootdx2_errors.py。
"""
import asyncio
import logging
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TdxQuantErrorType(Enum):
    """TdxQuant 错误类型枚举。"""

    DISCONNECTED = "disconnected"
    UPSTREAM_UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INIT_FAILED = "init_failed"
    STRATEGY_EXISTS = "strategy_exists"
    UNKNOWN = "unknown"


@dataclass
class TdxQuantError:
    """TdxQuant 错误详情。"""

    error_type: TdxQuantErrorType
    message: str
    recoverable: bool = True
    error_id: Optional[str] = None
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "error_type": self.error_type.value,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.error_id:
            result["error_id"] = self.error_id
        if self.details:
            result["details"] = self.details
        return result


_ERRORID_MAP: dict[str, tuple[TdxQuantErrorType, bool]] = {
    "6": (TdxQuantErrorType.DISCONNECTED, True),
    "7": (TdxQuantErrorType.DISCONNECTED, True),
    "12": (TdxQuantErrorType.STRATEGY_EXISTS, False),
}


def translate_errorid(result: Any) -> Optional[TdxQuantError]:
    """检查 tqcenter 返回 dict 的 ErrorId 字段，返回 TdxQuantError 或 None。"""
    if not isinstance(result, dict):
        return None
    error_id = str(result.get("ErrorId", "0"))
    if error_id == "0":
        return None
    err_msg = str(result.get("ErrMsg", ""))
    if error_id in _ERRORID_MAP:
        err_type, recoverable = _ERRORID_MAP[error_id]
        return TdxQuantError(
            error_type=err_type,
            message=err_msg or err_type.value,
            recoverable=recoverable,
            error_id=error_id,
        )
    return TdxQuantError(
        error_type=TdxQuantErrorType.UNKNOWN,
        message=f"通达信返回错误：ErrorId={error_id}, ErrMsg={err_msg}",
        recoverable=False,
        error_id=error_id,
    )


def classify_exception(exc: Exception) -> TdxQuantError:
    """将底层异常分类为 TdxQuantError。"""
    if isinstance(exc, (ModuleNotFoundError, FileNotFoundError)):
        return TdxQuantError(
            error_type=TdxQuantErrorType.INIT_FAILED,
            message=f"tqcenter 加载失败：{type(exc).__name__}: {str(exc)[:200]}",
            recoverable=False,
        )
    if isinstance(exc, asyncio.TimeoutError):
        return TdxQuantError(
            error_type=TdxQuantErrorType.TIMEOUT,
            message=f"调用通达信超时：{str(exc)[:200]}",
            recoverable=True,
        )
    if isinstance(exc, ConnectionError):
        return TdxQuantError(
            error_type=TdxQuantErrorType.UPSTREAM_UNAVAILABLE,
            message=f"通达信客户端未响应：{str(exc)[:200]}",
            recoverable=True,
        )
    if isinstance(exc, OSError):
        msg_lower = str(exc).lower()
        if any(kw in msg_lower for kw in ["timeout", "connection", "network", "refused"]):
            return TdxQuantError(
                error_type=TdxQuantErrorType.UPSTREAM_UNAVAILABLE,
                message=f"网络错误：{str(exc)[:200]}",
                recoverable=True,
            )
    logger.error(
        f"Unclassified exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    )
    return TdxQuantError(
        error_type=TdxQuantErrorType.UNKNOWN,
        message=f"调用失败：{type(exc).__name__}: {str(exc)[:200]}",
        recoverable=False,
    )
