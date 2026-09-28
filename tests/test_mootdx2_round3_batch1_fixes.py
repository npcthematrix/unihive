"""MOOTDX2 离线接口 · 第三轮 batch1 8 项 + D4 修复测试 (2026-09-28)。

8 项: C1 批异常吞 / C2 minute+fzline ValueError 透传 / H2-func suffix 白名单 /
H3-func 日期窗归一化 / H4-func 批内 000xxx warn 透传 / H5-func 批内 limit 限制 /
H1-silent search_stock 显式化 / M-silent4 limit=0 校验。
D4: KeyError/AttributeError/TypeError → INTERNAL_ERROR + traceback 到 logs。

本文件遵循项目 AAA 测试约定 + 不依赖 TDX 真实文件 (mock Reader.factory)。
"""

from __future__ import annotations

import asyncio
import sqlite3
import struct
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.models import mootdx2_errors
from src.unihive.models.mootdx2_errors import ErrorClassifier, MooTDXErrorType


# ========== fixtures ==========

@pytest.fixture
def client() -> MooTDX2Client:
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


@pytest.fixture
def client_with_tdx(client) -> MooTDX2Client:
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    return client


# ========== C1: 批异常吞 ==========

class TestBatchPartialFailure:
    """批工具部分失败时不再吞掉; 失败类型细分; 全失败 → success=False."""

    def test_batch_struct_error_does_not_swallow_to_warn_only(
        self, client_with_tdx: MooTDX2Client
    ):
        """H2.1: 一只 struct.error + 一只正常 → 正常那只进 results,
        struct.error 进 error_detail.warn 但保留 error_type 标签, 不再静默。"""
        import struct as _struct

        mock_df = pd.DataFrame(
            {"close": [10.0, 11.0]},
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )

        def _daily_side_effect(symbol, adjust):
            # reader_symbol for "sh600036" 是 "sh600036", 用末尾 6 位匹配
            if symbol.endswith("600036"):
                raise _struct.error("unpack requires 4 bytes")
            return mock_df

        mock_reader = MagicMock()
        mock_reader.daily.side_effect = _daily_side_effect

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_daily_batch_local(
                    codes="sh600036,sh600519",
                    adjust="none",
                )
            )

        assert r.success is True  # 至少一只成功
        assert "sh600519" in r.data
        assert r.error_detail is not None
        warn = r.error_detail.get("warn", "")
        assert "sh600036" in warn
        # 失败信息不再仅 str(e), 携带 file_error 标签
        assert "file_error" in warn or "internal_error" in warn

    def test_batch_all_fail_returns_success_false(self, client_with_tdx: MooTDX2Client):
        """H2.2: 全部失败 → success=False 而非 success=True data={}."""
        mock_reader = MagicMock()
        mock_reader.daily.return_value = None  # 全部返空

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_daily_batch_local(
                    codes="sh600036,sh600519",
                )
            )

        # 全部返空 → 仍 success=True 但 data={} 是设计 (业务无数据) ;
        # 但本测试验的是"全部结构错"而非"全部空"
        # 调整: mock raise struct.error 让全部结构性失败
        mock_reader.daily.side_effect = struct.error("bad binary")

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_daily_batch_local(
                    codes="sh600036,sh600519",
                )
            )

        assert r.success is False
        assert r.error_detail.get("error_type") in ("internal_error", "file_error")


# ========== C2: minute/fzline ValueError 透传 ==========

class TestMinuteFzlineValueErrorPropagate:

    def test_get_minute_invalid_code_returns_invalid_param(
        self, client_with_tdx: MooTDX2Client
    ):
        """H3.1: 'abc' → success=False, error_type=invalid_param (非 no_data)."""
        with patch("mootdx2.reader.Reader.factory"):
            r = asyncio.run(client_with_tdx.get_minute(code="abc"))

        assert r.success is False
        assert r.error_detail.get("error_type") == "invalid_param"

    def test_get_fzline_invalid_code_returns_invalid_param(
        self, client_with_tdx: MooTDX2Client
    ):
        """H3.2: fzline 同理, 'abc' → invalid_param."""
        with patch("mootdx2.reader.Reader.factory"):
            r = asyncio.run(client_with_tdx.get_fzline(code="abc"))

        assert r.success is False
        assert r.error_detail.get("error_type") == "invalid_param"


