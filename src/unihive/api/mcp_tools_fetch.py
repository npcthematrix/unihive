"""原始 MCP tools/list 拉取 —— 含全量游标分页与错误分类。

为什么单独一个模块, 而不是复用各 client 的 ``list_tools()``:

1. 那些 ``list_tools()`` 只读 ``result["tools"]``, 丢弃同级的 ``result["nextCursor"]``。
   THS-LOCAL-GATEWAY 实测分 3 页 (32/32/23 = 87 个工具), 所以旧实现静默返回了
   87 个中的 32 个 —— 这正是控制台 "32 tools" 的由来。
2. 它们 ``except Exception: return []``, 于是 "上游炸了" 和 "上游就是没有工具"
   塌缩成同一个值。控制台必须区分这两者, 事后无法补救。
3. 它们不检查 HTTP 状态码, 4xx/5xx 会被喂给 ``response.json()`` 再塌缩成 ``[]``。
4. 它们只返回 ``list[dict]``, 没有页数 / 字节数 / 截断标记。

本模块只负责 "把上游说的话完整带回来"。tools 列表原样透传, 不做任何
重命名、平铺或归一化 —— 控制台 MCP 浏览器展示的就是这份原始 payload。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 错误分类。前端按这些字符串分支, 改动即破坏契约。
ERROR_NOT_INITIALIZED = "not_initialized"
ERROR_TRANSPORT = "transport"
ERROR_TIMEOUT = "timeout"
ERROR_HTTP = "http"
ERROR_JSONRPC = "jsonrpc"
ERROR_MALFORMED = "malformed"

# 截断原因。与错误不同 —— 截断返回的是**部分数据 + 警告**, 不是失败。
TRUNCATED_PAGE_CAP = "page_cap"
TRUNCATED_MAX_TOOLS = "max_tools"
TRUNCATED_MAX_BYTES = "max_bytes"
TRUNCATED_CURSOR_LOOP = "cursor_loop"

DEFAULT_PAGE_CAP = 20
DEFAULT_MAX_TOOLS = 2000
DEFAULT_MAX_BYTES = 8_000_000


@dataclass(frozen=True)
class RawToolsResult:
    """一次原始 tools/list 拉取的完整结果。

    ``ok=False`` 时 ``tools`` 一定是空的 —— 只有传输层/协议层故障才 ok=False。
    截断(命中上限)属于 ``ok=True`` + ``truncated=True``: 截断的原始列表比一个
    错误页有用得多。
    """

    ok: bool
    tools: list[dict] = field(default_factory=list)
    pages: int = 0
    truncated: bool = False
    truncated_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None
    total_bytes: int = 0

    @staticmethod
    def failed(kind: str, message: str) -> "RawToolsResult":
        return RawToolsResult(ok=False, error=message, error_kind=kind)


def _normalize_cursor(raw: Any) -> str | None:
    """把 nextCursor 归一成非空字符串, 否则 None 表示 "没有下一页"。

    实测 THS-LOCAL 返回的是 JSON 字符串 ('32' / '64'), 但规范没规定类型,
    所以 int 也要收; 空串和空白串一律当作结束。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw if raw.strip() else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(raw)
    return None


def _truncated(reason: str, tools: list[dict], pages: int, total_bytes: int) -> RawToolsResult:
    return RawToolsResult(
        ok=True,
        tools=tools,
        pages=pages,
        truncated=True,
        truncated_reason=reason,
        total_bytes=total_bytes,
    )


async def fetch_all_raw_tools(
    http_client: httpx.AsyncClient | None,
    url: str,
    upstream: str,
    *,
    headers: dict[str, str] | None = None,
    page_cap: int = DEFAULT_PAGE_CAP,
    max_tools: int = DEFAULT_MAX_TOOLS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> RawToolsResult:
    """循环拉取上游 tools/list 直到 nextCursor 耗尽或命中上限。

    ``headers`` 每一页都会重新传入, 绝不能提升到循环外 —— 否则 fuyao 的
    ``X-api-key`` 会在第 2 页起丢失。
    """
    if http_client is None:
        return RawToolsResult.failed(
            ERROR_NOT_INITIALIZED, f"[{upstream}] HTTP client not initialized"
        )

    tools: list[dict] = []
    total_bytes = 0
    pages = 0
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while True:
        params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": params,
        }

        try:
            response = await http_client.post(url, json=request, headers=headers)
        except httpx.TimeoutException as e:
            return RawToolsResult.failed(
                ERROR_TIMEOUT, f"[{upstream}] Request timeout: {e}"
            )
        except httpx.HTTPError as e:
            # ConnectError / ReadError 等。与 timeout 分开归类, 便于排障时
            # 一眼看出是 "连不上" 还是 "连上了但太慢"。
            return RawToolsResult.failed(
                ERROR_TRANSPORT, f"[{upstream}] Transport error: {e}"
            )

        total_bytes += len(response.content)

        if response.status_code >= 400:
            body = response.text[:200]
            return RawToolsResult.failed(
                ERROR_HTTP,
                f"[{upstream}] HTTP {response.status_code}: {body}",
            )

        try:
            data = response.json()
        except ValueError as e:
            return RawToolsResult.failed(
                ERROR_MALFORMED, f"[{upstream}] Response is not JSON: {e}"
            )

        if isinstance(data, dict) and data.get("error") is not None:
            return RawToolsResult.failed(
                ERROR_JSONRPC, f"[{upstream}] JSON-RPC error: {data['error']}"
            )

        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return RawToolsResult.failed(
                ERROR_MALFORMED, f"[{upstream}] Missing result.tools in response"
            )

        tools.extend(result["tools"])
        pages += 1

        next_cursor = _normalize_cursor(result.get("nextCursor"))

        # 先判自然结束, 再判上限 —— 否则最后一页会被误报成截断。
        if next_cursor is None:
            return RawToolsResult(ok=True, tools=tools, pages=pages, total_bytes=total_bytes)

        if next_cursor in seen_cursors:
            logger.warning(
                f"[{upstream}] tools/list cursor repeated ({next_cursor!r}); "
                f"stopping to avoid an infinite loop"
            )
            return _truncated(TRUNCATED_CURSOR_LOOP, tools, pages, total_bytes)

        if pages >= page_cap:
            return _truncated(TRUNCATED_PAGE_CAP, tools, pages, total_bytes)
        if len(tools) >= max_tools:
            return _truncated(TRUNCATED_MAX_TOOLS, tools, pages, total_bytes)
        if total_bytes >= max_bytes:
            return _truncated(TRUNCATED_MAX_BYTES, tools, pages, total_bytes)

        seen_cursors.add(next_cursor)
        cursor = next_cursor
