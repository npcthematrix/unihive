"""THSDK 上游客户端。

封装社区 thsdk 包（pip install thsdk，panghu11033/thsdk），对外暴露
同花顺行情/K 线/盘口/分时/逐笔/公司行为/问财选股/7x24 快讯/板块成分/
市场证券列表，以及账号自选股的查询与写操作。

生命周期（对齐 tdx_quant_client）：
- start(): 惰性 import thsdk + 按环境变量 auth() 一次，失败置 UNAVAILABLE
- 调用全部在线程池执行（thsdk 是同步模块级单例），并做 60ms 节流
- 会话失效（NotAuthenticatedError）时自动重新 auth() 一次再重试
- stop(): 只释放引用，不调 logout()（那会删除 account.session）

安全约定：
- 凭证只从 THS_USERNAME / THS_PASSWORD 环境变量读取，不写 YAML
- 写工具以 WRITE_TOOLS 做运行时兜底门控（注册期 filter_specs_by_env 是第一道）
- 4 类 critical 写操作必须 confirm=true
- 日志只记工具名与成败，不打印密码/session 内容
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import importlib
import inspect
import logging
import math
import os
import re
import time
from typing import Any, Callable

from ..exceptions.thsdk_errors import ThsdkErrorType, classify_exception
from ..models.thsdk_config import (
    PASSWORD_ENV,
    ThsdkConfig,
    USERNAME_ENV,
    resolve_thsdk_call_timeout,  # re-export; 测试与 facade 历史路径依赖
)
from .upstream_client import ToolResult, UpstreamStatus
from ..core.blocking_pool import get_thsdk_executor

logger = logging.getLogger(__name__)

# 注入给 hq.dll 的原生超时相对 asyncio 超时的余量：DLL 先返回（干净的超时
# 错误），路由 wait_for 后到期，避免取消后留下僵尸线程。
_NATIVE_TIMEOUT_MARGIN_SEC = 1.0

# 熔断：连续超时达到阈值即熔断快速失败；冷却后半开放一个探针调用。
_CB_FAILURE_THRESHOLD = 3
_CB_COOLDOWN_SEC = 30.0

# 算作"上游超时"的原生错误码精确集合（不用子串匹配，避免
# invalid_timeout_param 这类客户端配置错误被误计成熔断）。
_TIMEOUT_ERROR_CODES = frozenset(
    {
        "timeout",
        "request_timeout",
        "request_timed_out",
        "timedout",
        "deadline",
        "deadline_exceeded",
    }
)


# ---- 原生枚举的网关入参名 → 数值（对照 thsdk.constants，3.0.0）----
# 网关对外只收字符串名，不泄漏 IntEnum 数字；未知名 fail-fast。
_ADJUST_MAP = {"none": 0, "forward": 1, "backward": 2}
_PERIOD_MAP = {
    "m1": 0x3001, "m5": 0x3005, "m15": 0x300F, "m30": 0x301E,
    "m60": 0x303C, "m120": 0x3078, "day": 0x4000, "week": 0x5001,
    "month": 0x6001, "quarter": 0x6003, "year": 0x7001,
}
_REALTIME_METRIC_MAP = {
    "first_limit_up_time": 330323,
    "final_limit_up_time": 330324,
    "limit_up_type": 330325,
    "limit_up_state": 330329,
}
_FINANCIAL_FIELD_ALL = ("total_shares", "float_shares", "net_asset_per_share")
_VALID_FINANCIAL_FIELDS = frozenset(_FINANCIAL_FIELD_ALL)
_VALID_TIMELINES = frozenset({"historical", "realtime"})
# 字符串枚举：LLM 客户端常输出大小写变体，统一 strip+归一 + 白名单拒绝。
_WENCAI_KIND_MAP = {"concept": "concept", "industry": "industry"}
_SORT_ORDER_MAP = {"d": "D", "a": "A"}
_NEWS_RANGE_MAP = {"recent": "recent", "historical": "historical"}


def _map_enum(value: str, mapping: dict[str, int], label: str) -> int:
    key = str(value).strip().lower()
    if key not in mapping:
        raise ValueError(
            f"{label} 取值非法：{value}，可选：{', '.join(mapping)}"
        )
    return mapping[key]


def _map_str_enum(value: Any, mapping: dict[str, str], label: str) -> str:
    """字符串枚举归一：输入大小写不敏感，输出映射里的规范值。"""
    key = str(value).strip().lower()
    if key not in mapping:
        raise ValueError(
            f"{label} 取值非法：{value}，可选：{', '.join(mapping)}"
        )
    return mapping[key]


def _map_enum_list(values: Any, mapping: dict[str, int], label: str) -> list[int]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{label} 必须是非空数组")
    return [_map_enum(v, mapping, label) for v in values]


# 完整 THSCODE：4 位市场前缀 + 6 位以上数字（USHA600519 / USZA300033）
_THSCODE_RE = re.compile(r"^[A-Z]{2,6}\d{4,12}$")

try:
    import numpy as np
    import pandas as pd
except ImportError:  # pragma: no cover - thsdk 依赖 pandas,正常环境必有
    np = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]


# 写工具表：confirm=True 的 4 个为 critical 操作（clear/delete/replace）
WRITE_TOOLS: dict[str, dict[str, bool]] = {
    "ths_add_account_watchlist_securities": {"confirm": False},
    "ths_remove_account_watchlist_securities": {"confirm": False},
    "ths_replace_account_watchlist_securities": {"confirm": True},
    "ths_clear_account_watchlist": {"confirm": True},
    "ths_create_account_watchlist_group": {"confirm": False},
    "ths_delete_account_watchlist_group": {"confirm": True},
    "ths_rename_account_watchlist_group": {"confirm": False},
    "ths_add_account_watchlist_group_securities": {"confirm": False},
    "ths_remove_account_watchlist_group_securities": {"confirm": False},
    "ths_replace_account_watchlist_group_securities": {"confirm": True},
}


def is_full_thscode(code: str) -> bool:
    """是否已是完整 THSCODE（USHA600519）。"""
    return bool(_THSCODE_RE.match(str(code).strip()))


# 基金市场前缀（get_market_metadata 官方子类型: 上证基金 sub=20 / 深证基金 sub=36）。
# 同花顺每日资金流向接口按 A 股资金分单口径，基金无此数据，需提前识别并明确拒绝。
_FUND_MARKET_PREFIXES = frozenset({"USHJ", "USZJ"})
# 裸码 ETF/基金段（无市场前缀时按代码段推断; 与 mootdx2 ETF 前缀对齐）：
# 沪 51/56/58（含 ETF/基金），深 15（ETF）、16（LOF/分级）、18。
_FUND_BARE_PREFIXES_SH = ("51", "56", "58")
_FUND_BARE_PREFIXES_SZ = ("15", "16", "18")


def is_fund_security(value: Any) -> bool:
    """判定一个 security 入参是否为基金/ETF（每日资金流向接口不支持的品种）。

    支持形态：完整 THSCODE（USHJ510300）、sh/sz 前缀（sh510300）、裸码（510300）。
    非基金或无法判定 → False（交由下游处理，不误拦）。
    """
    text = str(value).strip()
    if not text:
        return False
    upper = text.upper()

    # 完整 THSCODE: 前 4 位市场前缀
    if _THSCODE_RE.match(upper) and upper[:4] in _FUND_MARKET_PREFIXES:
        return True

    # sh/sz 文字前缀: 剥离后看裸码段（沪 51/56/58 为基金；深 15/16/18 为基金）
    m = re.match(r"^(SH|SZ)([0-9]{4,12})$", upper)
    if m:
        side, bare = m.group(1), m.group(2)
        prefixes = _FUND_BARE_PREFIXES_SH if side == "SH" else _FUND_BARE_PREFIXES_SZ
        return bare[:2] in prefixes

    # 纯裸码: 沪市基金段（51/56/58）；深市基金段（15/16/18）。
    if re.match(r"^[0-9]{4,12}$", text):
        bare2 = text[:2]
        return bare2 in _FUND_BARE_PREFIXES_SH or bare2 in _FUND_BARE_PREFIXES_SZ

    return False


# resolve_securities 的 codes 只收裸数字码（官方示例 ["300033","600519"]）；
# sh/sz/bj 前缀、.SH/.SZ/.BJ 后缀、完整 THSCODE 均被服务端静默丢弃返 0 行。
_BARE_DIGITS_RE = re.compile(r"^\d{4,12}$")


def to_bare_code(value: Any) -> str:
    """剥掉市场前缀/后缀，归一为裸数字码。

    600519 / sh600519 / 600519.SH / USHA600519 → "600519"；
    无法归一的元素直接 ValueError（不静默丢弃）。
    """
    text = str(value).strip()
    if not text:
        raise ValueError("codes 元素不能为空")
    bare = re.sub(r"^[A-Za-z]+", "", text)  # USHA / sh 前缀
    bare = re.sub(r"\.[A-Za-z]{1,4}$", "", bare)  # .SH 后缀
    if not _BARE_DIGITS_RE.match(bare):
        raise ValueError(
            f"无法识别的证券代码：{value}；请传裸码（如 600519）、"
            "交易所前缀码（sh600519）或完整 THSCODE（USHA600519）"
        )
    return bare


def _watchlist_version(obj: Any) -> Any:
    """从公开层 DataFrame 的 attrs 取自选 version；非 DataFrame 回退 None。"""
    if pd is not None and isinstance(obj, pd.DataFrame):
        metadata = obj.attrs.get("thsdk", {}).get("metadata", {})
        if isinstance(metadata, dict):
            return metadata.get("version")
    if isinstance(obj, dict):
        return obj.get("version")
    return None


def _group_id(group: dict) -> Any:
    """分组唯一身份：group_key 优先；缺失或显式 null 才回退 id。

    map 键与 group_order 都由本函数导出，避免两处取值口径漂移。
    """
    gid = group.get("group_key")
    return gid if gid is not None else group.get("id")


# 裸码数字段 → 市场（与原生按数字段推断市场一致；只列能确定的段）。
# 注意 000xxx：原生推断为深圳股票，故 sh000001（上证指数）剥成裸码会
# 静默解析成平安银行，必须靠显式市场标记拦下来。
def _inferred_market(bare: str) -> str | None:
    if bare[:2] in {"60", "68", "90"}:
        return "USHA"
    if bare[:2] in {"00", "30", "20"} or bare[:3] == "399":
        return "USZA"
    return None


def explicit_market_of(value: Any) -> str | None:
    """取证券代码的显式市场标记 → USHA/USZA；无标记返 None。

    sh600519 / 600519.SH / USHA600519 → USHA；
    北交所标记直接 ValueError（thsdk 3.0.0 仅沪深两市）。
    """
    upper = str(value).strip().upper()
    token: str | None = None
    suffix = re.search(r"\.([A-Z]{1,4})$", upper)
    if suffix:
        token = suffix.group(1)
    else:
        prefix = re.match(r"^([A-Z]+)", upper)
        if prefix:
            token = prefix.group(1)
    if token is None:
        return None
    if token in {"USHA", "SH"}:
        return "USHA"
    if token in {"USZA", "SZ"}:
        return "USZA"
    if token in {"BJ", "URBJ"}:
        raise ValueError(
            "THSDK 上游仅支持沪深市场（USHA/USZA），不支持北交所代码"
        )
    return None


def json_safe(obj: Any) -> Any:
    """把 thsdk 返回（DataFrame/dict/标量/numpy 类型/datetime）转成 JSON 安全对象。

    DataFrame（含时间索引）→ list[dict]（records）；datetime/date/Timestamp
    → ISO 字符串；NaN/Inf → None；其余递归转字符串。
    """
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if pd is not None and isinstance(obj, pd.DataFrame):
        frame = obj
        if not isinstance(frame.index, pd.RangeIndex):
            frame = frame.reset_index()
        return [json_safe(row) for row in frame.to_dict(orient="records")]
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if np is not None:
        if isinstance(obj, np.ndarray):
            return json_safe(obj.tolist())
        if isinstance(obj, np.generic):
            return json_safe(obj.item())
        if pd is not None and isinstance(obj, pd.Timestamp):
            return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in obj]
    return str(obj)


class _Throttle:
    """串行化所有 thsdk 调用并保证最小调用间隔（规避 50ms 服务端限频）。"""

    def __init__(
        self,
        interval_sec: float,
        executor_factory: Callable[[], Any],
    ):
        self._interval = max(0.0, interval_sec)
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._executor_factory = executor_factory
        # 并发累计 (调用突发短于轮询间隔, 靠 total/peak 留存)
        self.counters = {"total": 0, "current": 0, "peak": 0}

    async def run(
        self, loop: asyncio.AbstractEventLoop, fn: Callable[[], Any], timeout: float
    ) -> Any:
        async with self._lock:
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            counters = self.counters
            counters["total"] += 1
            counters["current"] += 1
            counters["peak"] = max(counters["peak"], counters["current"])
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(self._executor_factory(), fn), timeout=timeout
                )
            finally:
                counters["current"] -= 1
                self._last = time.monotonic()


class ThsdkClient:
    """进程内直调 thsdk 模块的上游客户端。"""

    def __init__(self, config: ThsdkConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._mod: Any = None
        self._authed = False
        self._auth_mode = "guest"  # "credentials" | "guest"
        self._throttle = _Throttle(
            config.throttle_ms / 1000.0, get_thsdk_executor
        )
        self._auth_lock = asyncio.Lock()
        # 熔断器状态："closed" | "open" | "half_open"
        self._cb_state: str = "closed"
        self._cb_failures = 0
        self._cb_opened_at = 0.0
        # half_open 期间是否已有探针在执行（只放行 1 个，其余快速失败）
        self._cb_probe_inflight = False

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        # M2: THSDK 没有 "半恢复" 语义 — 区别于 TdxQuantClient (DEGRADED 表示
        # DLL 部分活着, 仍能响应 tq.* 调用)。THSDK 的 DEGRADED 仅出现在
        # _invoke_with_reauth 重登过程中, 含义是 "session 临时失效, 不该被路由".
        # 因此 is_available 仅在 HEALTHY 时为 True, 避免路由选上一个
        # 已知 session 失效的上游。
        return self._status == UpstreamStatus.HEALTHY

    # ---------- 生命周期 ----------

    async def start(self) -> bool:
        """import thsdk 并完成一次登录。失败不抛，置 UNAVAILABLE。"""
        try:
            loop = asyncio.get_running_loop()
            self._mod = await self._throttle.run(
                loop, lambda: importlib.import_module("thsdk"), self.config.call_timeout_sec
            )
            await self._authenticate()
            self._bind_native_timeout()
            self._status = UpstreamStatus.HEALTHY
            self._cb_state = "closed"
            self._cb_failures = 0
            self._cb_opened_at = 0.0
            self._cb_probe_inflight = False
            logger.info(
                "[%s] authenticated (mode=%s, watchlist_write=%s)",
                self.name,
                self._auth_mode,
                self.config.allow_watchlist_write,
            )
            return True
        except Exception as e:
            # L3: import 成功但 _authenticate 失败时, _mod 已设置但 _authed=False.
            # 下次 start() 会重新覆盖 _mod (importlib 幂等), 但若调用方在失败
            # 状态下误调 call_tool, 残留 _mod.complete_ths_code 等仍可触发;
            # call_tool 的 self._authed 守卫会拦截, 这里仍主动归零以防漏判.
            self._mod = None
            self._authed = False
            err = classify_exception(e)
            logger.error(
                "[%s] start failed: %s: %s",
                self.name,
                err.error_type.value,
                err.message,
            )
            self._status = UpstreamStatus.UNAVAILABLE
            return False

    async def stop(self) -> None:
        # 不调 thsdk.logout()：那会删除可复用的 account.session 文件
        self._mod = None
        self._authed = False
        self._status = UpstreamStatus.UNAVAILABLE

    async def _authenticate(self) -> None:
        """按环境变量登录一次：凭证优先，否则 thsdk 自动/临时会话。"""
        async with self._auth_lock:
            user = os.getenv(USERNAME_ENV, "").strip()
            password = os.getenv(PASSWORD_ENV, "").strip()
            if bool(user) != bool(password):
                raise ValueError(
                    f"{USERNAME_ENV} 与 {PASSWORD_ENV} 必须同时设置且非空"
                )
            mod = self._mod
            if mod is None:
                raise RuntimeError("thsdk module not imported")

            def _do() -> bool:
                if user and password:
                    return bool(mod.auth(user, password))
                return bool(mod.auth())

            loop = asyncio.get_running_loop()
            ok = await self._throttle.run(
                loop, _do, self.config.call_timeout_sec
            )
            if not ok:
                raise RuntimeError("thsdk.auth() returned False")
            self._authed = True
            self._auth_mode = "credentials" if user and password else "guest"

    def _bind_native_timeout(self) -> None:
        """在默认 session 的 runtime bridge 上注入默认 timeout_ms。

        thsdk 3.0.0 的 api.py 便捷函数（get_price 等）调用 ``_apis`` 时
        timeout=None，请求 JSON 不带 timeout_ms，hq.dll 在网络瞬断时可无限
        阻塞。这里在 bridge.call 外包一层：调用方未显式给 timeout_ms 时
        注入 (call_timeout - 余量)，使 DLL 在 asyncio/路由超时之前自行返回。

        结构不符（旧版包 / 测试假模块无 session）时 info 级跳过，不阻断 start。
        """
        try:
            session = self._mod.session  # type: ignore[attr-defined]
            bridge = session._get_client().runtime
            # 签名预检：未来 thsdk 若改 call 签名（参数顺序/改名），绑定后
            # 每个业务调用都会 TypeError。这里直接跳过，保留原始 call。
            sig = inspect.signature(type(bridge).call)
            if "timeout_ms" not in sig.parameters:
                raise RuntimeError("runtime bridge.call has no timeout_ms parameter")
            default_ms = int(
                max(1.0, self.config.call_timeout_sec - _NATIVE_TIMEOUT_MARGIN_SEC)
                * 1000
            )
            # 取类上的原始 call，先剔除旧包装：保证 stop/start 重绑不嵌套，
            # 且新配置的超时值即时生效。
            bridge.__dict__.pop("call", None)
            original_call = type(bridge).call

            def call_with_default(
                method: str,
                params: Any = None,
                *,
                timeout_ms: int | None = None,
            ) -> Any:
                effective_ms = (
                    timeout_ms if timeout_ms is not None else default_ms
                )
                return original_call(
                    bridge, method, params, timeout_ms=effective_ms
                )

            bridge.call = call_with_default
        except Exception as e:
            logger.info(
                "[%s] native timeout binding skipped: %s", self.name, e
            )

    # ---------- 熔断 ----------

    def _circuit_gate_error(self) -> str | None:
        """熔断开启时返回快速失败消息；冷却到期则转 half_open 放行唯一探针。"""
        if self._cb_state == "closed":
            return None
        if self._cb_state == "open":
            elapsed = time.monotonic() - self._cb_opened_at
            if elapsed < _CB_COOLDOWN_SEC:
                remaining = int(_CB_COOLDOWN_SEC - elapsed) + 1
                return (
                    f"THSDK 熔断中：上游连续 {_CB_FAILURE_THRESHOLD} 次超时，"
                    f"约 {remaining} 秒后自动探活恢复"
                )
            self._cb_state = "half_open"
            logger.info(
                "[%s] circuit half-open: next call serves as probe", self.name
            )
        # half_open：标准熔断器语义只放 1 个探针，其余调用快速失败，
        # 防止积压队列在探针出结果前涌进 DLL（flip-flop / 冷却期被推迟）。
        if self._cb_probe_inflight:
            return "THSDK 熔断半开探活中：已有探针在执行，请稍后重试"
        self._cb_probe_inflight = True
        return None

    def _record_success(self) -> None:
        """成功调用：熔断复位（仅在曾开启时记日志）。"""
        was_tripped = self._cb_state != "closed"
        self._cb_state = "closed"
        self._cb_failures = 0
        self._cb_opened_at = 0.0
        self._cb_probe_inflight = False
        self._status = UpstreamStatus.HEALTHY
        if was_tripped:
            logger.info("[%s] circuit closed: upstream recovered", self.name)

    def _record_timeout_failure(self) -> None:
        """超时类失败计数：half_open/open 中的失败一律重新熔断。"""
        if self._cb_state in ("open", "half_open"):
            self._open_circuit()
            return
        self._cb_failures += 1
        if self._cb_failures >= _CB_FAILURE_THRESHOLD:
            self._open_circuit()

    def _open_circuit(self) -> None:
        self._cb_state = "open"
        self._cb_opened_at = time.monotonic()
        self._cb_probe_inflight = False
        self._status = UpstreamStatus.UNAVAILABLE
        logger.warning(
            "[%s] circuit OPEN: %d consecutive upstream timeouts",
            self.name,
            self._cb_failures or _CB_FAILURE_THRESHOLD,
        )

    @staticmethod
    def _is_timeout_like(err: Any) -> bool:
        """asyncio 超时，或原生错误码在超时精确集合内。"""
        if err.error_type is ThsdkErrorType.TIMEOUT:
            return True
        return str(err.code or "").lower() in _TIMEOUT_ERROR_CODES

    # ---------- 调用入口 ----------

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        start_time = time.time()

        def _result(
            ok: bool,
            data: Any = None,
            error: str | None = None,
            detail: dict | None = None,
        ) -> ToolResult:
            return ToolResult(
                success=ok,
                data=data,
                error=error,
                error_detail=detail,
                source=self.name,
                duration_ms=int((time.time() - start_time) * 1000),
            )

        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return _result(False, error=f"Unknown tool: {tool_name}")

        write_spec = WRITE_TOOLS.get(tool_name)
        if write_spec is not None and not self.config.allow_watchlist_write:
            return _result(
                False,
                error="自选写操作未启用：需在启动前设置 ALLOW_WATCHLIST_WRITE=true",
            )
        if write_spec is not None and write_spec["confirm"] and not params.get("confirm"):
            return _result(
                False,
                error="该操作不可撤销，必须显式传 confirm=true 才会执行",
            )
        if tool_name != "ths_auth_status" and not self._authed:
            return _result(
                False,
                error="THSDK 未登录或登录已失效",
                detail={"error_type": "not_authenticated"},
            )

        if tool_name != "ths_auth_status":
            gate_error = self._circuit_gate_error()
            if gate_error is not None:
                return _result(
                    False,
                    error=gate_error,
                    detail={"error_type": "circuit_open"},
                )

        try:
            data = await self._invoke_with_reauth(handler, params, tool_name=tool_name)
            if tool_name != "ths_auth_status":
                self._record_success()
            return _result(True, data=data)
        except asyncio.CancelledError:
            # 路由 wait_for 先到期（或关停）取消调用：CancelledError 是
            # BaseException，必须在这里单独接住——对客户端边界而言这就是
            # 一次超时类事件，不记录则熔断器在并发超时下永远打不开。
            if tool_name != "ths_auth_status":
                self._record_timeout_failure()
            raise
        except Exception as e:
            err = classify_exception(e)
            logger.warning(
                "[%s] call %s failed: %s: %s",
                self.name,
                tool_name,
                err.error_type.value,
                err.message,
            )
            if tool_name != "ths_auth_status":
                if self._cb_state == "half_open":
                    # 探针的任何失败（不只超时类，如 query_failed / 连接重置）
                    # 都重新熔断；否则 half_open 会永久卡死、快速失败失效。
                    self._open_circuit()
                elif self._is_timeout_like(err):
                    self._record_timeout_failure()
            return _result(False, error=err.message, detail=err.to_dict())

    async def _invoke_with_reauth(
        self, handler: Callable, params: dict, tool_name: str
    ) -> Any:
        try:
            return await handler(params)
        except Exception as e:
            if classify_exception(e).error_type != ThsdkErrorType.NOT_AUTHENTICATED:
                raise
            # 写工具不重试 handler: 若服务端已部分执行, 重试会重复修改自选数据。
            # 后台 best-effort 重登让后续只读调用能继续, 然后把 NotAuthenticatedError
            # 抛回去让调用方知道"写已中止, 需用户重新确认后重发"。
            if tool_name in WRITE_TOOLS:
                logger.error(
                    "[%s] write tool %s aborted due to session expiry; "
                    "not retrying to avoid duplicate execution",
                    self.name,
                    tool_name,
                )
                try:
                    await self._authenticate()
                except Exception as auth_err:
                    logger.warning(
                        "[%s] background re-auth after aborted write failed: %s",
                        self.name,
                        auth_err,
                    )
                raise
            # 只读工具: 重新登录后重试一次 (idempotent read)
            self._status = UpstreamStatus.DEGRADED
            logger.warning("[%s] session expired, re-authenticating", self.name)
            await self._authenticate()
            self._status = UpstreamStatus.HEALTHY
            return await handler(params)

    async def _call_raw(
        self, fn: Callable, *args: Any, **kwargs: Any
    ) -> Any:
        """线程池执行 + 节流，不做 JSON 转换（保留 DataFrame.attrs 元数据）。"""
        loop = asyncio.get_running_loop()
        return await self._throttle.run(
            loop, lambda: fn(*args, **kwargs), self.config.call_timeout_sec
        )

    async def _call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """线程池执行同步 thsdk 调用 + 节流 + JSON 转换。"""
        return json_safe(await self._call_raw(fn, *args, **kwargs))

    async def _resolve_codes(self, codes: list[str]) -> list[str]:
        """短代码先经 complete_ths_code 补全；返回去重后的完整 THSCODE 列表。"""
        if not codes:
            raise ValueError("securities 列表不能为空")
        complete = [c.strip() for c in codes if is_full_thscode(c)]
        short = [c.strip() for c in codes if not is_full_thscode(c)]
        if short:
            # complete_ths_code 返回帧，json_safe 后为 records；
            # _identity_rows 保证每行带 full_code 列（未解析行为空串）。
            raw = await self._call(self._mod.complete_ths_code, short)
            if not isinstance(raw, list):
                raise ValueError(f"短代码无法补全为完整 THSCODE：{short}")
            resolved = [
                r["full_code"]
                for r in raw
                if isinstance(r, dict) and is_full_thscode(str(r.get("full_code") or ""))
            ]
            if not resolved:
                raise ValueError(f"短代码无法补全为完整 THSCODE：{short}")
            complete.extend(resolved)
        return list(dict.fromkeys(complete))

    async def _normalize_block(self, value: Any) -> Any:
        """板块入参归一到 thsdk 3.0.0 原生层接受的形态。

        3.0.0 的 ``_normalize_block`` 只收 int（数字 ID）或 Mapping（Block），
        传 str 直接 TypeError。约定：数字串→int；名称串→经原生 resolve_block
        取 Block 映射；int/Mapping 原样透传。
        """
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, dict):
            return value
        text = str(value).strip()
        if not text:
            raise ValueError("板块 ID 或名称不能为空")
        if text.isdigit():
            return int(text)
        resolved = await self._call(self._mod.resolve_block, name=text)
        if not isinstance(resolved, dict):
            raise ValueError(f"无法解析板块名称：{text}")
        return resolved

    async def _normalize_blocks(self, values: Any) -> list[Any]:
        """板块数组逐元素归一（list_block_descriptions 原生必填 blocks）。"""
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError("blocks 必须是非空数组")
        return [await self._normalize_block(v) for v in values]

    async def list_tools(self) -> list[dict]:
        return []

    async def health_check(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    def get_metrics(self) -> dict:
        """运行时状态快照 (供 /api/upstreams/thsdk/metrics 与监控页)。

        只含运行时事实: 连接/认证/熔断态 + 生效配置, 不回读配置文件。
        """
        from pathlib import Path
        from ..core.blocking_pool import thsdk_executor_snapshot

        # account.session 复用情况: stop() 不 logout, 重启后免重复登录。
        session_file = Path("account.session")

        # 熔断剩余冷却 (open 态); half_open 等探针, 无倒计时。
        cb_remaining: float | None = None
        if self._cb_state == "open":
            cb_remaining = max(
                0.0, _CB_COOLDOWN_SEC
                - (time.monotonic() - self._cb_opened_at)
            )

        # 节流冷却剩余: interval - 距上次调用
        throttle = self._throttle
        throttle_remaining = max(
            0.0,
            throttle._interval - (time.monotonic() - throttle._last),
        )

        return {
            "status": self._status.value,
            "available": self.is_available,
            "authenticated": self._authed,
            "auth_mode": self._auth_mode,
            "watchlist_write": self.config.allow_watchlist_write,
            "circuit_breaker": self._cb_state,
            "cb_failures": self._cb_failures,
            "cb_cooldown_remaining_sec": round(cb_remaining, 1)
            if cb_remaining is not None else None,
            "call_timeout_sec": self.config.call_timeout_sec,
            "throttle_ms": self.config.throttle_ms,
            "throttle_remaining_sec": round(throttle_remaining, 2),
            "worker_pool": {
                **thsdk_executor_snapshot(),
                "total": throttle.counters["total"],
                "peak": throttle.counters["peak"],
            },
            "session_persisted": session_file.exists(),
        }

    # ========== 只读工具 ==========

    async def _tool_ths_auth_status(self, p: dict) -> dict:
        return {
            "authenticated": self._authed,
            "auth_mode": self._auth_mode,
            "guest": self._auth_mode == "guest",
            "watchlist_write_enabled": self.config.allow_watchlist_write,
        }

    async def _tool_ths_account_permissions(self, p: dict) -> Any:
        return await self._call(self._mod.account_permissions)

    async def _tool_ths_get_account_watchlist(self, p: dict) -> Any:
        frame = await self._call_raw(self._mod.get_account_watchlist)
        # 破坏性返回变更：裸 list → {"version", "securities"}；
        # version 供 ths_replace_account_watchlist_securities 乐观锁使用。
        return {
            "version": _watchlist_version(frame),
            "securities": json_safe(frame),
        }

    async def _tool_ths_get_account_watchlist_groups(self, p: dict) -> Any:
        return await self._call(self._mod.get_account_watchlist_groups)

    async def _tool_ths_get_all_watchlist(self, p: dict) -> Any:
        """合并主自选(group_id=0)与分组(group_id=35+)为一个统一视图。"""
        wl_frame = await self._call_raw(self._mod.get_account_watchlist)
        wl = json_safe(wl_frame)
        wl_version = _watchlist_version(wl_frame)
        # groups 公开层帧经 json_safe reset_index → list，
        # 每个元素是 {group_key, name, securities, version}
        groups = await self._call(self._mod.get_account_watchlist_groups)

        # 默认分组统一为 group_key 形状（与命名分组同构）
        default_group = {
            "group_key": 0,
            "name": "默认分组",
            "securities": wl,
            "version": wl_version,
        }

        all_groups = {"0": default_group}
        if groups:
            for g in groups:
                all_groups[str(_group_id(g))] = g

        return {
            "version": wl_version,
            "group_order": [0] + [_group_id(g) for g in groups],
            "groups": all_groups,
            "main_watchlist": {
                "count": len(wl) if wl else 0,
                "securities": wl,
                "version": wl_version,
            },
        }

    async def _tool_ths_search_symbols(self, p: dict) -> Any:
        return await self._call(
            self._mod.search_symbols, p["pattern"], market=p.get("market")
        )

    async def _tool_ths_complete_ths_code(self, p: dict) -> Any:
        return await self._call(self._mod.complete_ths_code, p["codes"])

    async def _tool_ths_get_price(self, p: dict) -> Any:
        kwargs: dict[str, Any] = {
            "frequency": p.get("frequency", "daily"),
            "fq": p.get("fq", "pre"),
        }
        if p.get("start_date") and p.get("end_date"):
            kwargs["start_date"] = p["start_date"]
            kwargs["end_date"] = p["end_date"]
        else:
            kwargs["count"] = int(p.get("count", 20))
        return await self._call(self._mod.get_price, p["security"], **kwargs)

    async def _tool_ths_klines(self, p: dict) -> Any:
        kwargs: dict[str, Any] = {
            "interval": p.get("interval", "day"),
            "adjust": p.get("adjust", ""),
        }
        if p.get("start_time") and p.get("end_time"):
            kwargs["start_time"] = p["start_time"]
            kwargs["end_time"] = p["end_time"]
        else:
            kwargs["count"] = int(p.get("count", 20))
        ths_code = p["security"]
        return await self._call(self._mod.klines, ths_code, **kwargs)

    async def _tool_ths_intraday_data(self, p: dict) -> Any:
        ths_code = p["security"]
        return await self._call(
            self._mod.intraday_data, ths_code, date=p.get("date")
        )

    async def _tool_ths_depth(self, p: dict) -> Any:
        ths_code = p["security"]
        return await self._call(self._mod.depth, ths_code)

    async def _tool_ths_tick_level1(self, p: dict) -> Any:
        # 原生硬性互斥：count 不能与 start_time/end_time 同用，且时间端点须成对。
        start_t = str(p.get("start_time") or "").strip()
        end_t = str(p.get("end_time") or "").strip()
        if start_t or end_t:
            if not (start_t and end_t):
                raise ValueError("start_time/end_time 必须同时传入")
            kwargs: dict[str, Any] = {
                "count": None,
                "start_time": start_t,
                "end_time": end_t,
            }
        else:
            kwargs = {"count": int(p.get("count", 100))}
        ths_code = p["security"]
        return await self._call(self._mod.tick_level1, ths_code, **kwargs)

    async def _tool_ths_corporate_action(self, p: dict) -> Any:
        ths_code = p["security"]
        return await self._call(
            self._mod.corporate_action, ths_code, count=int(p.get("count", 100))
        )

    async def _tool_ths_wencai_nlp(self, p: dict) -> Any:
        return await self._call(
            self._mod.wencai_nlp,
            condition=p["query"],
            markets=p.get("markets"),
            limit=int(p.get("limit", 50)),
        )

    async def _tool_ths_news(self, p: dict) -> Any:
        return await self._call(
            self._mod.news,
            page=int(p.get("page", 1)),
            page_size=int(p.get("page_size", 20)),
            tag=p.get("tag", ""),
        )

    async def _tool_ths_block_constituents(self, p: dict) -> Any:
        raw = p["block"]
        # 公开层 block_constituents(block: int|str, ...) 是手写函数，不收
        # Mapping：数字串须先归 int（否则被当名称再 resolve 一次必然落空），
        # 名称串原样传入，由公开层内部 resolve_block，网关不重复调用。
        if isinstance(raw, int):
            block: Any = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            block = int(raw.strip())
        else:
            block = raw
        return await self._call(
            self._mod.block_constituents,
            block,
            start=int(p.get("start", 0)),
            count=int(p.get("count", 50)),
        )

    async def _tool_ths_market_securities(self, p: dict) -> Any:
        return await self._call(
            self._mod.list_market_securities,
            market=p["market"],
            sort_begin=int(p.get("start", 0)),
            sort_count=int(p.get("count", 100)),
        )

    # ========== 账号自选写工具（运行时兜底；注册由 ALLOW_WATCHLIST_WRITE 门控） ==========

    async def _tool_ths_add_account_watchlist_securities(self, p: dict) -> Any:
        codes = await self._resolve_codes(p["securities"])
        return await self._call(
            self._mod.add_account_watchlist_securities,
            securities=codes,
            add_to_front=bool(p.get("add_to_front", False)),
        )

    async def _tool_ths_remove_account_watchlist_securities(self, p: dict) -> Any:
        codes = await self._resolve_codes(p["securities"])
        return await self._call(
            self._mod.remove_account_watchlist_securities, securities=codes
        )

    async def _tool_ths_replace_account_watchlist_securities(self, p: dict) -> Any:
        codes = await self._resolve_codes(p["securities"])
        return await self._call(
            self._mod.replace_account_watchlist_securities,
            version=int(p["version"]),
            securities=codes,
        )

    async def _tool_ths_clear_account_watchlist(self, p: dict) -> Any:
        return await self._call(self._mod.clear_account_watchlist)

    async def _tool_ths_create_account_watchlist_group(self, p: dict) -> Any:
        kwargs: dict[str, Any] = {"name": p["name"]}
        if p.get("securities"):
            kwargs["securities"] = await self._resolve_codes(p["securities"])
        return await self._call(self._mod.create_account_watchlist_group, **kwargs)

    async def _tool_ths_delete_account_watchlist_group(self, p: dict) -> Any:
        return await self._call(
            self._mod.delete_account_watchlist_group, group_id=int(p["group_id"])
        )

    async def _tool_ths_rename_account_watchlist_group(self, p: dict) -> Any:
        return await self._call(
            self._mod.rename_account_watchlist_group,
            group_id=int(p["group_id"]),
            name=p["name"],
        )

    async def _tool_ths_add_account_watchlist_group_securities(self, p: dict) -> Any:
        codes = await self._resolve_codes(p["securities"])
        return await self._call(
            self._mod.add_account_watchlist_group_securities,
            group_id=int(p["group_id"]),
            securities=codes,
        )

    async def _tool_ths_remove_account_watchlist_group_securities(
        self, p: dict
    ) -> Any:
        codes = await self._resolve_codes(p["securities"])
        return await self._call(
            self._mod.remove_account_watchlist_group_securities,
            group_id=int(p["group_id"]),
            securities=codes,
        )

    async def _tool_ths_replace_account_watchlist_group_securities(
        self, p: dict
    ) -> Any:
        codes = await self._resolve_codes(p["securities"])
        return await self._call(
            self._mod.replace_account_watchlist_group_securities,
            group_id=int(p["group_id"]),
            securities=codes,
        )

    # ========== 板块 Blocks ==========

    async def _tool_ths_list_block_descriptions(self, p: dict) -> Any:
        # 原生逐元素只收 int/Mapping：数字 ID 或名称数组先统一归一。
        blocks = await self._normalize_blocks(p["blocks"])
        return await self._call(
            self._mod.list_block_descriptions, blocks=blocks
        )

    async def _tool_ths_list_security_block_memberships(self, p: dict) -> Any:
        sec = p["security"]
        return await self._call(
            self._mod.list_security_block_memberships, security=sec
        )

    async def _tool_ths_get_security_concept_tags(self, p: dict) -> Any:
        sec = p["security"]
        return await self._call(
            self._mod.get_security_concept_tags, security=sec
        )

    async def _tool_ths_get_security_industry(self, p: dict) -> Any:
        sec = p["security"]
        return await self._call(
            self._mod.get_security_industry, security=sec
        )

    async def _tool_ths_list_industry_children(self, p: dict) -> Any:
        return await self._call(
            self._mod.list_industry_children,
            industry=p["industry"],
        )

    async def _tool_ths_rank_block_securities(self, p: dict) -> Any:
        # 原生只收 int ID / Block 映射；YAML 入参是 str，统一归一。
        block = await self._normalize_block(p["block"])
        kwargs: dict[str, Any] = {
            "block": block,
            "sort_begin": int(p.get("start", 0)),
            "sort_count": int(p.get("count", 50)),
        }
        if p.get("sort_id"):
            kwargs["sort_id"] = str(p["sort_id"])
        if p.get("order"):
            kwargs["sort_order"] = _map_str_enum(
                p["order"], _SORT_ORDER_MAP, "order"
            )
        return await self._call(self._mod.rank_block_securities, **kwargs)

    # ========== 问财 WenCai ==========

    async def _tool_ths_query_wencai(self, p: dict) -> Any:
        return await self._call(
            self._mod.query_wencai,
            query=p["query"],
            markets=p.get("markets"),
            limit=int(p.get("limit", 50)),
        )

    async def _tool_ths_query_wencai_securities(self, p: dict) -> Any:
        return await self._call(
            self._mod.query_wencai_securities,
            query=p["query"],
            markets=p.get("markets"),
            limit=int(p.get("limit", 50)),
        )

    async def _tool_ths_list_wencai_hot_blocks(self, p: dict) -> Any:
        kind = _map_str_enum(p.get("kind", "concept"), _WENCAI_KIND_MAP, "kind")
        return await self._call(
            self._mod.list_wencai_hot_blocks,
            kind=kind,
        )

    # ========== 热度 Popularity ==========

    async def _tool_ths_rank_securities_by_popularity(self, p: dict) -> Any:
        return await self._call(
            self._mod.rank_securities_by_popularity,
            type=1,  # realtime
        )

    async def _tool_ths_get_security_popularity_rank(self, p: dict) -> Any:
        return await self._call(
            self._mod.get_security_popularity_rank,
            security=p["security"],
        )

    # ========== 新闻 News ==========

    async def _tool_ths_list_hot_event_news(self, p: dict) -> Any:
        # 原生 list_hot_event_news 必填 block（range 可选）；缺省直接 400 类
        # 错误，不再发一个必然 TypeError 的请求。
        block_raw = p.get("block")
        if not block_raw:
            raise ValueError(
                "block 必填：板块数字 ID（如 61322）或名称（如 沪深300）"
            )
        block = await self._normalize_block(block_raw)
        kwargs: dict[str, Any] = {"block": block}
        if p.get("range"):
            kwargs["range"] = _map_str_enum(
                p["range"], _NEWS_RANGE_MAP, "range"
            )
        return await self._call(self._mod.list_hot_event_news, **kwargs)

    async def _tool_ths_list_security_news_events(self, p: dict) -> Any:
        timeline = str(p.get("timeline", "historical")).strip().lower()
        if timeline not in _VALID_TIMELINES:
            raise ValueError(
                f"timeline 取值非法：{timeline}，可选 historical/realtime"
            )
        start = str(p.get("start") or "").strip()
        end = str(p.get("end") or "").strip()
        if not start or not end:
            raise ValueError(
                "start/end 必填（ISO 时间，如 2026-08-01T00:00:00+08:00）"
            )
        window = {"start": start, "end": end}
        return await self._call(
            self._mod.list_security_news_events,
            security=p["security"],
            timeline=timeline,
            window=window,
        )

    # ========== 实时统计 Realtime ==========

    async def _tool_ths_calculate_security_realtime_statistics(self, p: dict) -> Any:
        # metrics 原生必填；省略时默认下发全部 4 个指标。
        metrics = _map_enum_list(
            p.get("metrics") or list(_REALTIME_METRIC_MAP),
            _REALTIME_METRIC_MAP,
            "metrics",
        )
        return await self._call(
            self._mod.calculate_security_realtime_statistics,
            securities=[p["security"]],
            metrics=metrics,
        )

    async def _tool_ths_get_security_short_term_highlights(self, p: dict) -> Any:
        return await self._call(
            self._mod.get_security_short_term_highlights,
            security=p["security"],
        )

    async def _tool_ths_list_security_price_volume_levels(self, p: dict) -> Any:
        return await self._call(
            self._mod.list_security_price_volume_levels,
            security=p["security"],
        )

    # ========== 公司行为 Corporate ==========

    async def _tool_ths_list_security_corporate_actions(self, p: dict) -> Any:
        # start/end 是相对周期偏移（官方示例 -10..0）；adjust/period 名称映射。
        return await self._call(
            self._mod.list_security_corporate_actions,
            security=p["security"],
            start=int(p.get("start", -10)),
            end=int(p.get("end", 0)),
            adjust=_map_enum(p.get("adjust", "none"), _ADJUST_MAP, "adjust"),
            period=_map_enum(p.get("period", "day"), _PERIOD_MAP, "period"),
        )

    async def _tool_ths_list_security_financial_snapshots(self, p: dict) -> Any:
        # fields 原生必填；省略时默认全部 3 个字段（兑现旧 spec"省略返回默认集"）。
        fields_raw = p.get("fields")
        if fields_raw is None:
            fields = list(_FINANCIAL_FIELD_ALL)
        else:
            if not isinstance(fields_raw, (list, tuple)) or not fields_raw:
                raise ValueError("fields 必须是非空数组")
            fields = [str(f).strip() for f in fields_raw]
            bad = [f for f in fields if f not in _VALID_FINANCIAL_FIELDS]
            if bad:
                raise ValueError(
                    f"fields 含非法字段：{bad}，"
                    f"可选：{', '.join(_FINANCIAL_FIELD_ALL)}"
                )
        return await self._call(
            self._mod.list_security_financial_snapshots,
            securities=p["securities"],
            fields=fields,
        )

    # ========== 涨跌停 Limit ==========

    async def _tool_ths_analyze_security_limit_up(self, p: dict) -> Any:
        return await self._call(
            self._mod.analyze_security_limit_up,
            security=p["security"],
            date=p.get("date"),
        )

    async def _tool_ths_get_security_price_limit_events(self, p: dict) -> Any:
        kwargs: dict[str, Any] = {"security": p["security"]}
        if p.get("start_year"):
            kwargs["start_year"] = int(p["start_year"])
        if p.get("end_year"):
            kwargs["end_year"] = int(p["end_year"])
        return await self._call(
            self._mod.get_security_price_limit_events, **kwargs
        )

    # ========== 跨市场 Cross-Market ==========

    async def _tool_ths_list_futures_related_securities(self, p: dict) -> Any:
        return await self._call(self._mod.list_futures_related_securities)

    async def _tool_ths_list_security_ah_relations(self, p: dict) -> Any:
        return await self._call(
            self._mod.list_security_ah_relations,
            security=p["security"],
        )

    async def _tool_ths_list_security_futures_relations(self, p: dict) -> Any:
        return await self._call(
            self._mod.list_security_futures_relations,
            securities=[p["security"]],
        )

    # ========== 搜索 Search ==========

    async def _tool_ths_search_securities(self, p: dict) -> Any:
        return await self._call(
            self._mod.search_securities,
            pattern=p["keyword"],
            market=p.get("market"),
        )

    async def _tool_ths_resolve_securities(self, p: dict) -> Any:
        raw_codes = p["codes"]
        if not isinstance(raw_codes, (list, tuple)) or not raw_codes:
            raise ValueError("codes 必须是非空数组")
        # 逐元素剥成裸码（坏元素 ValueError 不静默丢弃）；显式市场标记与
        # 数字段推断不一致时 fail-closed（如 sh000001 指数裸码会被解析成
        # 深市股票）；再去重保序。
        codes: list[str] = []
        for c in raw_codes:
            bare = to_bare_code(c)
            explicit = explicit_market_of(c)
            inferred = _inferred_market(bare)
            if explicit and inferred and explicit != inferred:
                raise ValueError(
                    f"代码 {c} 的市场标记（{explicit}）与代码段推断市场"
                    f"（{inferred}）不一致；原生 resolve_securities 只收裸码，"
                    "无法表达该证券（常见于指数代码，如 sh000001 上证指数）"
                )
            codes.append(bare)
        codes = list(dict.fromkeys(codes))
        return await self._call(self._mod.resolve_securities, codes=codes)

    # ========== 行情增强 Quotes ==========

    async def _tool_ths_list_security_ticks(self, p: dict) -> Any:
        # 原生 window 必填：{"count": N} 或 {"start", "end"}（ISO）。
        start = p.get("start_time")
        end = p.get("end_time")
        if start or end:
            if not start or not end:
                raise ValueError("start_time 和 end_time 必须同时提供")
            window = {"start": start, "end": end}
        else:
            window = {"count": int(p.get("count", 100))}
        return await self._call(
            self._mod.list_security_ticks,
            security=p["security"],
            window=window,
        )

    async def _tool_ths_list_security_intraday_bars(self, p: dict) -> Any:
        # date 原生必填；"0" 是当天记号，网关默认补上，spec 可诚实写"省略查当天"。
        return await self._call(
            self._mod.list_security_intraday_bars,
            security=p["security"],
            date=str(p.get("date") or "0"),
        )

    async def _tool_ths_list_security_order_books(self, p: dict) -> Any:
        return await self._call(
            self._mod.list_security_order_books,
            securities=[p["security"]],
            depth=str(p.get("depth") or "5"),  # 原生 OrderBookDepth 仅 "5"
        )

    async def _tool_ths_list_security_daily_capital_flows(self, p: dict) -> Any:
        security = p["security"]
        # 前置识别基金/ETF: 同花顺该接口仅支持 A 股资金分单口径, 基金无此数据。
        # 提前给明确错误, 而不是让原生在编码阶段返回含糊的 "failed to encode response"。
        if is_fund_security(security):
            raise ValueError(
                "同花顺每日资金流向接口仅支持个股（USHA/USZA 的 A 股），"
                "暂不支持 ETF/基金（USHJ/USZJ）、指数、债券"
            )
        return await self._call(
            self._mod.list_security_daily_capital_flows,
            securities=[security],
        )

    # ===== 2026-09-28 新增: 第一档推荐工具（实测可用） =====

    async def _tool_ths_daily_k_lines_with_prev_close(self, p: dict) -> Any:
        # 日线含前收盘价（可算跳空缺口/真实涨跌幅），普通 klines 不给 prev_close。
        count = int(p.get("count", 20))
        return await self._call(
            self._mod.list_security_daily_k_lines_with_previous_close,
            security=p["security"], start=-count, end=0, adjust=0,
        )

    async def _tool_ths_security_research_reports(self, p: dict) -> Any:
        # 个股券商研报（标题/机构/研究员/评级/摘要/链接）。
        return await self._call(
            self._mod.list_security_research_reports,
            security=p["security"], traversal={"mode": "page"},
        )

    async def _tool_ths_index_call_auction_quotes(self, p: dict) -> Any:
        # 指数集合竞价快照。index 为完整代码如 USHI1A0001（上证）/USZI399001（深证）。
        idx = str(p["index"]).strip()
        return await self._call(
            self._mod.list_index_call_auction_quotes,
            index={"security": {"market": idx[:4], "code": idx[4:]}},
            phase=str(p.get("phase") or "opening"),
        )

    async def _tool_ths_index_constituent_contribution(self, p: dict) -> Any:
        # 指数成分按涨跌贡献度排名（谁拉动/拖累指数）。
        count = int(p.get("count", 20))
        return await self._call(
            self._mod.rank_constituents_by_performance_contribution,
            code=str(p["code"]),
            market=str(p.get("market") or "USHI"),
            target="index", sort_id=2708, sort_order="D",
            sort_begin=0, sort_count=count, valid_begin=0, valid_end=0,
        )

    async def _tool_ths_rank_block_by_wencai_field(self, p: dict) -> Any:
        # 板块成分按任意问财字段（换手率/市盈率/ROE…）排名。
        # field 对外只需字段名，网关先经 list_wencai_expression_fields 解析为完整字段对象。
        field_name = str(p["field_name"])
        fields = await self._call(
            self._mod.list_wencai_expression_fields, field_name
        )
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"未找到问财字段：{field_name}")
        return await self._call(
            self._mod.rank_block_securities_by_wencai_field,
            block=int(p["block"]), field=dict(fields[0]),
            offset=0, limit=int(p.get("count", 20)), sort_order="D",
        )

    async def _tool_ths_security_news(self, p: dict) -> Any:
        # 个股分类正式新闻（区别于 ths_news 的 7x24 快讯）。
        return await self._call(
            self._mod.list_news_items,
            category=14339, security=p["security"],
            traversal={"mode": str(p.get("mode") or "since")},
            summary_mode=2, advertorial=False,
        )

    async def _tool_ths_get_market_security_names(self, p: dict) -> Any:
        return await self._call(
            self._mod.get_market_security_names,
            market=p.get("market"),
        )
