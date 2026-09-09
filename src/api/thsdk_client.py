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
)
from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)

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


def collect_thscodes(obj: Any) -> list[str]:
    """从 complete_ths_code 的任意返回形态（DataFrame/dict/list）中收集完整代码。"""
    found: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            if _THSCODE_RE.match(o.strip()):
                found.append(o.strip())
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return list(dict.fromkeys(found))




def _decode_gbk_bytes(obj: Any) -> tuple[Any, bool]:
    """Recursively decode GBK-encoded bytes to UTF-8 strings.

    Returns (decoded_obj, had_warnings) tuple. The warnings flag indicates
    whether any GBK decoding was performed.
    """
    had_warnings = False

    if obj is None:
        return None, False

    if isinstance(obj, bytes):
        try:
            # Directly decode GBK to UTF-8
            decoded = obj.decode("gbk")
            return decoded, True
        except UnicodeDecodeError:
            return obj, False

    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            decoded_v, w = _decode_gbk_bytes(v)
            had_warnings = had_warnings or w
            result[k] = decoded_v
        return result, had_warnings

    if isinstance(obj, (list, tuple, set, frozenset)):
        result = []
        for item in obj:
            decoded_item, w = _decode_gbk_bytes(item)
            had_warnings = had_warnings or w
            result.append(decoded_item)
        return type(obj)(result) if isinstance(obj, (tuple, set, frozenset)) else result, had_warnings

    return obj, had_warnings


def _decode_gbk_str(obj: Any) -> tuple[Any, list[str]]:
    """Recursively decode GBK-encoded strings back to UTF-8.

    json_safe converts bytes to str representations (e.g., "b'\\xbf\\xc6...'"),
    so we need to detect those patterns and decode them back to UTF-8.

    Returns (decoded_obj, warnings) tuple.
    """
    warnings: list[str] = []

    if obj is None:
        return None, []

    # Detect str that looks like GBK-encoded bytes: b'\\x..\\x..'
    if isinstance(obj, str):
        if obj.startswith("b'") and obj.endswith("'"):
            byte_str = obj[2:-1]  # Remove b' and '
            try:
                # Reconstruct bytes from hex escapes
                byte_str = byte_str.replace("\\x", "").replace("'", "")
                if len(byte_str) % 2 == 0:
                    # Convert hex string back to bytes
                    byte_data = bytes.fromhex(byte_str)
                    decoded = byte_data.decode("gbk")
                    return decoded, ["decoded GBK string"]
            except (ValueError, UnicodeDecodeError):
                pass
        return obj, []

    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            decoded_v, w = _decode_gbk_str(v)
            warnings.extend(w)
            result[k] = decoded_v
        return result, warnings

    if isinstance(obj, (list, tuple, set, frozenset)):
        result = []
        for item in obj:
            decoded_item, w = _decode_gbk_str(item)
            warnings.extend(w)
            result.append(decoded_item)
        return type(obj)(result) if isinstance(obj, (tuple, set, frozenset)) else result, warnings

    return obj, warnings


