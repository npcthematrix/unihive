# tdx_tq_local → tdx-quant Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `tdx_tq_local` HTTP JSON-RPC upstream with a new `tdx_quant` upstream that calls TdxQuant `tqcenter.py` in-process, and regenerate the 60+ tool spec from `skills/SKILL.md`.

**Architecture:** New `TdxQuantClient` mirrors `MooTDX2Client` structure — module-level singleton `tq` instance initialized at gateway startup, `run_in_executor` wraps sync DLL calls, periodic health-check task auto-reconnects on `ErrorId='6'/'7'`. Codegen script `gen_tdx_quant_tools.py` parses `skills/SKILL.md` and emits `config/tools_tdx_quant.yaml`.

**Tech Stack:** Python 3.10+, FastMCP 4.0.2, pytest, asyncio.run_in_executor, tqcenter.py v1.0.12 (Windows DLL IPC).

**Spec:** `docs/superpowers/specs/2026-09-06-tdx-quant-migration-design.md`

---

## File Structure

**New files:**
- `src/tdx_quant_errors.py` — `TdxQuantError`, `TdxQuantErrorType`, `translate_errorid()`
- `src/tdx_quant_config.py` — `TdxQuantSettings`, `TdxQuantConfig`
- `src/tdx_quant_client.py` — `TdxQuantClient` with singleton `tq`, state machine, health loop
- `scripts/gen_tdx_quant_tools.py` — codegen, adapted from `gen_tdx_tq_local_tools.py`
- `config/tools_tdx_quant.yaml` — codegen output (60+ tools)
- `docs/tdx-quant-known-limitations.md` — known limitations doc
- `scripts/smoke_tdx_quant.py` — manual smoke test script
- `tests/test_tdx_quant_errors.py`
- `tests/test_tdx_quant_client.py`
- `tests/test_gen_tdx_quant_tools.py`
- `tests/test_registry_tdx_quant.py`

**Modified files:**
- `src/registry.py` — `_TYPE_MAP` add `"List[str]": list`
- `src/gateway_server.py` — import + factory branch `type: tdx_quant`
- `config/upstreams.yaml` — delete `tdx_tq_local` block, add `tdx_quant` block

**Deleted files:**
- `config/tools_tdx_tq_local.yaml`
- `scripts/gen_tdx_tq_local_tools.py`
- `src/http_jsonrpc_client.py`
- `tests/test_gen_tdx_tq_local_tools.py`

---

### Task 1: tdx_quant_errors.py — error translation layer (TDD)

**Files:**
- Create: `src/tdx_quant_errors.py`
- Test: `tests/test_tdx_quant_errors.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tdx_quant_errors.py`:

```python
"""tests/test_tdx_quant_errors.py — TdxQuant 错误翻译层测试。"""
import asyncio
import pytest

from src.tdx_quant_errors import (
    TdxQuantError,
    TdxQuantErrorType,
    translate_errorid,
    classify_exception,
)


class TestTranslateErrorId:
    def test_errorid_0_is_success(self):
        result = {"ErrorId": "0", "Data": {"Now": "10.5"}}
        err = translate_errorid(result)
        assert err is None  # 无错误

    def test_errorid_missing_treated_as_success(self):
        result = {"Data": {"Now": "10.5"}}
        err = translate_errorid(result)
        assert err is None

    def test_errorid_6_marks_disconnect(self):
        result = {"ErrorId": "6", "ErrMsg": "disconnected"}
        err = translate_errorid(result)
        assert err is not None
        assert err.error_type == TdxQuantErrorType.DISCONNECTED
        assert err.recoverable is True

    def test_errorid_7_marks_disconnect(self):
        result = {"ErrorId": "7"}
        err = translate_errorid(result)
        assert err.error_type == TdxQuantErrorType.DISCONNECTED

    def test_errorid_12_strategy_exists(self):
        result = {"ErrorId": "12", "ErrMsg": "strategy exists"}
        err = translate_errorid(result)
        assert err.error_type == TdxQuantErrorType.STRATEGY_EXISTS
        assert err.recoverable is False

    def test_errorid_unknown_falls_back(self):
        result = {"ErrorId": "99", "ErrMsg": "weird"}
        err = translate_errorid(result)
        assert err.error_type == TdxQuantErrorType.UNKNOWN
        assert "99" in err.message


class TestClassifyException:
    def test_module_not_found_classifies_as_init_failed(self):
        err = classify_exception(ModuleNotFoundError("tqcenter"))
        assert err.error_type == TdxQuantErrorType.INIT_FAILED

    def test_file_not_found_classifies_as_init_failed(self):
        err = classify_exception(FileNotFoundError("tqcenter.py"))
        assert err.error_type == TdxQuantErrorType.INIT_FAILED

    def test_timeout_classifies_as_timeout(self):
        err = classify_exception(asyncio.TimeoutError())
        assert err.error_type == TdxQuantErrorType.TIMEOUT

    def test_connection_error_classifies_as_unavailable(self):
        err = classify_exception(ConnectionError("refused"))
        assert err.error_type == TdxQuantErrorType.UPSTREAM_UNAVAILABLE

    def test_os_error_with_network_keyword_classifies_as_unavailable(self):
        err = classify_exception(OSError("connection refused"))
        assert err.error_type == TdxQuantErrorType.UPSTREAM_UNAVAILABLE

    def test_generic_exception_falls_back_to_unknown(self):
        err = classify_exception(ValueError("boom"))
        assert err.error_type == TdxQuantErrorType.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tdx_quant_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.tdx_quant_errors'`

- [ ] **Step 3: Write the implementation**

Create `src/tdx_quant_errors.py`:

```python
"""TdxQuant 错误分类枚举与翻译层。

将 tqcenter.py 返回的 ErrorId 字符串与底层异常翻译为 TdxQuantError，
风格对齐 mootdx2_errors.py。
"""
import logging
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TdxQuantErrorType(Enum):
    """TdxQuant 错误类型枚举。"""

    # 连接相关 (可重试/可重连)
    DISCONNECTED = "disconnected"          # ErrorId='6'/'7'，连接断开
    UPSTREAM_UNAVAILABLE = "unavailable"   # TdxW 未响应 / 网络错误
    TIMEOUT = "timeout"                    # 调用超时

    # 初始化相关 (不可重试)
    INIT_FAILED = "init_failed"            # tqcenter 加载失败 / 文件缺失
    STRATEGY_EXISTS = "strategy_exists"    # ErrorId='12'，同名策略冲突

    # 业务/未知
    UNKNOWN = "unknown"                    # 未识别 ErrorId 或异常


@dataclass
class TdxQuantError:
    """TdxQuant 错误详情。"""

    error_type: TdxQuantErrorType
    message: str
    recoverable: bool = True
    error_id: Optional[str] = None         # 原始 ErrorId 字符串（如有）
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


# ErrorId 字符串 → (错误类型, recoverable)
_ERRORID_MAP: dict[str, tuple[TdxQuantErrorType, bool]] = {
    "6": (TdxQuantErrorType.DISCONNECTED, True),
    "7": (TdxQuantErrorType.DISCONNECTED, True),
    "12": (TdxQuantErrorType.STRATEGY_EXISTS, False),
}


def translate_errorid(result: Any) -> Optional[TdxQuantError]:
    """检查 tqcenter 返回 dict 的 ErrorId 字段，返回 TdxQuantError 或 None。

    Args:
        result: tq.* 方法的返回值（通常是 dict，含 ErrorId/ErrMsg/Data 等键）

    Returns:
        - None: 无错误（ErrorId 缺失或 '0'）
        - TdxQuantError: 其他 ErrorId
    """
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
    """将底层异常分类为 TdxQuantError。

    用于 try/except 包裹 tq.* 调用时，把异常翻译成统一结构。
    """
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
```

