"""THSDK 错误分类枚举与翻译层。

将 thsdk 包（panghu11033/thsdk）抛出的异常分类为可透传给 MCP 客户端的
错误类型，风格对齐 tdx_quant_errors.py。

thsdk 在启动时惰性 import，且离线测试会 mock 掉整个模块，因此这里不直接
import thsdk 做 isinstance，而是按异常类的 MRO 类名匹配 ——
避免 thsdk 未安装时本文件导入失败，也方便测试用同名假异常。
"""
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ThsdkErrorType(Enum):
    """THSDK 错误类型枚举。"""

    INIT_FAILED = "init_failed"
    AUTH_FAILED = "auth_failed"
    NOT_AUTHENTICATED = "not_authenticated"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    API_ERROR = "api_error"
    INVALID_PARAM = "invalid_param"
    UNKNOWN = "unknown"


@dataclass
class ThsdkError:
    """THSDK 错误详情。"""

    error_type: ThsdkErrorType
    message: str
    recoverable: bool = True
    code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "error_type": self.error_type.value,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.code:
            result["code"] = self.code
        return result


def _class_names(exc: BaseException) -> frozenset[str]:
    """收集异常及其 MRO 的全部类名（含 thsdk 模块延迟加载场景）。"""
    return frozenset(cls.__name__ for cls in type(exc).__mro__)


def _is_thsdk_exc(exc: BaseException, *names: str) -> bool:
    cls_names = _class_names(exc)
    return any(name in cls_names for name in names)


def classify_exception(exc: Exception) -> ThsdkError:
    """将底层异常分类为 ThsdkError。

    thsdk 4 个公开异常（见包 __init__）：
    - ``AuthenticationError``: 账号密码错误 / 环境变量只配了一只
    - ``NotAuthenticatedError``: 会话失效，需要重新 auth()
    - ``APIError``: 服务端业务错误，带 code/message
    - ``THSDKError``: 基类
    """
    if isinstance(exc, asyncio.TimeoutError):
        return ThsdkError(
            error_type=ThsdkErrorType.TIMEOUT,
            message=f"调用 THSDK 超时：{str(exc)[:200]}",
            recoverable=True,
        )
    if isinstance(exc, (ModuleNotFoundError, ImportError)):
        return ThsdkError(
            error_type=ThsdkErrorType.INIT_FAILED,
            message=f"thsdk 包加载失败：{type(exc).__name__}: {str(exc)[:200]}",
            recoverable=False,
        )
    if _is_thsdk_exc(exc, "AuthenticationError"):
        return ThsdkError(
            error_type=ThsdkErrorType.AUTH_FAILED,
            message=f"同花顺登录失败：{str(exc)[:200]}",
            recoverable=False,
            code=getattr(exc, "code", None),
        )
    if _is_thsdk_exc(exc, "NotAuthenticatedError"):
        return ThsdkError(
            error_type=ThsdkErrorType.NOT_AUTHENTICATED,
            message=f"同花顺会话未登录或已失效，请重新登录：{str(exc)[:200]}",
            recoverable=True,
            code=getattr(exc, "code", None),
        )
    if isinstance(exc, ValueError):
        return ThsdkError(
            error_type=ThsdkErrorType.INVALID_PARAM,
            message=f"参数错误：{str(exc)[:200]}",
            recoverable=False,
        )
    if _is_thsdk_exc(exc, "APIError"):
        code = getattr(exc, "code", None)
        msg = getattr(exc, "message", str(exc))[:200]
        rate_limited = str(code).lower() in {"-32003", "rate_limit", "ratelimit"} or (
            "限频" in msg or "频繁" in msg or "rate" in msg.lower()
        )
        return ThsdkError(
            error_type=ThsdkErrorType.RATE_LIMITED if rate_limited else ThsdkErrorType.API_ERROR,
            message=f"同花顺接口错误：{msg}",
            recoverable=True,
            code=str(code) if code is not None else None,
        )
    if _is_thsdk_exc(exc, "THSDKError"):
        return ThsdkError(
            error_type=ThsdkErrorType.API_ERROR,
            message=f"同花顺 SDK 错误：{str(exc)[:200]}",
            recoverable=True,
        )
    return ThsdkError(
        error_type=ThsdkErrorType.UNKNOWN,
        message=f"调用失败：{type(exc).__name__}: {str(exc)[:200]}",
        recoverable=False,
    )
