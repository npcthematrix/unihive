"""
上游 MCP Client 封装
管理单个上游 MCP Server 的连接、重连、健康检查
"""
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class UpstreamStatus(Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ErrorKind(str, Enum):
    """网关级统一错误类别 (跨上游归一)。

    各上游 client 的细分 ``error_detail.error_type`` 字符串不同
    (mootdx2/thsdk/tdx_quant 各自一套枚举), router 经 :func:`classify_error_kind`
    归一到本枚举, 以便:
      - 产出准确的对外文案 (参数错 vs 服务不可用, 而不是笼统的
        "All upstream failed");
      - 前端错误跟踪页按类别筛选 / 着色;
      - 调用方判断是否值得重试 (``retryable``)。
    """

    INVALID_REQUEST = "invalid_request"          # 参数/代码/品种/鉴权错, 不可重试
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"  # 上游故障/未就绪, 可重试
    TIMEOUT = "timeout"                          # 调用超时, 可重试
    RATE_LIMITED = "rate_limited"                # 触发限流, 稍后重试
    NO_DATA = "no_data"                          # 查询成功但无数据 (业务态, 非错误)
    INTERNAL_ERROR = "internal_error"            # 未预期异常, 不可重试

    @property
    def retryable(self) -> bool:
        return self in (
            ErrorKind.UPSTREAM_UNAVAILABLE,
            ErrorKind.TIMEOUT,
            ErrorKind.RATE_LIMITED,
        )


# 各上游细分 error_type → canonical ErrorKind。
# 未列出的一律回落 INTERNAL_ERROR (保守, 不当成可重试)。
_ERROR_TYPE_TO_KIND: dict[str, "ErrorKind"] = {
    # mootdx2 MooTDXErrorType
    "invalid_param": ErrorKind.INVALID_REQUEST,
    "invalid_symbol": ErrorKind.INVALID_REQUEST,
    "stock_not_found": ErrorKind.INVALID_REQUEST,
    "sector_not_found": ErrorKind.INVALID_REQUEST,
    "no_data": ErrorKind.NO_DATA,
    "rate_limited": ErrorKind.RATE_LIMITED,
    "timeout_error": ErrorKind.TIMEOUT,
    "timeout": ErrorKind.TIMEOUT,
    "connection_error": ErrorKind.UPSTREAM_UNAVAILABLE,
    "server_unavailable": ErrorKind.UPSTREAM_UNAVAILABLE,
    "tdx_not_installed": ErrorKind.UPSTREAM_UNAVAILABLE,
    "tdx_sector_file_missing": ErrorKind.UPSTREAM_UNAVAILABLE,
    "tdx_custom_sector_unavailable": ErrorKind.UPSTREAM_UNAVAILABLE,
    # thsdk ThsdkErrorType
    "not_authenticated": ErrorKind.INVALID_REQUEST,
    "auth_failed": ErrorKind.INVALID_REQUEST,
    "invalid_request": ErrorKind.INVALID_REQUEST,
    "init_failed": ErrorKind.UPSTREAM_UNAVAILABLE,
    "api_error": ErrorKind.UPSTREAM_UNAVAILABLE,
    "unknown": ErrorKind.INTERNAL_ERROR,
    "internal_error": ErrorKind.INTERNAL_ERROR,
}


def classify_error_kind(error_type: str | None) -> "ErrorKind":
    """上游细分 error_type → canonical ErrorKind。

    None / 未识别值回落 :attr:`ErrorKind.INTERNAL_ERROR` (保守)。
    """
    if not error_type:
        return ErrorKind.INTERNAL_ERROR
    return _ERROR_TYPE_TO_KIND.get(
        str(error_type), ErrorKind.INTERNAL_ERROR
    )


@dataclass
class UpstreamConfig:
    name: str
    enabled: bool
    package: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    health_check_url: str | None = None
    health_check_interval: int = 30
    timeout_seconds: int = 30
    max_retry: int = 3
    backoff_base: int = 2


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    # 失败路径：错误文本（进信封 error）。成功路径默认对消费者不可见；
    # 仅当配合 error_detail.error_type="no_data"（success+data=None 业务态）
    # 或 error_detail.warn 时，文本才会提升为信封顶层 warn 透传给用户。
    error: str | None = None
    error_detail: dict | None = None  # 详细错误信息 {"error_type": "...", "message": "...", "recoverable": true/false}
    source: str | None = None
    duration_ms: int = 0

    @property
    def error_kind(self) -> str | None:
        """网关级统一错误类别，从 ``error_detail.error_type`` 即时归一。

        派生属性而非构造字段: 各上游 client 的失败构造点很多，让每个点都显式
        填 error_kind 容易漏; 统一从已有的 error_type 推导则零改动、不漂移。
        成功路径 (error_detail 无 error_type) 返回 None。
        """
        if not self.error_detail:
            return None
        return classify_error_kind(self.error_detail.get("error_type")).value


@dataclass
class UpstreamError:
    name: str
    message: str
    timestamp: float


class UpstreamClient:
    """单个上游 MCP Server 的客户端封装"""

    def __init__(self, config: UpstreamConfig):
        self.config = config
        self.name = config.name
        self._process: asyncio.subprocess.Process | None = None
        self._status = UpstreamStatus.UNKNOWN
        self._last_error: UpstreamError | None = None
        self._retry_count = 0
        self._lock = asyncio.Lock()
        # M7 (round 9 audit): 后台 drain 子进程 stderr, 防 64KB 管道自锁。
        self._stderr_drain_task: asyncio.Task | None = None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def last_error(self) -> UpstreamError | None:
        return self._last_error

    @property
    def is_available(self) -> bool:
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    def _resolve_npx_path(self) -> str:
        """解析 npx 可执行文件路径"""
        for name in ["npx.cmd", "npx"]:
            npx_path = shutil.which(name)
            if npx_path:
                return npx_path
        raise FileNotFoundError("npx not found in PATH")

    async def start(self) -> bool:
        """启动上游 MCP Server 进程"""
        async with self._lock:
            if not self.config.enabled:
                self._status = UpstreamStatus.UNAVAILABLE
                return False

            if self._process and self._process.returncode is None:
                return True

            try:
                # 构建命令
                cmd = self.config.command.copy()
                if cmd and "npx" in cmd[0]:
                    cmd[0] = self._resolve_npx_path()

                # 构建环境变量
                env = {
                    **subprocess.os.environ,
                    "PYTHONIOENCODING": "utf-8",
                }
                env.update(self.config.env)

                # Windows: 解析 .cmd 文件的完整路径
                if sys.platform == "win32" and cmd[0].endswith(".cmd"):
                    resolved_cmd0 = shutil.which(cmd[0])
                    if resolved_cmd0:
                        cmd[0] = resolved_cmd0

                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )

                # 等待进程启动
                await asyncio.sleep(0.5)

                if self._process.returncode is not None:
                    # 进程立即退出
                    stdout, stderr = await self._process.communicate()
                    error_msg = stderr.decode("utf-8", errors="replace") if stderr else "Process exited immediately"
                    self._last_error = UpstreamError(self.name, error_msg, time.time())
                    self._status = UpstreamStatus.UNAVAILABLE
                    logger.error(f"[{self.name}] Process failed to start: {error_msg}")
                    return False

                self._status = UpstreamStatus.HEALTHY
                self._retry_count = 0
                logger.info(f"[{self.name}] Started successfully")
                # M7 (round 9 audit): 后台 drain stderr, 防止子进程写满 64KB
                # 管道后在 write() 阻塞、整个上游假死。readline 到 EOF, 每行 log。
                if self._process and self._process.stderr:
                    self._stderr_drain_task = asyncio.create_task(
                        self._drain_stderr(),
                        name=f"{self.name}-stderr-drain",
                    )
                return True

            except Exception as e:
                error_msg = str(e)
                self._last_error = UpstreamError(self.name, error_msg, time.time())
                self._status = UpstreamStatus.UNAVAILABLE
                logger.error(f"[{self.name}] Failed to start: {error_msg}")
                return False

    async def stop(self):
        """停止上游进程"""
        async with self._lock:
            # M7 (round 9 audit): 先停 drain 任务, 再 terminate 进程,
            # 防止 drain 还在 readline 已关闭的 stderr。
            drain = self._stderr_drain_task
            if drain is not None and not drain.done():
                drain.cancel()
                try:
                    await drain
                except (asyncio.CancelledError, Exception):
                    pass
                self._stderr_drain_task = None
            if self._process:
                try:
                    self._process.terminate()
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
                finally:
                    self._process = None
                    self._status = UpstreamStatus.UNAVAILABLE

    async def _drain_stderr(self):
        """后台消费子进程 stderr 到 EOF, 防 64KB 管道自锁。

        子进程 (stdio MCP server) 往 stderr 写诊断信息, 若从不 drain,
        写满 OS 管道缓冲 (典型 64KB) 后会在 write() 永久阻塞, 上游假死。
        """
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                logger.info(
                    f"[{self.name}] stderr: "
                    f"{line.decode('utf-8', errors='replace').rstrip()}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[{self.name}] stderr drain ended: {e}")

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """通过 MCP JSON-RPC 调用工具"""
        start_time = time.time()

        if not self._process or self._process.returncode is not None:
            return ToolResult(
                success=False,
                error="Process not running",
                source=self.name,
                duration_ms=0
            )

        try:
            request = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }

            request_json = json.dumps(request) + "\n"
            self._process.stdin.write(request_json.encode("utf-8"))
            await self._process.stdin.drain()

            # 读取响应
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=self.config.timeout_seconds
            )

            if not response_line:
                return ToolResult(
                    success=False,
                    error="No response from process",
                    source=self.name,
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            response = json.loads(response_line.decode("utf-8"))
            duration_ms = int((time.time() - start_time) * 1000)

            if "error" in response:
                return ToolResult(
                    success=False,
                    error=str(response["error"]),
                    source=self.name,
                    duration_ms=duration_ms
                )

            return ToolResult(
                success=True,
                data=response.get("result"),
                source=self.name,
                duration_ms=duration_ms
            )

        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                success=False,
                error="Request timeout",
                source=self.name,
                duration_ms=duration_ms
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                success=False,
                error=str(e),
                source=self.name,
                duration_ms=duration_ms
            )

    async def list_tools(self) -> list[dict]:
        """列出可用工具"""
        if not self._process or self._process.returncode is not None:
            return []

        try:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }
            request_json = json.dumps(request) + "\n"
            self._process.stdin.write(request_json.encode("utf-8"))
            await self._process.stdin.drain()

            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=10
            )

            response = json.loads(response_line.decode("utf-8"))
            if "result" in response and "tools" in response["result"]:
                return response["result"]["tools"]
            return []

        except Exception as e:
            logger.error(f"[{self.name}] List tools failed: {e}")
            return []

    async def health_check(self) -> bool:
        """健康检查"""
        if not self._process:
            self._status = UpstreamStatus.UNAVAILABLE
            return False

        if self._process.returncode is not None:
            self._status = UpstreamStatus.UNAVAILABLE
            return False

        self._status = UpstreamStatus.HEALTHY
        return True