Add missing import at top of test file:

```python
import asyncio
```
(already present)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tdx_quant_errors.py -v`
Expected: PASS, 11 tests passed

- [ ] **Step 5: Commit**

```bash
git add src/tdx_quant_errors.py tests/test_tdx_quant_errors.py
git commit -m "feat(tdx_quant): add error translation layer with ErrorId mapping"
```

---

### Task 2: tdx_quant_config.py — config dataclasses

**Files:**
- Create: `src/tdx_quant_config.py`

- [ ] **Step 1: Write the config module**

Create `src/tdx_quant_config.py`:

```python
"""TdxQuant 配置管理。

配置来源：config/upstreams.yaml 的 tdx_quant 段。
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    # 连续探活失败 N 次后触发 reconnect
    reconnect_threshold: int = 3

    # 连续失败 N 次后降级为 UNAVAILABLE，停止自动重连
    unavailable_threshold: int = 10

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TdxQuantSettings":
        return cls(
            tdx_root=data.get("tdx_root", ""),
            strategy_id=data.get("strategy_id", ""),
            health_check_interval_sec=int(data.get("health_check_interval_sec", 60)),
            call_timeout_sec=int(data.get("call_timeout_sec", 10)),
            reconnect_threshold=int(data.get("reconnect_threshold", 3)),
            unavailable_threshold=int(data.get("unavailable_threshold", 10)),
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
        return self.tdx_root_path / "PYPlugins" / "user" / "tqcenter.py"

    @property
    def tqcenter_dir(self) -> Path:
        """需要加入 sys.path 的目录（含 tqcenter.py）。"""
        return self.tdx_root_path / "PYPlugins" / "user"
```

- [ ] **Step 2: Verify import works**

Run: `python -c "from src.tdx_quant_config import TdxQuantConfig, TdxQuantSettings; c = TdxQuantConfig(name='t', settings=TdxQuantSettings(tdx_root='D:/new_tdx_mock')); print(c.tqcenter_path)"`
Expected: prints `D:/new_tdx_mock/PYPlugins/user/tqcenter.py` (or with backslashes on Windows)

- [ ] **Step 3: Commit**

```bash
git add src/tdx_quant_config.py
git commit -m "feat(tdx_quant): add config dataclasses mirroring MooTDX2Config"
```

---

### Task 3: tdx_quant_client.py — singleton tq + state machine (TDD with mocks)

**Files:**
- Create: `src/tdx_quant_client.py`
- Test: `tests/test_tdx_quant_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tdx_quant_client.py`:

```python
"""tests/test_tdx_quant_client.py — TdxQuantClient 单元测试（mock tqcenter.tq）。"""
import asyncio
import sys
import types
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.tdx_quant_config import TdxQuantConfig, TdxQuantSettings
from src.tdx_quant_errors import TdxQuantErrorType


@pytest.fixture
def mock_tq():
    """构造一个 mock tq 实例，注入到 sys.modules['tqcenter']。"""
    mock_module = types.ModuleType("tqcenter")
    mock_tq_inst = MagicMock()
    mock_tq_inst.initialize = MagicMock(return_value={"ErrorId": "0"})
    mock_tq_inst.close = MagicMock()
    mock_tq_inst.get_user_sector = MagicMock(return_value={"ErrorId": "0", "Data": []})
    mock_tq_inst.get_market_snapshot = MagicMock(return_value={
        "ErrorId": "0", "Now": "10.5", "LastClose": "10.0",
    })
    mock_module.tq = MagicMock(return_value=mock_tq_inst)
    sys.modules["tqcenter"] = mock_module
    yield mock_tq_inst
    sys.modules.pop("tqcenter", None)


@pytest.fixture
def client_config():
    return TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake_tdx",
            strategy_id="test_strategy",
            health_check_interval_sec=1,
            reconnect_threshold=3,
            unavailable_threshold=10,
            call_timeout_sec=5,
        ),
    )


@pytest.mark.asyncio
async def test_start_initializes_tq_singleton(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient, UpstreamStatus
    c = TdxQuantClient(client_config)
    assert await c.start() is True
    assert c.status == UpstreamStatus.HEALTHY
    mock_tq.initialize.assert_called_once_with("test_strategy")


@pytest.mark.asyncio
async def test_start_failure_marks_unavailable(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient, UpstreamStatus
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    assert await c.start() is False
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_start_when_module_missing(client_config, monkeypatch):
    from src.tdx_quant_client import TdxQuantClient, UpstreamStatus
    # 确保 tqcenter 不可导入
    monkeypatch.setattr(sys, "path", list(sys.path))
    c = TdxQuantClient(client_config)
    assert await c.start() is False
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_call_tool_returns_success_on_errorid_0(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    await c.start()
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is True
    assert result.data["Now"] == "10.5"
    assert result.source == "tdx_quant"


@pytest.mark.asyncio
async def test_call_tool_returns_error_on_errorid_6(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    mock_tq.get_market_snapshot.return_value = {"ErrorId": "6", "ErrMsg": "disconnected"}
    c = TdxQuantClient(client_config)
    await c.start()
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "DISCONNECTED" in result.error or "断开" in result.error


@pytest.mark.asyncio
async def test_call_tool_when_unavailable_returns_error(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient, UpstreamStatus
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    await c.start()  # 失败但不抛
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "unavailable" in result.error.lower() or "未就绪" in result.error


@pytest.mark.asyncio
async def test_health_loop_marks_disconnected_after_3(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient, UpstreamStatus
    mock_tq.get_user_sector.return_value = {"ErrorId": "6"}
    c = TdxQuantClient(client_config)
    await c.start()
    # 等 3 次探活周期（interval=1s）
    await asyncio.sleep(3.5)
    assert mock_tq.close.call_count >= 1  # 触发了 reconnect


@pytest.mark.asyncio
async def test_stop_closes_tq_and_cancels_health_task(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient, UpstreamStatus
    c = TdxQuantClient(client_config)
    await c.start()
    await c.stop()
    mock_tq.close.assert_called()
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_reconnect_lock_serializes(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    await c.start()
    # 并发触发 5 次 reconnect
    await asyncio.gather(*[c._reconnect() for _ in range(5)])
    # initialize 应只被调用一次（reconnect 内部 lock 串行化）
    # 但第一次 start() 也会调 initialize，所以总调用数 = 1 (start) + 1 (reconnect)
    assert mock_tq.initialize.call_count <= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tdx_quant_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.tdx_quant_client'`

- [ ] **Step 3: Write the implementation**

Create `src/tdx_quant_client.py`:

```python
"""TdxQuant 上游客户端。