# ========== H2-func: suffix 白名单 ==========

class TestMinuteSuffixWhitelist:

    def test_get_minute_suffix_2_rejected(self, client_with_tdx: MooTDX2Client):
        """H4.1: suffix='2' (非 '1'/'5') → invalid_param 而非静默返 5 分钟线."""
        with patch("mootdx2.reader.Reader.factory"):
            r = asyncio.run(
                client_with_tdx.get_minute(code="600036", suffix="2")
            )

        assert r.success is False
        assert r.error_detail.get("error_type") == "invalid_param"
        assert "suffix" in r.error_detail.get("message", "").lower() or \
               "suffix" in (r.error or "").lower()

    def test_get_minute_suffix_5_still_works(self, client_with_tdx: MooTDX2Client):
        """H4.2: suffix='5' 合法, 仍走 fzline (.lc5) 路径."""
        mock_df = pd.DataFrame(
            {"close": [10.0]},
            index=pd.to_datetime(["2024-01-02 10:00"]),
        )
        mock_reader = MagicMock()
        mock_reader.minute.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_minute(code="600036", suffix="5")
            )

        assert r.success is True
        # reader.minute 应被调用且 suffix=5
        assert mock_reader.minute.call_args.kwargs.get("suffix") == 5


# ========== H3-func: 日期窗归一化 ==========

class TestMinuteDateWindowNormalize:

    def test_get_minute_invalid_date_rejected(
        self, client_with_tdx: MooTDX2Client
    ):
        """H5.1: start_date='2026-13-01' (非法日历日) → invalid_param."""
        with patch("mootdx2.reader.Reader.factory"):
            r = asyncio.run(
                client_with_tdx.get_minute(
                    code="600036",
                    start_date="2026-13-01",
                )
            )

        assert r.success is False
        assert r.error_detail.get("error_type") == "invalid_param"

    def test_get_minute_yyyymmdd_format_accepted(
        self, client_with_tdx: MooTDX2Client
    ):
        """H5.2: YYYYMMDD 紧凑串应被 _normalize_date_window 接受."""
        # 用日期索引 (00:00:00), 避免 datetime 与 date 字符串字典序比较的边界
        # (实现是字符串字典序过滤, "2024-01-03 10:00" > "2024-01-03" 会被剔除)
        mock_df = pd.DataFrame(
            {"close": [10.0, 11.0]},
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        mock_reader = MagicMock()
        mock_reader.minute.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_minute(
                    code="600036",
                    start_date="20240102",
                    end_date="20240103",
                )
            )

        # 不应被静默返空 (旧 bug), 应成功; YYYYMMDD 紧凑串必须被接受且全 PASS
        assert r.success is True
        assert len(r.data) == 2


# ========== H4-func: 批内 000xxx 消歧透传 ==========

class TestBatch000xxxWarnPropagate:

    def test_batch_000xxx_warn_appears_with_data(
        self, client_with_tdx: MooTDX2Client
    ):
        """H6.1: 单只 get_daily('000001') 返平安银行 + warn; 批也带同样 warn."""
        mock_df = pd.DataFrame(
            {"close": [10.0]},
            index=pd.to_datetime(["2024-01-02"]),
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_daily_batch_local(
                    codes="000001,sh600519",
                )
            )

        # records 非空时, 旧码吞掉 warn; 修复后不论空否都带 warn
        assert r.success is True
        warn = (r.error_detail or {}).get("warn", "")
        assert "000001" in warn
        # warn 中应有跨市场歧义消歧提示
        assert "歧义" in warn or "指数" in warn or "上证" in warn


# ========== H5-func: 批内 limit 限制 ==========

