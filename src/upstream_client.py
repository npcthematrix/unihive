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
    error: str | None = None
    source: str | None = None
    duration_ms: int = 0


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