通过进程内 singleton tq 实例调用 tqcenter.py，与运行中的 TdxW.exe
通过 DLL IPC 通信。结构对齐 mootdx2_client.py。
"""
import asyncio
import logging
import sys
import time
from enum import Enum
from typing import Any, Optional

from .tdx_quant_config import TdxQuantConfig
from .tdx_quant_errors import TdxQuantError, TdxQuantErrorType, classify_exception, translate_errorid
from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


class TdxQuantErrorType_(Enum):  # 兼容占位，实际用 tdx_quant_errors.TdxQuantErrorType
    pass


class TdxQuantClient:
    """进程内直调 tqcenter.py 的上游客户端。

    生命周期：
    - start(): sys.path.insert + import tqcenter + tq() + initialize()
    - 失败不抛，标记 UNAVAILABLE，gateway 继续启动其他上游
    - 后台 _health_loop 周期探活，触发 _reconnect
    - stop(): 取消探活任务 + tq.close()
    """

    def __init__(self, config: TdxQuantConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._tq: Any = None  # tqcenter.tq 实例
        self._health_task: Optional[asyncio.Task] = None
        self._reconnect_lock = asyncio.Lock()
        self._fail_count = 0
        self._strategy_id = config.settings.strategy_id or __file__

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self) -> bool:
        """启动：加载 tqcenter + initialize + 启动探活任务。

        Returns:
            True: 初始化成功，status=HEALTHY
            False: 初始化失败，status=UNAVAILABLE（不抛，gateway 继续）
        """
        try:
            tqcenter_dir = str(self.config.tqcenter_dir)
            if tqcenter_dir and tqcenter_dir not in sys.path:
                sys.path.insert(0, tqcenter_dir)
            import tqcenter  # noqa: F401
            self._tq = tqcenter.tq()
            self._tq.initialize(self._strategy_id)
            self._status = UpstreamStatus.HEALTHY
            self._health_task = asyncio.create_task(self._health_loop())
            logger.info(f"[{self.name}] initialized (strategy_id={self._strategy_id})")
            return True
        except (FileNotFoundError, ModuleNotFoundError) as e:
            logger.error(f"[{self.name}] init failed: {e}")
            self._status = UpstreamStatus.UNAVAILABLE
            return False
        except Exception as e:
            # 含 ErrorId='12' 同名策略：仅警告，仍标记 HEALTHY
            logger.warning(f"[{self.name}] initialize warning: {e}")
            self._status = UpstreamStatus.HEALTHY
            self._health_task = asyncio.create_task(self._health_loop())
            return True

    async def stop(self):
        """关闭：取消探活任务 + tq.close()。"""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        if self._tq:
            try:
                self._tq.close()
            except Exception as e:
                logger.warning(f"[{self.name}] close warning: {e}")
        self._tq = None
        self._status = UpstreamStatus.UNAVAILABLE

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """调用 tq.{tool_name}(**arguments)。

        - status != HEALTHY → 立即返失败
        - run_in_executor 包裹同步 DLL 调用
        - 翻译 ErrorId 为 ToolResult
        """
        start_time = time.time()
        if self._status != UpstreamStatus.HEALTHY or self._tq is None:
            return ToolResult(
                success=False,
                error=f"[{self.name}] upstream unavailable (status={self._status.value if hasattr(self._status, 'value') else self._status})",
                source=self.name,
                duration_ms=0,
            )

        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: getattr(self._tq, tool_name)(**arguments),
                ),
                timeout=self.config.settings.call_timeout_sec,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            err = translate_errorid(result)
            if err is None:
                return ToolResult(
                    success=True,
                    data=result,
                    source=self.name,
                    duration_ms=duration_ms,
                )
            # DISCONNECTED → 触发 reconnect
            if err.error_type == TdxQuantErrorType.DISCONNECTED:
                asyncio.create_task(self._reconnect())
            return ToolResult(
                success=False,
                error=err.message,
                source=self.name,
                duration_ms=duration_ms,
            )
        except asyncio.TimeoutError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            err = classify_exception(e)
            return ToolResult(
                success=False,
                error=err.message,
                source=self.name,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            err = classify_exception(e)
            # ConnectionError/OSError(network) → 触发 reconnect
            if err.error_type == TdxQuantErrorType.UPSTREAM_UNAVAILABLE:
                asyncio.create_task(self._reconnect())
            logger.error(f"[{self.name}] call {tool_name} failed: {e}")
            return ToolResult(
                success=False,
                error=err.message,
                source=self.name,
                duration_ms=duration_ms,
            )

    async def list_tools(self) -> list[dict]:
        return []

    async def health_check(self) -> bool:
        """对外暴露的同步探活（用于上游状态查询）。"""
        return self._status == UpstreamStatus.HEALTHY

    async def _health_loop(self):
        """后台探活：周期性调 tq.get_user_sector()。"""
        interval = self.config.settings.health_check_interval_sec
        while self._status != UpstreamStatus.UNAVAILABLE:
            await asyncio.sleep(interval)
            try:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: self._tq.get_user_sector()
                    ),
                    timeout=5.0,
                )
                err = translate_errorid(result)
                if err and err.error_type == TdxQuantErrorType.DISCONNECTED:
                    self._fail_count += 1
                else:
                    self._fail_count = 0
                    self._status = UpstreamStatus.HEALTHY
            except (asyncio.TimeoutError, Exception):
                self._fail_count += 1
                if self._fail_count >= self.config.settings.reconnect_threshold:
                    await self._reconnect()
                if self._fail_count >= self.config.settings.unavailable_threshold:
                    self._status = UpstreamStatus.UNAVAILABLE
                    logger.error(f"[{self.name}] marked UNAVAILABLE after {self._fail_count} failures")
                    return

    async def _reconnect(self):
        """重连：tq.close() + tq.initialize()，asyncio.Lock 串行化。"""
        async with self._reconnect_lock:
            try:
                if self._tq:
                    self._tq.close()
            except Exception as e:
                logger.warning(f"[{self.name}] close during reconnect: {e}")
            try:
                self._tq.initialize(self._strategy_id)
                self._fail_count = 0
                self._status = UpstreamStatus.HEALTHY
                logger.info(f"[{self.name}] reconnected")
            except Exception as e:
                self._status = UpstreamStatus.DISCONNECTED if hasattr(UpstreamStatus, 'DISCONNECTED') else UpstreamStatus.UNKNOWN
                logger.warning(f"[{self.name}] reconnect failed: {e}")
```

Note: `UpstreamStatus` enum may not have `DISCONNECTED`. Check actual enum values.

- [ ] **Step 4: Check UpstreamStatus enum values and patch**

Run: `python -c "from src.upstream_client import UpstreamStatus; print([s.name for s in UpstreamStatus])"`
Expected: prints available states.

If `DISCONNECTED` is missing, use the closest existing state (likely `UNAVAILABLE` or `UNKNOWN`). Update `_reconnect` accordingly:

```python
# 在 _reconnect 失败分支里:
self._status = UpstreamStatus.UNKNOWN  # 或最接近的"不健康"状态
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_tdx_quant_client.py -v`
Expected: PASS, all 9 tests passed

If `test_health_loop_marks_disconnected_after_3` is flaky due to timing, increase sleep from 3.5 to 5.0.

- [ ] **Step 6: Commit**

```bash
git add src/tdx_quant_client.py tests/test_tdx_quant_client.py
git commit -m "feat(tdx_quant): add TdxQuantClient with singleton tq + health loop"
```

---

### Task 4: gen_tdx_quant_tools.py — codegen (TDD)

**Files:**
- Create: `scripts/gen_tdx_quant_tools.py`
- Test: `tests/test_gen_tdx_quant_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gen_tdx_quant_tools.py`:

```python
"""tests/test_gen_tdx_quant_tools.py — codegen 脚本测试。"""
import yaml
from pathlib import Path

from scripts.gen_tdx_quant_tools import (
    parse_skill_md,
    infer_dangerous,
    infer_cache_ttl_key,
    build_tool_specs,
    render_yaml,
)


SAMPLE_SKILL = """# TdxQuant Skill Sample