def json_safe(obj: Any) -> Any:
    """把 thsdk 返回（DataFrame/dict/标量/numpy 类型/datetime）转成 JSON 安全对象。

    DataFrame（含时间索引）→ list[dict]（records）；datetime/date/Timestamp
    → ISO 字符串；NaN/Inf → None；其余递归转换。GBK 字段名自动解码为 UTF-8。
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

    def __init__(self, interval_sec: float):
        self._interval = max(0.0, interval_sec)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def run(
        self, loop: asyncio.AbstractEventLoop, fn: Callable[[], Any], timeout: float
    ) -> Any:
        async with self._lock:
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, fn), timeout=timeout
                )
            finally:
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
        self._throttle = _Throttle(config.throttle_ms / 1000.0)
        self._auth_lock = asyncio.Lock()
        self._metrics = {"total_requests": 0, "total_errors": 0}

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
            self._status = UpstreamStatus.HEALTHY
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

    # ---------- 调用入口 ----------

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        start_time = time.time()
        self._metrics["total_requests"] += 1

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

        try:
            data = await self._invoke_with_reauth(handler, params, tool_name=tool_name)
            return _result(True, data=data)
        except Exception as e:
            self._metrics["total_errors"] += 1
            err = classify_exception(e)
            logger.warning(
                "[%s] call %s failed: %s: %s",
                self.name,
                tool_name,
                err.error_type.value,
                err.message,
            )
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

    async def _call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """线程池执行同步 thsdk 调用 + 节流 + JSON 转换。"""
        loop = asyncio.get_running_loop()
        raw = await self._throttle.run(
            loop, lambda: fn(*args, **kwargs), self.config.call_timeout_sec
        )
        return json_safe(raw)

    async def _call_with_gbk_decode(self, fn: Callable, *args: Any, **kwargs: Any) -> tuple[Any, list[str]]:
        """Like _call but also handles GBK decoding for watchlist group names.

        Returns (result, warnings) tuple.
        """
        loop = asyncio.get_running_loop()
        raw = await self._throttle.run(
            loop, lambda: fn(*args, **kwargs), self.config.call_timeout_sec
        )
        # M3: 先对 raw 数据做 GBK 解码（处理 bytes），再 json_safe
        decoded, had_warnings = _decode_gbk_bytes(raw)
        result = json_safe(decoded)
        warnings = ["GBK name decoded to UTF-8"] if had_warnings else []
        return result, warnings

    async def _resolve_codes(self, codes: list[str]) -> list[str]:
        """短代码先经 complete_ths_code 补全；返回去重后的完整 THSCODE 列表。"""
        if not codes:
            raise ValueError("securities 列表不能为空")
        complete = [c.strip() for c in codes if is_full_thscode(c)]
        short = [c.strip() for c in codes if not is_full_thscode(c)]
        if short:
            raw = await self._call(self._mod.complete_ths_code, short)
            resolved = collect_thscodes(raw)
            if not resolved:
                raise ValueError(f"短代码无法补全为完整 THSCODE：{short}")
            complete.extend(resolved)
        return list(dict.fromkeys(complete))

    async def list_tools(self) -> list[dict]:
        return []

    async def health_check(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

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
        return await self._call(self._mod.get_account_watchlist)

    async def _tool_ths_get_account_watchlist_groups(self, p: dict) -> Any:
        result, warnings = await self._call_with_gbk_decode(
            self._mod.get_account_watchlist_groups
        )
        if warnings:
            result["_warnings"] = warnings
        return result

    async def _tool_ths_get_all_watchlist(self, p: dict) -> Any:
        """合并主自选(group_id=0)与分组(group_id=35+)为一个统一视图。"""
        wl = await self._call(self._mod.get_account_watchlist)
        groups, warnings = await self._call_with_gbk_decode(
            self._mod.get_account_watchlist_groups
        )

        # 构建 group_id=0 的"默认分组"（主自选）
        default_group = {
            "id": 0,
            "name": "默认分组",
            "securities": wl.get("securities", []),
            "version": wl.get("version"),
        }

        # 转换 groups 为 dict 格式（thsdk 返回的 groups 已是 dict）
        all_groups = {"0": default_group}
        if groups.get("groups"):
            for gid, gdata in groups["groups"].items():
                all_groups[gid] = gdata

        result = {
            "version": groups.get("group_order_version"),
            "group_order": [0] + groups.get("group_order", []),
            "groups": all_groups,
            "main_watchlist": {
                "count": wl.get("count"),
                "securities": wl.get("securities", []),
                "version": wl.get("version"),
            },
        }

        if warnings:
            result["_warnings"] = warnings

        return result

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
        return await self._call(self._mod.klines, p["security"], **kwargs)

    async def _tool_ths_intraday_data(self, p: dict) -> Any:
        return await self._call(
            self._mod.intraday_data, p["security"], date=p.get("date")
        )

    async def _tool_ths_depth(self, p: dict) -> Any:
        return await self._call(self._mod.depth, p["securities"])

    async def _tool_ths_tick_level1(self, p: dict) -> Any:
        kwargs: dict[str, Any] = {"count": int(p.get("count", 100))}
        if p.get("start_time"):
            kwargs["start_time"] = p["start_time"]
        if p.get("end_time"):
            kwargs["end_time"] = p["end_time"]
        return await self._call(self._mod.tick_level1, p["security"], **kwargs)

    async def _tool_ths_corporate_action(self, p: dict) -> Any:
        return await self._call(
            self._mod.corporate_action, p["security"], count=int(p.get("count", 100))
        )

    async def _tool_ths_wencai_nlp(self, p: dict) -> Any:
        return await self._call(
            self._mod.wencai_nlp,
            p["query"],
            markets=p.get("market"),
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
        return await self._call(
            self._mod.block_constituents,
            p["block"],
            start=int(p.get("start", 0)),
            count=int(p.get("count", 50)),
        )

    async def _tool_ths_market_securities(self, p: dict) -> Any:
        page = max(1, int(p.get("page", 1)))
        page_size = int(p.get("page_size", 100))
        return await self._call(
            self._mod.market_securities,
            p["market"],
            start=(page - 1) * page_size,
            count=page_size,
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
