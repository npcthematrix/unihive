"""Round-10 启动审视 · 批 1 (H1 + M1 + M2)。

- H1: wheel 安装 (非 src 布局) 后 /static 挂载不再指向不存在的 4-parents 目录;
  console_api 暴露布局感知的 STATIC_DIR, gateway 挂载它。
- M1: console_api 的 GATEWAY_CONFIG_PATH / CACHE_DB_PATH 与 config_db.project_root()
  在 dev 与 installed 两种布局下解析到同一个运行时根 (不再 4-parents vs 3-parents split-brain)。
- M2: OmniClient.__init__ 不再在 board.db 缺失时静默物化空库; 缺库 → start() UNAVAILABLE;
  写路径 run_sync() 自行 _ensure_db 建表 (board_sync CLI 本来就先 init_db)。
"""
import inspect
import sqlite3
from pathlib import Path

import pytest

from src.unihive.api.omni_client import OmniClient, OmniConfig
from src.unihive.api.upstream_client import UpstreamStatus


# ---------------------------------------------------------------------------
# H1: static 目录解析 (布局感知)
# ---------------------------------------------------------------------------


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def test_h1_static_dir_dev_layout(tmp_path):
    """dev: <repo>/src/unihive/utils/console_api.py + <repo>/static → repo/static。"""
    from src.unihive.utils.console_api import resolve_static_dir

    f = _touch(tmp_path / "src" / "unihive" / "utils" / "console_api.py")
    (tmp_path / "console.html").write_text("x")
    static = tmp_path / "static"
    static.mkdir()
    assert resolve_static_dir(f) == static


def test_h1_static_dir_installed_layout(tmp_path):
    """installed: <site-packages>/unihive/utils/console_api.py, force-include 把
    static/ 放到 site-packages/static → 回落到向上 3 层。"""
    from src.unihive.utils.console_api import resolve_static_dir

    f = _touch(tmp_path / "unihive" / "utils" / "console_api.py")
    (tmp_path / "console.html").write_text("x")
    static = tmp_path / "static"
    static.mkdir()
    assert resolve_static_dir(f) == static


def test_h1_static_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIHIVE_STATIC_DIR", str(tmp_path / "custom-static"))
    from src.unihive.utils.console_api import resolve_static_dir

    f = _touch(tmp_path / "src" / "unihive" / "utils" / "console_api.py")
    assert resolve_static_dir(f) == tmp_path / "custom-static"


def test_h1_gateway_mounts_resolved_static_dir():
    """gateway 装配必须用 _ca.STATIC_DIR, 不再拼 _PROJECT_ROOT / 'static'
    (后者在 installed 布局指向 venv 根 → StaticFiles 构造期 raise, 网关拒启)。"""
    from src.unihive import gateway_server

    src = Path(gateway_server.__file__).read_text(encoding="utf-8")
    assert "_ca.STATIC_DIR" in src, "Mount('/static') must use the layout-aware _ca.STATIC_DIR"
    assert '_PROJECT_ROOT / "static"' not in src, (
        "hard-coded 4-parents static path breaks non-editable wheel installs"
    )


def test_h1_console_api_static_dir_constant_exists_on_disk():
    """dev 运行态: STATIC_DIR 必须真实存在 (StaticFiles 构造要求)。"""
    from src.unihive.utils import console_api

    assert Path(console_api.STATIC_DIR).is_dir()


# ---------------------------------------------------------------------------
# M1: 运行时根在两种布局下唯一, console 常量与 config_db 同根
# ---------------------------------------------------------------------------


def test_m1_resolve_layout_root_dev_layout(tmp_path):
    from src.unihive.core.config_db import resolve_layout_root

    f = _touch(tmp_path / "src" / "unihive" / "core" / "config_db.py")
    assert resolve_layout_root(f) == tmp_path


def test_m1_resolve_layout_root_installed_layout(tmp_path):
    """installed: <site-packages>/unihive/core/config_db.py, site-packages 下无
    src/  → 根 = site-packages (向上 2 层), 而非多爬一层的 venv/Lib。"""
    from src.unihive.core.config_db import resolve_layout_root

    f = _touch(tmp_path / "unihive" / "core" / "config_db.py")
    assert resolve_layout_root(f) == tmp_path