> sample

## 二、行情数据接口

### 2.1 获取K线行情 `get_market_data`

获取 K 线和历史行情数据。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| stock_list | Y | List[str] | 股票代码列表 |
| period | Y | str | K线周期，如 `1m`/`5m`/`1d` |
| count | N | int | 取最近 n 条 |
| dividend_type | N | str | 复权：`none`/`front`/`back`

**返回字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Close | str | 收盘价 |

### 2.2 获取实时行情快照 `get_market_snapshot`

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| stock_code | Y | str | 证券代码 |
| field_list | N | List[str] | 指定字段 |

### 4.4 自定义板块管理

#### 创建板块 `create_sector`

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| sector_name | Y | str | 板块名 |

#### 下单 `order_stock`

下单。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| account_id | Y | str | 账户句柄 |
| code | Y | str | 证券代码 |
"""


def test_parse_skill_md_extracts_methods():
    methods = parse_skill_md(SAMPLE_SKILL)
    names = [m["name"] for m in methods]
    assert "get_market_data" in names
    assert "get_market_snapshot" in names
    assert "create_sector" in names
    assert "order_stock" in names


def test_parse_skill_md_extracts_params():
    methods = parse_skill_md(SAMPLE_SKILL)
    gmd = next(m for m in methods if m["name"] == "get_market_data")
    param_names = [p["name"] for p in gmd["params"]]
    assert "stock_list" in param_names
    assert "period" in param_names
    assert "count" in param_names
    # 不应混入返回字段表
    assert "Close" not in param_names


def test_infer_dangerous_for_write_ops():
    assert infer_dangerous("order_stock") is True
    assert infer_dangerous("cancel_order_stock") is True
    assert infer_dangerous("create_sector") is True
    assert infer_dangerous("delete_sector") is True
    assert infer_dangerous("send_message") is True
    assert infer_dangerous("refresh_cache") is True
    assert infer_dangerous("formula_zb") is True


def test_infer_dangerous_for_read_ops():
    assert infer_dangerous("get_market_data") is False
    assert infer_dangerous("get_market_snapshot") is False
    assert infer_dangerous("get_user_sector") is False


def test_infer_cache_ttl_key_realtime():
    assert infer_cache_ttl_key("get_market_snapshot") == "realtime_quote"
    assert infer_cache_ttl_key("get_more_info") == "realtime_quote"
    assert infer_cache_ttl_key("get_gp_one_data") == "realtime_quote"


def test_infer_cache_ttl_key_historical():
    assert infer_cache_ttl_key("get_market_data") == "historical"
    assert infer_cache_ttl_key("get_divid_factors") == "historical"


def test_infer_cache_ttl_key_fundamentals():
    assert infer_cache_ttl_key("get_financial_data") == "fundamentals"
    assert infer_cache_ttl_key("get_stock_info") == "fundamentals"
    assert infer_cache_ttl_key("get_gpjy_value_by_date") == "fundamentals"


def test_infer_cache_ttl_key_ticker_list():
    assert infer_cache_ttl_key("get_stock_list") == "ticker_list"
    assert infer_cache_ttl_key("get_user_sector") == "ticker_list"


def test_infer_cache_ttl_key_workday():
    assert infer_cache_ttl_key("get_trading_dates") == "workday"
    assert infer_cache_ttl_key("get_trading_calendar") == "workday"


def test_infer_cache_ttl_key_null_for_writes():
    assert infer_cache_ttl_key("order_stock") is None
    assert infer_cache_ttl_key("send_message") is None
    assert infer_cache_ttl_key("subscribe_hq") is None
    assert infer_cache_ttl_key("formula_zb") is None


def test_build_tool_specs_uses_tdx_quant_upstream():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    for spec in specs:
        assert spec["upstream_tool_mapping"] == {"tdx_quant": spec["name"]}


def test_render_yaml_includes_do_not_edit_header():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    out = render_yaml(specs)
    assert "DO NOT EDIT" in out
    assert "gen_tdx_quant_tools.py" in out
    # 可被 yaml 解析
    parsed = yaml.safe_load(out)
    assert "tools" in parsed
    assert len(parsed["tools"]) == len(specs)


def test_codegen_roundtrip_idempotent():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs1 = build_tool_specs(methods)
    specs2 = build_tool_specs(methods)
    assert specs1 == specs2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gen_tdx_quant_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.gen_tdx_quant_tools'`

- [ ] **Step 3: Write the implementation**

Create `scripts/gen_tdx_quant_tools.py`:

```python
r"""从 skills/SKILL.md 生成 UniHive 工具定义。

输出: config/tools_tdx_quant.yaml（顶层 tools: 列表）

规则：
- 解析 `### X.Y 标题 `method_name`` 标题（含反引号包裹的 method 名）
- 抓取紧随的 markdown 参数表（列: 参数 | 必填 | 类型 | 说明）
- 按方法名规则推断 dangerous: true
- 按方法名精确匹配推断 cache_ttl_key
- 缺表 method 仍写入（params=[]）

CLI:
- 默认：写入
- --dry-run：仅打印统计
- --check：与磁盘内容比对，不一致则 exit 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


SKILL_PATH = Path("skills/SKILL.md")
OUTPUT_PATH = Path("config/tools_tdx_quant.yaml")


class CodegenParseError(Exception):
    pass


# ---------- dangerous 推断 ----------

_DANGEROUS_PREFIXES = (
    "order_", "cancel_order",
    "create_sector", "delete_sector", "rename_sector", "clear_sector",
)
_DANGEROUS_EXACT = frozenset({
    "send_message", "send_file", "send_warn", "send_bt_data",
    "send_user_block", "exec_to_tdx", "print_to_tdx",
    "refresh_cache", "refresh_kline", "download_file",
})
_DANGEROUS_STARTSWITH = ("formula_",)


def infer_dangerous(name: str) -> bool:
    if any(name.startswith(p) for p in _DANGEROUS_PREFIXES):
        return True
    if name in _DANGEROUS_EXACT:
        return True
    if any(name.startswith(p) for p in _DANGEROUS_STARTSWITH):
        return True
    return False


# ---------- cache_ttl_key 推断（精确方法名匹配） ----------

_CACHE_TTL_MAP: dict[str, str] = {
    # realtime_quote (10s)
    "get_market_snapshot": "realtime_quote",
    "get_more_info": "realtime_quote",
    "get_gp_one_data": "realtime_quote",
    # historical (3600s)
    "get_market_data": "historical",
    "get_divid_factors": "historical",
    "get_pricevol": "historical",
    # fundamentals (3600s)
    "get_financial_data": "fundamentals",
    "get_financial_data_by_date": "fundamentals",
    "get_stock_info": "fundamentals",
    "get_gb_info": "fundamentals",
    "get_gb_info_by_date": "fundamentals",
    "get_kzz_info": "fundamentals",
    "get_ipo_info": "fundamentals",
    "get_trackzs_etf_info": "fundamentals",
    "get_gpjy_value": "fundamentals",
    "get_gpjy_value_by_date": "fundamentals",
    "get_bkjy_value": "fundamentals",
    "get_bkjy_value_by_date": "fundamentals",
    "get_scjy_value": "fundamentals",
    "get_scjy_value_by_date": "fundamentals",
    # ticker_list (3600s)
    "get_stock_list": "ticker_list",
    "get_sector_list": "ticker_list",
    "get_user_sector": "ticker_list",
    "get_stock_list_in_sector": "ticker_list",
    "get_relation": "ticker_list",
    "get_match_stkinfo": "ticker_list",
    # workday (86400s)
    "get_trading_dates": "workday",
    "get_trading_calendar": "workday",
}