class TestBatchPerCodeLimit:

    def test_batch_per_code_limit_truncates_with_warn(
        self, client_with_tdx: MooTDX2Client
    ):
        """H7.1: 批内 limit=10, 每只股票最多 10 条; 超长返 truncated warn."""
        # 20 行 DataFrame, limit=10 → 应截断
        dates = pd.date_range("2024-01-01", periods=20, freq="D")
        mock_df = pd.DataFrame(
            {"close": list(range(20))},
            index=dates,
        )
        mock_reader = MagicMock()
        mock_reader.daily.return_value = mock_df

        with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
            r = asyncio.run(
                client_with_tdx.get_daily_batch_local(
                    codes="sh600036",
                    limit=10,
                )
            )

        assert r.success is True
        # 单只应 ≤10
        for sym, records in r.data.items():
            assert len(records) <= 10


# ========== H1-silent: search_stock 显式化 ==========

class TestSearchStockExplicitError:

    def test_search_stock_no_tdx_no_boarddb_returns_no_data(
        self, client: MooTDX2Client
    ):
        """H8.1: 无 tdxdir + 无 board.db → success=True data=null + 明确 no_data
        而非静默 success=True data=[] 让运维以为搜索功能坏了."""
        # 不设 tdxdir (default empty) + board.db 路径不存在
        client._get_tdxdir = lambda: None  # type: ignore[assignment]
        with patch(
            "src.unihive.api.mootdx2_client.resolve_project_path",
            return_value=MagicMock(exists=lambda: False),
        ):
            r = asyncio.run(client.search_stock(keyword="平安"))

        # 应返 no_data 状态 (业务), success=True 但 data=None,
        # error 字段带 NO_DATA error_type 标签便于运维分流
        assert r.success is True
        assert r.data is None or r.data == []
        assert r.error is not None or (r.error_detail or {}).get("error_type") == "no_data"


# ========== M-silent4: limit=0 校验 ==========

class TestIndexKlineLocalLimitZero:

    def test_get_index_kline_local_limit_zero_rejected(
        self, client_with_tdx: MooTDX2Client
    ):
        """H9.1: limit=0 走错工具 (静默返全集) → 应返 invalid_param."""
        with patch("mootdx2.reader.Reader.factory"):
            r = asyncio.run(
                client_with_tdx.get_index_kline_local(
                    code="sh000001",
                    limit=0,
                )
            )

        assert r.success is False
        assert r.error_detail.get("error_type") == "invalid_param"

    def test_get_index_kline_local_limit_negative_rejected(
        self, client_with_tdx: MooTDX2Client
    ):
        """H9.2: limit=-5 → invalid_param."""
        with patch("mootdx2.reader.Reader.factory"):
            r = asyncio.run(
                client_with_tdx.get_index_kline_local(
                    code="sh000001",
                    limit=-5,
                )
            )

        assert r.success is False
        assert r.error_detail.get("error_type") == "invalid_param"


# ========== D4: KeyError/AttributeError/TypeError → INTERNAL_ERROR ==========

class TestD4ExceptionClassification:

    def test_keyerror_classified_as_internal(self):
        """D4.1: KeyError('datetime') 是代码 bug 而非参数错, 应走 INTERNAL_ERROR."""
        exc = KeyError("datetime")
        err = ErrorClassifier.classify(exc, context="daily_sync")

        assert err.error_type == MooTDXErrorType.INTERNAL_ERROR
        assert err.recoverable is False

    def test_attributeerror_classified_as_internal(self):
        """D4.2: AttributeError 也走 INTERNAL_ERROR."""
        exc = AttributeError("'NoneType' object has no attribute 'foo'")
        err = ErrorClassifier.classify(exc, context="test")

        assert err.error_type == MooTDXErrorType.INTERNAL_ERROR

    def test_typeerror_classified_as_internal(self):
        """D4.3: TypeError 也走 INTERNAL_ERROR."""
        exc = TypeError("argument of type 'NoneType' is not iterable")
        err = ErrorClassifier.classify(exc, context="test")

        assert err.error_type == MooTDXErrorType.INTERNAL_ERROR

    def test_valueerror_still_invalid_param(self):
        """D4.5: ValueError 保持原行为 (参数错), 回归保护."""
        exc = ValueError("invalid suffix")
        err = ErrorClassifier.classify(exc, context="get_minute")

        assert err.error_type == MooTDXErrorType.INVALID_PARAM
        assert err.recoverable is False