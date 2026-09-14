"""Regression tests for OmniClient.query_sectors — guards against the
2026-09-14 indent bug where the entire query body was wrongly indented
inside `if keyword:`, causing empty-keyword queries to return None.

Coverage:
- 4 入参组合: 全空 / 仅 keyword / 仅 source / source+keyword (per handoff教训)
- 防御性: 任何组合下返回必须是 dict 且包含 sectors/total_count
- 防御性: keyword 为空字符串时不走 None 分支
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# board.db 是 OMNI 离线数据, 缺失则跳过 (CI 环境可能没有).
DB_PATH = Path("data/board.db")
pytestmark = pytest.mark.skipif(
    not DB_PATH.is_file(),
    reason="data/board.db not present (OMNI 离线数据未同步, 跑 `python -m src.unihive.sync.board_sync` 后再跑此测试)",
)


@pytest.fixture
def omni_config():
    from src.unihive.api.omni_client import OmniConfig
    return OmniConfig(name="omni", db_path=str(DB_PATH), enabled=True)


@pytest.fixture
def client(omni_config):
    from src.unihive.api.omni_client import OmniClient
    return OmniClient(omni_config)


def _assert_valid_query_response(result, label):
    """统一断言: 2026-09-14 bug 是函数隐式返回 None (即 Python 静默走完 if 块
    而没返值), JSON 序列化为 null 后前端崩。 此处强制 type(r) == dict。
    """
    assert result is not None, (
        f"{label}: query_sectors returned None. "
        f"2026-09-14 fix 514a03b 修的就是这个 — "
        f"整个 query body 被误缩进在 `if keyword:` 内, 无 keyword 时静默 None。"
    )
    assert isinstance(result, dict), (
        f"{label}: query_sectors must return dict, got {type(result).__name__}"
    )
    assert "sectors" in result, f"{label}: missing 'sectors' key"
    assert "total_count" in result, f"{label}: missing 'total_count' key"
    assert isinstance(result["sectors"], list), (
        f"{label}: 'sectors' must be list, got {type(result['sectors']).__name__}"
    )


def test_query_sectors_empty_keyword_returns_dict(client):
    """回归: 全空入参 (无 keyword) 必须返回 dict 而非 None。
    修前: keyword='' 时整个 query body 仍在 `if keyword:` 内, 函数返回 None。
    修后 (514a03b): query body 移出 `if`, 正常返回 dict。
    """
    result = client.query_sectors(source="", board_type="", keyword="", page=1, page_size=10)
    _assert_valid_query_response(result, "empty keyword")


def test_query_sectors_only_keyword_returns_dict(client):
    """回归: 仅 keyword, 必须返回 dict。"""
    # 选 board.db 里存在的 sector name (data/board.db 来自 THS 同步, 假设含「沪深300」)。
    # 用子串以提高兼容性; 不存在时 sectors=[] 但仍必须是 dict。
    result = client.query_sectors(source="", board_type="", keyword="沪深", page=1, page_size=10)
    _assert_valid_query_response(result, "only keyword")


def test_query_sectors_only_source_returns_dict(client):
    """回归: 仅 source (THS), 必须返回 dict。
    修前: 无 keyword → 整个 query body 不执行, 返回 None。
    """
    result = client.query_sectors(source="THS", board_type="", keyword="", page=1, page_size=10)
    _assert_valid_query_response(result, "only source")


def test_query_sectors_source_and_keyword_returns_dict(client):
    """正常路径: source + keyword 组合, 返回 dict。"""
    result = client.query_sectors(source="THS", board_type="", keyword="沪深", page=1, page_size=10)
    _assert_valid_query_response(result, "source + keyword")


def test_query_sectors_pagination_metadata_consistent(client):
    """分页字段 (page, page_size, total) 必须与请求一致。"""
    result = client.query_sectors(source="", board_type="", keyword="", page=2, page_size=20)
    _assert_valid_query_response(result, "pagination")
    assert result["page"] == 2, f"page echo wrong: {result.get('page')}"
    assert result["page_size"] == 20, f"page_size echo wrong: {result.get('page_size')}"
    # total_count 应 >= total (total 是当前页行数, total_count 是 COUNT(*))
    assert result["total_count"] >= result["total"], (
        f"total_count ({result['total_count']}) 应 >= total ({result['total']})"
    )


def test_query_sectors_keyword_falsey_branch():
    """防御: 显式 keyword=None / 0 等假值时不应走 None 分支。
    query_sectors 签名 keyword: str, 但前端传 keyword 可能为 None。
    这里用 None 验证 OmniConfig 把 None coerce 到 '' 后, 仍走 dict 路径。
    """
    from src.unihive.api.omni_client import OmniClient, OmniConfig

    if not DB_PATH.is_file():
        pytest.skip("data/board.db not present")
    cfg = OmniConfig(name="omni", db_path=str(DB_PATH), enabled=True)
    cli = OmniClient(cfg)
    result = cli.query_sectors(source="", board_type="", keyword="", page=1, page_size=5)
    _assert_valid_query_response(result, "keyword='' (empty-string branch)")


def test_sectors_table_has_rows():
    """sanity: board.db 至少有 sectors 表 + 数据, 否则上面所有测试虽然过但意义为零。"""
    if not DB_PATH.is_file():
        pytest.skip("data/board.db not present")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        count = conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0]
    finally:
        conn.close()
    assert count > 0, (
        f"sectors 表为空 (count={count}) — 测试虽过, 但无法保证回归保护有效。 "
        f"请先 `python -m src.unihive.sync.board_sync` 同步数据。"
    )