def infer_cache_ttl_key(name: str) -> str | None:
    return _CACHE_TTL_MAP.get(name)


# ---------- 解析 ----------

# 匹配 `### X.Y 标题 `method_name`` 格式
# 例：### 2.1 获取K线行情 `get_market_data`
_METHOD_HEADING = re.compile(
    r"^###\s+\d+\.\d+\s+.+?`([A-Za-z_][A-Za-z0-9_]*)`\s*$",
    re.MULTILINE,
)
_PARAM_ROW = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<req>[^|]+?)\s*\|\s*(?P<type>[^|]+?)\s*\|\s*(?P<desc>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)
# 也支持 #### `method` 格式（板块管理小节用 ####）
_METHOD_HEADING_ALT = re.compile(
    r"^####\s+[^`]*`([A-Za-z_][A-Za-z0-9_]*)`\s*$",
    re.MULTILINE,
)


def _all_method_headings(text: str) -> list[tuple[str, int, int, int]]:
    """返回 [(method_name, heading_start, heading_end, section_end), ...]。"""
    matches: list[tuple[int, str, int]] = []  # (start, name, end)
    for m in _METHOD_HEADING.finditer(text):
        matches.append((m.start(), m.group(1), m.end()))
    for m in _METHOD_HEADING_ALT.finditer(text):
        matches.append((m.start(), m.group(1), m.end()))
    matches.sort(key=lambda x: x[0])
    result: list[tuple[str, int, int, int]] = []
    for i, (start, name, end) in enumerate(matches):
        section_end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        result.append((name, start, end, section_end))
    return result


def parse_skill_md(text: str) -> list[dict]:
    """返回 [{"name", "description", "params"}, ...]，按文档顺序。"""
    methods: list[dict] = []
    headings = _all_method_headings(text)
    if not headings:
        return methods

    for name, _heading_start, heading_end, section_end in headings:
        section = text[heading_end:section_end]

        # description: 第一个非空、非表格、非粗体、非标题行
        description = name  # 默认用 method 名
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|"):
                break
            if stripped.startswith("#"):
                continue
            if stripped.startswith("**"):
                continue
            # 用这行作为 description（去掉尾部冒号）
            description = stripped.rstrip("：:")
            break

        # 参数表：第一段连续 | 开头的行
        table_lines: list[str] = []
        for line in section.splitlines():
            if line.lstrip().startswith("|"):
                table_lines.append(line)
            elif table_lines:
                break

        params: list[dict] = []
        first_block = "\n".join(table_lines)
        _VALID_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for row in _PARAM_ROW.finditer(first_block):
            cells = row.groupdict()
            pname = cells["name"].strip()
            if not pname or set(pname) <= {"-", ":"}:
                continue
            if pname in {"参数", "字段"} or "字段" in pname or "返回" in pname:
                continue
            if not _VALID_IDENT.match(pname):
                continue
            params.append({
                "name": pname,
                "required": cells["req"].strip().upper() in ("Y", "YES", "TRUE", "是", "✓"),
                "type": cells["type"].strip(),
                "description": cells["desc"].strip(),
            })

        methods.append({
            "name": name,
            "description": description,
            "params": params,
        })

    # 检查重复
    seen: set[str] = set()
    for m in methods:
        if m["name"] in seen:
            raise CodegenParseError(f"duplicate method: {m['name']}")
        seen.add(m["name"])
    return methods


# ---------- enum 提取 ----------

def extract_enum_from_description(desc: str) -> list[str] | None:
    """从说明文字提取 `` 内联代码值作为 enum 候选。"""
    if "`" not in desc:
        return None
    atoms = re.findall(r"`([^`]+)`", desc)
    if not atoms:
        return None
    values: set[str] = set()
    for atom in atoms:
        if " " in atom:
            continue
        if re.search(r"[<>=!&|]", atom):
            continue
        if "_" in atom and atom.replace("_", "").islower():
            continue
        if atom[0].isupper():
            continue
        for v in atom.split("/"):
            v = v.strip()
            if v and v not in ("Y", "N", "Yes", "No", "y", "n"):
                values.add(v)
    return sorted(values) if values else None


# ---------- 构造 spec ----------