def test_m1_console_runtime_root_matches_config_db_in_dev():
    """dev 运行态: console_api 的运行时根必须与 config_db.project_root() 完全一致
    (这是 split-brain 的回归点: cache.db/config 路径两边必须同根)。"""
    from src.unihive.core.config_db import project_root
    from src.unihive.utils import console_api

    assert Path(console_api._RUNTIME_ROOT) == project_root()


def test_m1_console_paths_anchored_under_runtime_root():
    """关键回归: 控制台侧 CACHE_DB_PATH 必须与网关侧 resolve_project_path(None,
    'logs/cache.db') 解析到同一路径 (旧实现 4-parents vs project_root split-brain);
    GATEWAY_CONFIG_PATH 默认值位于运行时根 /config 下 (env 覆盖除外)。"""
    import os

    from src.unihive.core.config_db import resolve_project_path
    from src.unihive.utils import console_api

    assert Path(console_api.CACHE_DB_PATH) == resolve_project_path(None, "logs/cache.db")

    if not os.environ.get("UNIHIVE_UPSTREAMS_CONFIG"):
        assert Path(console_api.GATEWAY_CONFIG_PATH) == (
            Path(console_api._RUNTIME_ROOT) / "config" / "upstreams.yaml"
        )


# ---------------------------------------------------------------------------
# M2: omni 缺库不再被构造期静默物化
# ---------------------------------------------------------------------------


async def test_m2_init_does_not_materialize_missing_db(tmp_path):
    """旧实现 __init__ 无条件 _ensure_db() → mkdir + 建空 board.db; start() 的
    '文件缺失 → UNAVAILABLE' 分支因此永不触发。修复后缺库保持缺库。"""
    missing = tmp_path / "nested" / "board.db"
    assert not missing.exists()
    OmniClient(OmniConfig(db_path=str(missing)))
    assert not missing.exists(), (
        "__init__ must not silently create an empty board.db at a possibly-wrong path"
    )
    assert not missing.parent.exists(), "must not even mkdir the parent of a misconfigured path"


async def test_m2_start_missing_db_is_unavailable(tmp_path):
    missing = tmp_path / "board.db"
    client = OmniClient(OmniConfig(db_path=str(missing)))
    try:
        ok = await client.start()
        assert ok is True, "start() stays non-fatal (other upstreams continue)"
        assert client._status == UpstreamStatus.UNAVAILABLE
        assert not missing.exists()
    finally:
        await client.stop()


async def test_m2_existing_empty_db_file_is_degraded(tmp_path):
    """文件存在但 0 行 (同步建过表未灌数据 / 老库) → DEGRADED, 且 __init__ 补表。"""
    db = tmp_path / "board.db"
    sqlite3.connect(str(db)).close()  # 物化空文件但不建表
    client = OmniClient(OmniConfig(db_path=str(db)))
    try:
        await client.start()
        assert client._status == UpstreamStatus.DEGRADED
    finally:
        await client.stop()


async def test_m2_run_sync_ensures_schema_before_first_write(tmp_path):
    """写路径 (控制台触发同步) 缺库时必须自行建表, 而非 INSERT 抛 no such table。
    board_sync CLI 在构造 client 前已 init_db, 这里覆盖 HTTP 触发路径。"""
    db = tmp_path / "board.db"
    client = OmniClient(OmniConfig(db_path=str(db)))
    try:
        # 桩掉实际同步分发 (会触网), 只验证 run_sync 写 sync_log 的建表保证
        async def _fake_dispatch(source, board_type):
            return {"status": "success", "message": "ok", "stocks": 0}

        client._dispatch_sync = _fake_dispatch
        result = await client.run_sync("fuyao", "industry")
        assert result["status"] == "success"
        assert db.exists()
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute("SELECT COUNT(*) FROM sector_sync_log").fetchone()
            assert rows[0] == 1
        finally:
            conn.close()
    finally:
        await client.stop()


def test_m2_run_sync_source_calls_ensure_db():
    """静态断言: run_sync 在 _insert_running_log 之前调 _ensure_db。"""
    src = inspect.getsource(OmniClient.run_sync)
    assert src.index("_ensure_db") < src.index("_insert_running_log")
