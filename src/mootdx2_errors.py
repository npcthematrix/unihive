"""MooTDX2 错误分类枚举与错误处理"""
import logging
import traceback
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MooTDXErrorType(Enum):
    """MooTDX2 错误类型枚举"""

    # 连接相关 (可重试)
    CONNECTION_ERROR = "connection_error"  # 连接失败/超时
    TIMEOUT_ERROR = "timeout_error"  # 读取超时
    SERVER_UNAVAILABLE = "server_unavailable"  # 所有服务器不可用

    # 参数相关 (不可重试)
    INVALID_SYMBOL = "invalid_symbol"  # 股票代码不存在或格式错误
    INVALID_PARAM = "invalid_param"  # 参数值无效

    # 限流相关 (可重试)
    RATE_LIMITED = "rate_limited"  # 触发限流

    # 业务状态 (不是错误)
    NO_DATA = "no_data"  # 查询成功但无数据（如停牌/非交易日）

    # 内部错误 (需记录日志)
    INTERNAL_ERROR = "internal_error"  # 未预期异常


@dataclass
class MooTDXError:
    """MooTDX2 错误详情"""

    error_type: MooTDXErrorType
    message: str
    recoverable: bool = True  # 是否可重试
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "error_type": self.error_type.value,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.details:
            result["details"] = self.details
        return result


class ErrorClassifier:
    """异常分类器：将底层异常转换为 MooTDXError"""

    # 可重试的异常类型
    RETRYABLE_EXCEPTIONS = (
        ConnectionError,
        TimeoutError,
        OSError,
    )

    # 参数错误异常
    PARAM_EXCEPTIONS = (
        ValueError,
        KeyError,
    )

    @classmethod
    def classify(cls, exc: Exception, context: str = "") -> MooTDXError:
        """将异常分类为 MooTDXError"""

        # 连接错误
        if isinstance(exc, ConnectionError):
            return MooTDXError(
                error_type=MooTDXErrorType.CONNECTION_ERROR,
                message=f"连接失败: {str(exc)[:100]}",
                recoverable=True,
                details={"context": context},
            )

        # 超时错误
        if isinstance(exc, TimeoutError):
            return MooTDXError(
                error_type=MooTDXErrorType.TIMEOUT_ERROR,
                message=f"请求超时: {str(exc)[:100]}",
                recoverable=True,
                details={"context": context},
            )

        # OS 错误 (网络相关)
        if isinstance(exc, OSError):
            msg_lower = str(exc).lower()
            if any(kw in msg_lower for kw in ["timeout", "connection", "network", "refused"]):
                return MooTDXError(
                    error_type=MooTDXErrorType.CONNECTION_ERROR,
                    message=f"网络错误: {str(exc)[:100]}",
                    recoverable=True,
                    details={"context": context},
                )

        # 参数错误
        if isinstance(exc, (ValueError, KeyError)):
            return MooTDXError(
                error_type=MooTDXErrorType.INVALID_PARAM,
                message=f"参数错误: {str(exc)[:100]}",
                recoverable=False,
                details={"context": context},
            )

        # 默认归类为内部错误
        # 记录完整堆栈到日志
        logger.error(
            f"[{context}] Unclassified exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )

        return MooTDXError(
            error_type=MooTDXErrorType.INTERNAL_ERROR,
            message=f"内部错误: {type(exc).__name__}",
            recoverable=False,
            details={"context": context},
        )


def error_to_result(
    exc: Exception,
    context: str = "",
    log_stack: bool = True,
) -> dict[str, Any]:
    """将异常转换为统一的错误响应结构

    Args:
        exc: 捕获的异常
        context: 调用上下文（用于日志）
        log_stack: 是否记录完整堆栈

    Returns:
        统一的错误响应 dict
    """
    error = ErrorClassifier.classify(exc, context)

    if log_stack and error.error_type == MooTDXErrorType.INTERNAL_ERROR:
        # 内部错误需要记录完整堆栈
        logger.error(
            f"[{context}] Internal error details:\n{traceback.format_exc()}"
        )

    return {
        "success": False,
        "error": error.to_dict(),
    }


def no_data_result(message: str = "查询成功但无数据") -> dict[str, Any]:
    """返回 no_data 状态（不是错误，是正常业务状态）"""
    return {
        "success": True,
        "data": None,
        "error": {
            "error_type": MooTDXErrorType.NO_DATA.value,
            "message": message,
            "recoverable": True,
        },
    }
