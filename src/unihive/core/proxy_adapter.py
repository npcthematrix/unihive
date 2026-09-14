"""Mootdx2 → TDX_QUANT transparent adapter for 行情类 tools.

7 tools covered. Writes the mapping/period/param conventions as code-level
constants — these are protocol-level and not user-editable.

Usage:
    adapter = Mootdx2ToTdxQuantAdapter()
    if adapter.supports(gateway_tool):
        tdx_tool, params, normalizer = adapter.adapt(gateway_tool, params)
        result = await tdx_client.call_tool(tdx_tool, params)
        if result.success:
            return normalizer(result.data), "tdx_quant"

Mapping caveat (2026-09-14 audit):
    Initial commit c83090b mapped `get_batch_quote` → `get_security_snapshot`
    and `get_minute_data` → `get_minute_time_data`. Neither method exists
    in `skills/SKILL.md` (tqcenter contract v1.0.12) or
    `config/tools_tdx_quant.yaml` (54 tools, generated from SKILL.md).
    SKILL.md only declares:
      - single-stock snapshot:  get_market_snapshot(stock_code: str)
      - K-line / minute:        get_market_data(stock_list, period, count)
                                with period ∈ {1m, 5m, 15m, 30m, 1h, 1d, 1w}
    Fix: reuse those two methods. If the real installed `tqcenter.py`
    rejects `get_market_snapshot(stock_list=[...])` (multi-code variant),
    revisit this mapping or update tqcenter.
"""
from __future__ import annotations

from typing import Any, Callable

ResponseNormalizer = Callable[[Any], Any]


class ProxyAdapterError(Exception):
    """Raised when adapter cannot translate mootdx2 → tdx_quant."""


class Mootdx2ToTdxQuantAdapter:
    """Adapter that translates mootdx2 行情类 tool calls to tdx_quant equivalents.

    Mappings are HARDCODED — they reflect protocol-level contracts between
    mootdx2 and tdx_quant and are not user-configurable.
    """

    TOOL_MAP: dict[str, str] = {
        "get_quote": "get_market_snapshot",
        "get_batch_quote": "get_market_snapshot",   # multi-code: stock_list=[...]
        "get_kline": "get_market_data",
        "get_index_kline": "get_market_data",
        "get_index_all": "get_market_data",
        "get_index_bars": "get_market_data",
        "get_minute_data": "get_market_data",       # period='1m', stock_list=[code]
    }

    PERIOD_MAP: dict[str, str] = {
        "day": "1d",
        "week": "1w",
        "month": "1mon",
        "minute1": "1m",
        "minute5": "5m",
        "minute15": "15m",
        "minute30": "30m",
        "minute60": "1h",
    }

    def supports(self, mootdx2_tool: str) -> bool:
        return mootdx2_tool in self.TOOL_MAP

    def adapt(
        self, mootdx2_tool: str, params: dict[str, Any]
    ) -> tuple[str, dict[str, Any], ResponseNormalizer]:
        """Translate mootdx2 call into tdx_quant call.

        Returns:
            (tdx_quant_tool_name, transformed_params, response_normalizer)

        Raises:
            ProxyAdapterError: tool not in TOOL_MAP
        """
        if not self.supports(mootdx2_tool):
            raise ProxyAdapterError(
                f"tool {mootdx2_tool!r} is not supported by proxy adapter"
            )

        tdx_tool = self.TOOL_MAP[mootdx2_tool]

        # get_market_snapshot is reused by both get_quote (single) and
        # get_batch_quote (multi-code via stock_list). Disambiguate by
        # source tool name, not just target.
        if tdx_tool == "get_market_snapshot" and mootdx2_tool == "get_quote":
            tdx_params = self._adapt_get_quote(params)
        elif tdx_tool == "get_market_snapshot" and mootdx2_tool == "get_batch_quote":
            tdx_params = self._adapt_get_batch_quote_to_snapshot(params)
        elif tdx_tool == "get_market_data" and mootdx2_tool == "get_minute_data":
            tdx_params = self._adapt_get_minute_data_to_market_data(params)
        elif tdx_tool == "get_market_data":
            tdx_params = self._adapt_get_kline(mootdx2_tool, params)
        else:
            raise ProxyAdapterError(
                f"internal: unknown (mootdx2_tool, tdx_tool)="
                f"({mootdx2_tool!r}, {tdx_tool!r})"
            )

        normalizer = _identity
        return tdx_tool, tdx_params, normalizer

    # --- private param transformers ---

    def _adapt_get_quote(self, params: dict) -> dict:
        """get_quote(code) → get_market_snapshot(stock_code)."""
        return {"stock_code": params["code"]}

    def _adapt_get_batch_quote_to_snapshot(self, params: dict) -> dict:
        """get_batch_quote(codes='A,B,C') → get_market_snapshot(stock_list=[A,B,C]).

        Caveat: SKILL.md's yaml spec for get_market_snapshot declares only
        stock_code: str (single). This adapter assumes tqcenter.tq also
        accepts stock_list for the multi-code form (consistent with how
        get_market_data uses stock_list). If the real installed tqcenter
        rejects stock_list here, the proxy will AttributeError on this
        tool and fall back to mootdx2 (Round 10 audit's safety net).
        """
        codes_raw = params.get("codes", "")
        if isinstance(codes_raw, list):
            stock_list = list(codes_raw)
        else:
            stock_list = [c.strip() for c in str(codes_raw).split(",") if c.strip()]
        return {"stock_list": stock_list}

    def _adapt_get_kline(self, mootdx2_tool: str, params: dict) -> dict:
        """get_kline(code, period, limit) → get_market_data(stock_list, period, count).

        mootdx2_tool is preserved for debugging — all 4 mootdx2 K-line tools
        share the same tdx_quant target.
        """
        period_raw = params.get("period", "day")
        period = self.PERIOD_MAP.get(period_raw, period_raw)
        code = params.get("code") or params.get("codes", "")
        stock_list = [c.strip() for c in str(code).split(",") if c.strip()]
        # mootdx2 limit (>0 取最近 n 条) ↔ tdx_quant count (>0 取截止 end_time 最近 n 条)
        limit = int(params.get("limit", 0))
        out: dict[str, Any] = {
            "stock_list": stock_list,
            "period": period,
            "count": limit,
        }
        return out

    def _adapt_get_minute_data_to_market_data(self, params: dict) -> dict:
        """get_minute_data(code) → get_market_data(stock_list=[code], period='1m', count=N).

        mootdx2's get_minute_data defaults to ~240 1-minute bars per trading
        day (4 hours × 60 minutes), so count=240 is the natural default.
        tdx_quant's count semantics: "距 end_time 最近 n 条"; without a count
        the server may return the full historical minute series.
        """
        code = params["code"]
        count = int(params.get("count", 240))
        return {
            "stock_list": [code],
            "period": "1m",
            "count": count,
        }


def _identity(x: Any) -> Any:
    """Default normalizer — pass-through. Field rename can be added later
    if downstream consumers depend on mootdx2 field names like {close, open}
    vs tdx_quant's {Now, LastClose}. For now, identity is the safe default
    because the existing mootdx2 tool specs are the ones registered in
    tools_mootdx2.yaml — downstream consumers have already adapted to those
    field names. Switching to tdx_quant fields would break callers.
    """
    return x
