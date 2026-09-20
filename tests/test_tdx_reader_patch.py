"""tdx_reader_patch 契约测试：bj（北交所）文件路径路由。

上游 mootdx2 1.5.x ``find_path`` 两个缺陷（见 tdx_reader_patch.py 模块
docstring）：
  1. 显式 bj 前缀被 get_stock_market 误判 sh，拼出 shbj{code} 垃圾路径
  2. bj 分支不补市场前缀，裸码找的是 bj/lday/{code}.day，
     与标准布局 bj/lday/bj{code}.day 不符
外加北交所 920 新号段被 ``9 → sh`` 兜底误判。

路由契约用 tmp_path 合成空文件验证（find_path 只检查存在性）；
真实二进制解析用开发机 mock 目录做端到端验证（缺失则 skip）。
"""
from pathlib import Path

import pytest

from mootdx2.reader import Reader

from src.unihive.api.tdx_reader_patch import patch_reader_bj_paths

_MOCK_TDX = Path("D:/new_tdx_mock")


def _touch(tmp_path: Path, rel: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


@pytest.fixture
def patched_reader(tmp_path):
    files = [
        "vipdoc/bj/lday/bj899050.day",
        "vipdoc/bj/lday/bj430047.day",
        "vipdoc/bj/lday/bj920000.day",
        "vipdoc/bj/minline/bj810011.lc1",
        "vipdoc/bj/fzline/bj810011.lc5",
        "vipdoc/sh/lday/sh000001.day",
        "vipdoc/sh/lday/sh881001.day",
        "vipdoc/sz/lday/sz000001.day",
    ]
    for rel in files:
        _touch(tmp_path, rel)
    reader = Reader.factory(market="std", tdxdir=str(tmp_path))
    patch_reader_bj_paths(reader)
    return reader


class TestBJDailyRouting:
    @pytest.mark.parametrize("symbol", ["bj899050", "899050"])
    def test_bj_index_routes_to_bj_prefixed_file(self, patched_reader, tmp_path, symbol):
        p = patched_reader.find_path(symbol, subdir="lday", suffix="day")
        assert p == tmp_path / "vipdoc/bj/lday/bj899050.day"

    @pytest.mark.parametrize("symbol", ["bj430047", "430047"])
    def test_bj_430_stock_routing(self, patched_reader, tmp_path, symbol):
        p = patched_reader.find_path(symbol, subdir="lday", suffix="day")
        assert p == tmp_path / "vipdoc/bj/lday/bj430047.day"

    @pytest.mark.parametrize("symbol", ["bj920000", "920000"])
    def test_bj_920_new_segment(self, patched_reader, tmp_path, symbol):
        # 上游 get_stock_market 会把裸 920xxx 兜底成 sh
        p = patched_reader.find_path(symbol, subdir="lday", suffix="day")
        assert p == tmp_path / "vipdoc/bj/lday/bj920000.day"

    def test_bj_minute_and_fzline(self, patched_reader, tmp_path):
        assert (patched_reader.find_path("bj810011", subdir="minline", suffix=["lc1", "1"])
                == tmp_path / "vipdoc/bj/minline/bj810011.lc1")
        assert (patched_reader.find_path("810011", subdir="fzline", suffix=["lc5", "5"])
                == tmp_path / "vipdoc/bj/fzline/bj810011.lc5")


class TestSHSZRegression:
    def test_sh_index_explicit_and_sz_bare_000(self, patched_reader, tmp_path):
        assert (patched_reader.find_path("sh000001", suffix="day")
                == tmp_path / "vipdoc/sh/lday/sh000001.day")
        # 裸 000001 按 get_stock_market 推断 sz（歧义消歧在 index_registry 层）
        assert (patched_reader.find_path("000001", suffix="day")
                == tmp_path / "vipdoc/sz/lday/sz000001.day")

    def test_881_sector_index_routes_sh(self, patched_reader, tmp_path):
        assert (patched_reader.find_path("881001", suffix="day")
                == tmp_path / "vipdoc/sh/lday/sh881001.day")

    def test_missing_file_returns_none(self, patched_reader):
        assert patched_reader.find_path("600000", suffix="day") is None
        assert patched_reader.find_path("bj810999", suffix="day") is None

    def test_debug_returns_market_symbol_tuple(self, patched_reader):
        assert patched_reader.find_path(
            "bj899050", subdir="lday", suffix="day", debug=True
        ) == ("bj", "bj899050", ["day"])


def test_patch_is_idempotent(tmp_path):
    _touch(tmp_path, "vipdoc/bj/lday/bj899050.day")
    reader = Reader.factory(market="std", tdxdir=str(tmp_path))
    patch_reader_bj_paths(reader)
    first = reader.find_path
    patch_reader_bj_paths(reader)
    assert reader.find_path is first
    assert reader.find_path("899050", suffix="day") == tmp_path / "vipdoc/bj/lday/bj899050.day"


def test_patch_skips_non_reader_object_without_error():
    """factory 返回裸 object() 替身时补丁静默跳过（不设属性、不抛异常）。"""
    sentinel = object()
    assert patch_reader_bj_paths(sentinel) is None
    assert not hasattr(sentinel, "_unihive_bj_patch")


def test_upstream_without_patch_bj_is_broken(tmp_path):
    """记录上游基线：不打补丁时 bj 显式/裸码都读不到（防回归误判）。"""
    _touch(tmp_path, "vipdoc/bj/lday/bj899050.day")
    reader = Reader.factory(market="std", tdxdir=str(tmp_path))
    assert reader.find_path("bj899050", suffix="day") is None
    assert reader.find_path("899050", suffix="day") is None


@pytest.mark.skipif(not _MOCK_TDX.exists(), reason="需要开发机 mock TDX 目录")
class TestEndToEndOnMockDir:
    """真实二进制解析（find_path 路由 + bar reader）。"""

    @pytest.fixture
    def real_reader(self):
        reader = Reader.factory(market="std", tdxdir=str(_MOCK_TDX))
        patch_reader_bj_paths(reader)
        return reader

    def test_bj_index_daily(self, real_reader):
        df = real_reader.daily(symbol="bj899050", adjust="none")
        assert df is not None and len(df) > 0

    def test_bj_stock_daily_bare(self, real_reader):
        df = real_reader.daily(symbol="810011", adjust="none")
        assert df is not None and len(df) > 0

    def test_bj_minute_lines(self, real_reader):
        assert len(real_reader.minute(symbol="bj810011", suffix=1)) > 0
        assert len(real_reader.minute(symbol="bj810011", suffix=5)) > 0

    def test_sh_sz_still_readable(self, real_reader):
        assert len(real_reader.daily(symbol="sh000001", adjust="none")) > 0
        assert len(real_reader.daily(symbol="sz000001", adjust="none")) > 0