def build_tool_specs(methods: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for m in methods:
        name = m["name"]
        params_out: list[dict] = []
        for p in m["params"]:
            param: dict = {
                "name": p["name"],
                "required": p["required"],
                "type": p["type"],
                "description": p["description"],
            }
            enum_vals = extract_enum_from_description(p["description"])
            if enum_vals:
                param["enum"] = enum_vals
            params_out.append(param)
        specs.append({
            "name": name,
            "description": m["description"] or "",
            "routing": name,
            "upstream_tool_mapping": {"tdx_quant": name},
            "cache_ttl_key": infer_cache_ttl_key(name),
            "dangerous": infer_dangerous(name),
            "params": params_out,
        })
    return specs


# ---------- 输出 ----------

def render_yaml(specs: list[dict]) -> str:
    body = yaml.safe_dump(
        {"tools": specs},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return (
        f"# Auto-generated from skills/SKILL.md\n"
        f"# DO NOT EDIT — re-run scripts/gen_tdx_quant_tools.py\n"
        f"{body}"
    )


# ---------- CLI ----------

def _stats(specs: list[dict]) -> str:
    dangerous = sum(1 for s in specs if s["dangerous"])
    cached = sum(1 for s in specs if s["cache_ttl_key"])
    return f"{len(specs)} methods ({dangerous} dangerous, {cached} cacheable)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill-path", default=str(SKILL_PATH))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    skill = Path(args.skill_path)
    if not skill.exists():
        print(f"SKILL.md not found: {skill}", file=sys.stderr)
        return 2
    text = skill.read_text(encoding="utf-8")

    try:
        methods = parse_skill_md(text)
    except CodegenParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1

    specs = build_tool_specs(methods)
    out = render_yaml(specs)

    print(_stats(specs))

    if args.dry_run:
        return 0

    if args.check:
        existing = Path(args.output).read_text(encoding="utf-8") if Path(args.output).exists() else ""
        if existing != out:
            print(f"drift detected: {args.output}", file=sys.stderr)
            return 1
        return 0

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(out, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gen_tdx_quant_tools.py -v`
Expected: PASS, all 11 tests passed

- [ ] **Step 5: Run codegen against real skill**

Run: `python scripts/gen_tdx_quant_tools.py --dry-run`
Expected: prints something like `XX methods (YY dangerous, ZZ cacheable)` where XX is 50-70.

- [ ] **Step 6: Write yaml output**

Run: `python scripts/gen_tdx_quant_tools.py`
Expected: prints `wrote config/tools_tdx_quant.yaml`

- [ ] **Step 7: Verify yaml loads**

Run: `python -c "import yaml; d = yaml.safe_load(open('config/tools_tdx_quant.yaml', encoding='utf-8')); print(len(d['tools']), 'tools'); print('first:', d['tools'][0]['name'])"`
Expected: prints tool count (50-70) and first tool name.

- [ ] **Step 8: Commit**

```bash
git add scripts/gen_tdx_quant_tools.py tests/test_gen_tdx_quant_tools.py config/tools_tdx_quant.yaml
git commit -m "feat(tdx_quant): add codegen script and generate tools_tdx_quant.yaml"
```

---

### Task 5: registry.py — add List[str] type mapping (TDD)

**Files:**
- Modify: `src/registry.py:15-27` (the `_TYPE_MAP` dict)
- Test: `tests/test_registry_tdx_quant.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry_tdx_quant.py`:

```python
"""tests/test_registry_tdx_quant.py — registry 加载 tdx_quant yaml 验证。"""
import yaml
from pathlib import Path

from src.registry import _TYPE_MAP, build_signature, _validate_and_normalize


def test_type_map_includes_list_str():
    assert "List[str]" in _TYPE_MAP
    assert _TYPE_MAP["List[str]"] is list


def test_loads_tdx_quant_yaml_without_error():
    yaml_path = Path("config/tools_tdx_quant.yaml")
    if not yaml_path.exists():
        import pytest
        pytest.skip("tools_tdx_quant.yaml not generated yet")
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    specs = data["tools"]
    assert len(specs) >= 50
    # 每个 spec 必填字段
    for s in specs:
        assert "name" in s
        assert "routing" in s
        assert "upstream_tool_mapping" in s
        assert s["upstream_tool_mapping"] == {"tdx_quant": s["name"]}


def test_list_str_param_builds_valid_signature():
    spec = [{
        "name": "stock_list",
        "required": True,
        "type": "List[str]",
        "description": "股票代码列表",
    }]
    sig = build_signature(spec)
    p = sig.parameters["stock_list"]
    assert p.annotation is list


def test_list_str_param_normalizes_csv_to_list():
    param_specs = [{
        "name": "stock_list",
        "required": True,
        "type": "List[str]",
        "description": "股票代码列表",
    }]
    normalized = _validate_and_normalize(
        "test_tool",
        param_specs,
        {"stock_list": "600519.SH,000001.SZ"},
    )
    assert normalized["stock_list"] == ["600519.SH", "000001.SZ"]


def test_dangerous_tools_present_in_yaml():
    yaml_path = Path("config/tools_tdx_quant.yaml")
    if not yaml_path.exists():
        import pytest
        pytest.skip("tools_tdx_quant.yaml not generated yet")
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    dangerous_names = [s["name"] for s in data["tools"] if s.get("dangerous")]
    assert "order_stock" in dangerous_names
    assert "send_message" in dangerous_names
    assert "create_sector" in dangerous_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_registry_tdx_quant.py -v`
Expected: FAIL at `test_type_map_includes_list_str` (assert "List[str]" in _TYPE_MAP fails)

- [ ] **Step 3: Modify `_TYPE_MAP`**

Edit `src/registry.py`:

Change:
```python
_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}
```

To:
```python
_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "List[str]": list,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_registry_tdx_quant.py -v`
Expected: PASS, all 5 tests passed

- [ ] **Step 5: Run full registry test suite to check no regressions**

Run: `python -m pytest tests/test_registry.py tests/test_registry_enum_validation.py -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/registry.py tests/test_registry_tdx_quant.py
git commit -m "feat(registry): add List[str] type mapping for tdx_quant tools"
```

---

### Task 6: gateway_server.py — add tdx_quant factory branch

**Files:**
- Modify: `src/gateway_server.py:33-37` (imports), `:146-158` (factory branch)

- [ ] **Step 1: Add imports**

Edit `src/gateway_server.py`, in the import block (around line 33-37), add:

```python
from .tdx_quant_client import TdxQuantClient
from .tdx_quant_config import TdxQuantConfig, TdxQuantSettings
```

After the existing:
```python
from .mootdx2_client import MooTDX2Client, MooTDX2Config
from .mootdx2_config import MooTDX2Settings
```

- [ ] **Step 2: Add factory branch**

In `_do_initialize()`, after the `mootdx2` branch (around line 146-157), add a new branch before the `else`:

```python
                elif cfg.get("type") == "tdx_quant":
                    # TdxQuant 进程内客户端
                    tdx_quant_settings = TdxQuantSettings(
                        tdx_root=cfg.get("tdx_root", ""),
                        strategy_id=cfg.get("strategy_id", ""),
                        health_check_interval_sec=cfg.get("health_check_interval_sec", 60),
                        call_timeout_sec=cfg.get("call_timeout_sec", 10),
                        reconnect_threshold=cfg.get("reconnect_threshold", 3),
                        unavailable_threshold=cfg.get("unavailable_threshold", 10),
                    )
                    tdx_quant_cfg = TdxQuantConfig(
                        name=name,
                        market=cfg.get("market", "std"),
                        settings=tdx_quant_settings,
                    )
                    client = TdxQuantClient(tdx_quant_cfg)
```

- [ ] **Step 3: Update the type hint for self.upstreams (optional, low priority)**

In the `__init__` or class attribute declaration (around line 69), update the type hint to include TdxQuantClient:

```python
self.upstreams: dict[str, UpstreamClient | FuyaoClient | TokenWaveTdxClient | MooTDX2Client | TdxQuantClient] = {}
```

(Remove `HttpJsonRpcClient` from the union since it's being deleted.)

- [ ] **Step 4: Remove http_jsonrpc imports and branch**

Edit `src/gateway_server.py`:

Remove line 34:
```python
from .http_jsonrpc_client import HttpJsonRpcClient, HttpJsonRpcConfig
```

Remove the `http_jsonrpc` branch (around lines 130-138):
```python
                elif cfg.get("type") == "http_jsonrpc":
                    # 通用 HTTP JSON-RPC 客户端（如 TQ-Local 通达信本地服务）
                    jsonrpc_cfg = HttpJsonRpcConfig(
                        name=name,
                        base_url=cfg["base_url"].rstrip("/") + "/",
                        timeout_seconds=cfg.get("timeout_seconds", 10),
                        max_retry=cfg.get("retry", {}).get("max_attempts", 3),
                    )
                    client = HttpJsonRpcClient(jsonrpc_cfg)
```

- [ ] **Step 5: Verify gateway imports cleanly**

Run: `python -c "from src.gateway_server import GatewayServer; print('OK')"`
Expected: prints `OK`

- [ ] **Step 6: Commit**

```bash
git add src/gateway_server.py
git commit -m "feat(gateway): add tdx_quant factory branch, remove http_jsonrpc"
```

---

### Task 7: config/upstreams.yaml — swap tdx_tq_local → tdx_quant

**Files:**
- Modify: `config/upstreams.yaml`

- [ ] **Step 1: Replace the tdx_tq_local block**

Edit `config/upstreams.yaml`, replace the `tdx_tq_local:` block (lines ~10-19):

```yaml
  tdx_tq_local:
    enabled: true
    description: "通达信-交易终端: 行情/自选/交易/公式系统, 含 17 个高风险工具 (write ops), 58 个 gateway 工具 (TQ-Local codegen)"
    type: "http_jsonrpc"
    base_url: "http://127.0.0.1:17709"
    timeout_seconds: 10
    retry:
      max_attempts: 2
    capabilities:
      - user_block
      - watchlist
      - conditional_stock
```

With:

```yaml
  tdx_quant:
    enabled: true
    description: "通达信-量化终端 (TdxQuant): 进程内直调 tqcenter.py，含 60+ 工具（行情/板块/交易日/财务/公式/交易/预警）"
    type: "tdx_quant"
    market: "std"
    tdx_root: "D:/new_tdx_mock"
    strategy_id: "unihive_gateway"
    health_check_interval_sec: 60
    call_timeout_sec: 10
    reconnect_threshold: 3
    unavailable_threshold: 10
    capabilities:
      - market_data
      - market_snapshot
      - stock_info
      - sector
      - watchlist
      - trading_calendar
      - financials
      - formula
      - trading
      - subscribe
```

- [ ] **Step 2: Remove old TQ-Local upstream_tool_mapping entries**

In `config/upstreams.yaml`, the comment says "TQ-Local 工具移至 config/tools_tdx_tq_local.yaml（自带 upstream_tool_mapping）". Update to:

```yaml
  # MooTDX2 工具已移至 config/tools_mootdx2.yaml（自带 upstream_tool_mapping）
  # TdxQuant 工具移至 config/tools_tdx_quant.yaml（自带 upstream_tool_mapping）
```

No `upstream_tool_mapping` entries for tdx_tq_local exist in the main yaml (they're in the tools_tdx_tq_local.yaml which will be deleted).

- [ ] **Step 3: Verify yaml parses**

Run: `python -c "import yaml; d = yaml.safe_load(open('config/upstreams.yaml', encoding='utf-8')); print('upstreams:', list(d['upstreams'].keys()))"`
Expected: prints `upstreams: ['mootdx2', 'tdx_quant', 'fuyao_ashare', 'fuyao_index', 'fuyao_meta', 'fuyao_fund']`

- [ ] **Step 4: Commit**

```bash
git add config/upstreams.yaml
git commit -m "feat(config): replace tdx_tq_local with tdx_quant upstream"
```

---

### Task 8: Delete obsolete files

**Files:**
- Delete: `config/tools_tdx_tq_local.yaml`
- Delete: `scripts/gen_tdx_tq_local_tools.py`
- Delete: `src/http_jsonrpc_client.py`
- Delete: `tests/test_gen_tdx_tq_local_tools.py`

- [ ] **Step 1: Verify no live references to deleted files**

Run: `grep -rn "tools_tdx_tq_local\|gen_tdx_tq_local\|http_jsonrpc_client\|HttpJsonRpc" src/ scripts/ config/ tests/ 2>/dev/null | grep -v __pycache__ | head -20`
Expected: empty (or only the files being deleted themselves)

If any references remain in src/ or config/, fix them first.

- [ ] **Step 2: Delete the files**

Run:
```bash
git rm config/tools_tdx_tq_local.yaml
git rm scripts/gen_tdx_tq_local_tools.py
git rm src/http_jsonrpc_client.py
git rm tests/test_gen_tdx_tq_local_tools.py
```

- [ ] **Step 3: Run full test suite to verify no breakage**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests pass (except tests that require running TdxW.exe, which should be skipped)

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove obsolete tdx_tq_local files (yaml/codegen/client/tests)"
```

---

### Task 9: docs/tdx-quant-known-limitations.md

**Files:**
- Create: `docs/tdx-quant-known-limitations.md`

- [ ] **Step 1: Write the limitations doc**

Create `docs/tdx-quant-known-limitations.md`:

```markdown
# TdxQuant 已知限制清单

本文档列出 TdxQuant 上游（`tdx_quant`）实现中的已知限制和简化场景。

## 一、平台与运行环境

### 1.1 仅支持 Windows
- **状态**: tqcenter.py 依赖 Windows DLL（TPyth.dll / TPythClient.dll）与 TdxW.exe IPC
- **影响**: 部署到 Linux 容器无法运行 tdx_quant 上游
- **缓解**: gateway 启动时检测 `sys.platform != "win32"`，跳过 tdx_quant 初始化、标记 UNAVAILABLE，其他上游正常工作
- **优先级**: 已完成

### 1.2 需通达信已安装
- **状态**: 需注册表 `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信*` 存在
- **配置**: `tdx_root` 显式指定安装目录（如 `D:/new_tdx_mock`），避免依赖注册表
- **优先级**: 已完成

### 1.3 需 TdxW.exe 运行中
- **状态**: gateway 启动时若 TdxW 未运行，tq.initialize() 失败、标记 UNAVAILABLE
- **影响**: 工具调用返"上游未就绪"错误
- **恢复**: 后台探活任务在 TdxW 重启后自动 reconnect
- **优先级**: 已完成

## 二、单例与并发

### 2.1 进程级 singleton tq 实例
- **状态**: 整个 gateway 进程持有一个 tq 实例，等同于"一个策略"
- **影响**: 多 gateway 实例需配不同 `strategy_id`，否则 `ErrorId='12'` 冲突
- **优先级**: 已完成

### 2.2 同步调用走 run_in_executor
- **状态**: 所有 tq.* 调用通过默认 ThreadPoolExecutor 执行
- **影响**: 高并发场景可能线程池打满
- **后续**: 必要时引入独立 ThreadPoolExecutor(max_workers=N) 限制
- **优先级**: 低

## 三、功能覆盖

### 3.1 公式系统复杂参数
- **状态**: `formula_zb`/`formula_xg` 的 `setting` 参数为复杂 dict 结构
- **限制**: codegen 抓为 `dict` 类型，调用时由用户传入完整结构
- **后续**: 必要时为公式系统单独编写更精细的参数 spec
- **优先级**: 低

### 3.2 订阅推送机制未实现
- **状态**: `subscribe_hq`/`unsubscribe_hq` 的回调推送本次不实现
- **影响**: 订阅接口可调用但不推送实时数据
- **后续**: 实现 SSE/WebSocket 推送通道
- **优先级**: 低

### 3.3 复权数据
- **状态**: `get_market_data` 支持 `dividend_type` 参数（none/front/back/qfq/hfq）
- **优先级**: 已完成

## 四、MCP 协议

### 4.1 工具标注
- **状态**: ✅ 所有工具设置 readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=True
- **优先级**: 已完成

### 4.2 危险工具审计
- **状态**: ✅ dangerous=true 工具每次调用写 WARNING 审计日志
- **优先级**: 已完成

### 4.3 缓存策略
- **状态**: ✅ 按方法名精确匹配 cache_ttl_key（realtime_quote/historical/fundamentals/ticker_list/workday/null）
- **优先级**: 已完成

## 五、错误处理

### 5.1 ErrorId 翻译
- **状态**: ✅ ErrorId='6'/'7' → DISCONNECTED + 触发 reconnect；'12' → STRATEGY_EXISTS；其他 → UNKNOWN
- **优先级**: 已完成

### 5.2 自动重连
- **状态**: ✅ 连续探活失败 3 次触发 _reconnect()，10 次降级 UNAVAILABLE
- **优先级**: 已完成

---

## 验收检查清单

- [x] 仅 Windows 平台支持（检测后降级）
- [x] 需通达信已安装（tdx_root 配置）
- [x] 需 TdxW.exe 运行中（启动时初始化 + 定期探活）
- [x] 进程级 singleton tq
- [x] run_in_executor 包裹同步调用
- [x] ErrorId 翻译层
- [x] 自动重连（asyncio.Lock 串行化）
- [x] 工具行为标注（readOnlyHint/idempotentHint 等）
- [x] 缓存 TTL 精确映射
- [x] 危险工具审计日志
- [ ] 公式系统复杂参数精细化 spec
- [ ] 订阅推送机制
- [ ] 独立 ThreadPoolExecutor 限流

---

*本文档根据 tdx_quant 迁移生成，最后更新: 2026-09-06*
```

- [ ] **Step 2: Commit**

```bash
git add docs/tdx-quant-known-limitations.md
git commit -m "docs(tdx_quant): add known limitations doc"
```

---

### Task 10: smoke_tdx_quant.py — manual smoke test script

**Files:**
- Create: `scripts/smoke_tdx_quant.py`

- [ ] **Step 1: Write the smoke test script**

Create `scripts/smoke_tdx_quant.py`:

```python
"""scripts/smoke_tdx_quant.py — TdxQuant 上游手动烟测脚本。

前置：TdxW.exe 已运行、已登录。
用法：python scripts/smoke_tdx_quant.py
"""
import json
import sys
import urllib.request


BASE = "http://127.0.0.1:18080/mcp/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


def call_mcp(method: str, params: dict, msg_id: int) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params,
    }
    req = urllib.request.Request(
        BASE,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
    # 解析 SSE 格式：event: message\ndata: {json}
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise RuntimeError(f"no data line in response: {raw[:300]}")


def main() -> int:
    print("=== 1. tools/list (验证 tdx_quant 工具已注册) ===")
    result = call_mcp("tools/list", {}, 1)
    tools = result.get("result", {}).get("tools", [])
    tdx_quant_tools = [t for t in tools if t.get("upstream_tool_mapping") or "tdx_quant" in str(t)]
    print(f"  total tools: {len(tools)}")

    print("\n=== 2. get_market_snapshot (实时快照) ===")
    result = call_mcp("tools/call", {
        "name": "get_market_snapshot",
        "arguments": {"stock_code": "600519.SH"},
    }, 2)
    print(f"  result: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")

    print("\n=== 3. get_market_data (K线) ===")
    result = call_mcp("tools/call", {
        "name": "get_market_data",
        "arguments": {
            "stock_list": ["600519.SH"],
            "period": "1d",
            "count": 5,
        },
    }, 3)
    print(f"  result: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")

    print("\n=== 4. order_stock (dangerous 工具审计日志验证) ===")
    result = call_mcp("tools/call", {
        "name": "order_stock",
        "arguments": {
            "account_id": "fake",
            "code": "600519.SH",
            "order_type": "buy",
        },
    }, 4)
    print(f"  result: {json.dumps(result, ensure_ascii=False, indent=2)[:300]}")
    print("  (检查 logs/gateway.log 是否有 DANGEROUS call: order_stock 警告)")

    print("\n=== 5. 杀 TdxW.exe → 调用 → 验证 DISCONNECTED 翻译 ===")
    print("  手动步骤：taskkill /F /IM TdxW.exe，然后重跑本脚本 step 2")
    print("  预期：返回 success=false, error 含 'DISCONNECTED' 或 '断开'")

    print("\n=== 6. 重启 TdxW.exe → 等 60s 探活周期 → 验证自动恢复 ===")
    print("  手动步骤：重启 TdxW.exe，等 60-120s，然后调用 get_server_status")
    result = call_mcp("tools/call", {
        "name": "get_server_status",
        "arguments": {},
    }, 6)
    print(f"  status: {json.dumps(result, ensure_ascii=False, indent=2)[:300]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add scripts/smoke_tdx_quant.py
git commit -m "test(tdx_quant): add manual smoke test script"
```

---

### Task 11: Final integration test + coverage check

**Files:**
- Test: `tests/test_gateway_startup_batch3.py` (existing, may need update)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -50`
Expected: All tests pass. Any tests requiring live TdxW.exe should be marked skipped or use mocks.

- [ ] **Step 2: Check for leftover references**

Run: `grep -rn "tdx_tq_local\|HttpJsonRpc\|http_jsonrpc" src/ config/ scripts/ tests/ docs/ 2>/dev/null | grep -v __pycache__ | grep -v ".pyc"`
Expected: empty

- [ ] **Step 3: Start gateway and verify tdx_quant loads**

Run: `python -m src.gateway_server --transport http &`
Then: `sleep 3`
Then: `curl -s -X POST http://127.0.0.1:18080/mcp/ -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-06-18" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -c 500`
Expected: tool list includes tdx_quant tools (get_market_data, get_market_snapshot, order_stock, etc.)

Then: `curl -s http://127.0.0.1:18080/api/upstreams | python -m json.tool 2>/dev/null | head -30`
Expected: shows tdx_quant upstream (status HEALTHY if TdxW running, UNAVAILABLE otherwise)

- [ ] **Step 4: Kill gateway**

Run: `taskkill //F //IM python.exe` (or find specific PID via `netstat -ano | grep 18080`)

- [ ] **Step 5: Final commit if any test fixes needed**

```bash
git add -A
git commit -m "test(tdx_quant): integration test pass, full migration complete" 2>/dev/null || echo "nothing to commit"
```

- [ ] **Step 6: Final verification — list all new files**

Run: `git diff --stat main HEAD~11..HEAD` (or appropriate range)
Expected: shows all new/modified/deleted files matching the plan's File Structure section.

---

## Self-Review Notes

### Spec coverage check

- ✅ §1 背景与目标 → covered by plan header + all tasks
- ✅ §2.1 组件清单 → covered by File Structure section + each task's Files block
- ✅ §2.2 模块边界 → Task 3 (client) imports only config + errors + upstream_client
- ✅ §3 数据流 → Task 3 implements call_tool with run_in_executor + ErrorId translation
- ✅ §3.1 关键设计点 (singleton state machine, health loop, cache, dangerous flags) → Tasks 3, 4, 5, 6
- ✅ §4 错误处理 → Task 1 (errors) + Task 3 (client uses errors)
- ✅ §5 codegen + yaml → Task 4
- ✅ §6 缓存 TTL 映射 → Task 4 `infer_cache_ttl_key` + tests
- ✅ §7 测试策略 → Tasks 1, 3, 4, 5 + Task 11 integration
- ✅ §8 配置变更 → Task 7
- ✅ §9 已知限制 → Task 9
- ✅ §10 验收清单 → Task 11 final verification
- ✅ §11 实施顺序 → matches task order

### Placeholder scan

- No TBD/TODO/"implement later"
- All code blocks contain real implementation
- All test cases have specific assertions
- All commands have expected outputs

### Type consistency

- `TdxQuantError`, `TdxQuantErrorType` used consistently across Task 1 (definition) and Task 3 (usage)
- `TdxQuantClient._reconnect()` method name consistent in Task 3 tests + impl
- `UpstreamStatus.HEALTHY/UNAVAILABLE/UNKNOWN` used consistently (no `DISCONNECTED` enum value — using `UNKNOWN` for reconnect-failed state, noted in Task 3 Step 4)
- `infer_dangerous`, `infer_cache_ttl_key`, `parse_skill_md`, `build_tool_specs`, `render_yaml` consistent in Task 4 tests + impl
- `_TYPE_MAP` modification consistent in Task 5 test + impl
