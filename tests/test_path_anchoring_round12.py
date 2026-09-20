"""Round-12 簇D: runtime 状态路径锚定项目根 + 日志目录不可写时降级。

- logs/gateway.log 相对路径以前按 CWD 解析 → 从其他目录启动网关会在 CWD
  下另建 logs/, 与锚定到项目根的 cache.db/config.db 裂脑;
- log 目录创建/打开失败以前直接抛 OSError, 网关拒启 — 日志是可观测设施,
  不应阻塞进程启动, 降级为 stderr-only;
- cache_stats.json 同样按 CWD 解析, 锚定到项目根。
"""
from __future__ import annotations

import json
import logging
import sys

import pytest

from src.unihive.core import config_db
from src.unihive.storage.cache import Cache, CacheConfig


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


# ---------------------------------------------------------------------------
# log_config 锚定 + 降级
# ---------------------------------------------------------------------------


class TestLogPathAnchoring:
    def test_relative_log_path_anchored_to_project_root(
        self, tmp_path, monkeypatch, restore_root_logger
    ):
        fake_root = tmp_path / "root"
        fake_root.mkdir()
        monkeypatch.setattr(config_db, "project_root", lambda: fake_root)
        # 从完全无关的 CWD 启动
        monkeypatch.chdir(tmp_path)

        from src.unihive.utils.log_config import configure_logging

        configure_logging("stdio")  # 用默认 "logs/gateway.log"
        logging.getLogger("src.test").info("anchored %s", "ok")
        for h in logging.getLogger().handlers:
            h.flush()

        assert (fake_root / "logs" / "gateway.log").is_file()
        # CWD 下不得另建 logs/
        assert not (tmp_path / "logs").exists()

    def test_absolute_log_path_not_rewritten(
        self, tmp_path, monkeypatch, restore_root_logger
    ):
        monkeypatch.setattr(config_db, "project_root", lambda: tmp_path / "root")
        from src.unihive.utils.log_config import configure_logging

        target = tmp_path / "elsewhere" / "gateway.log"
        configure_logging("stdio", log_path=target)
        assert target.parent.is_dir()
        assert not (tmp_path / "root").exists()

    def test_mkdir_failure_degrades_to_stderr_stdio(
        self, tmp_path, monkeypatch, capsys, restore_root_logger
    ):
        """stdio 模式文件 handler 起不来时不得拒启; 应急挂 stderr。"""
        from src.unihive.utils.log_config import configure_logging

        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        impossible = blocker / "logs" / "gateway.log"

        configure_logging("stdio", log_path=impossible)  # 不抛
        root = logging.getLogger()
        streams = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(streams) == 1
        assert streams[0].stream is sys.stderr

        logging.getLogger("src.test").error("still logging %s", "fine")
        for h in root.handlers:
            h.flush()
        captured = capsys.readouterr()
        assert "still logging fine" in captured.err

    def test_mkdir_failure_degrades_to_stderr_http(
        self, tmp_path, restore_root_logger
    ):
        from src.unihive.utils.log_config import configure_logging

        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        configure_logging("http", log_path=blocker / "logs" / "gateway.log")
        streams = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(streams) == 1


# ---------------------------------------------------------------------------
# cache_stats.json 锚定 + 写盘失败不炸热路径
# ---------------------------------------------------------------------------


class TestCacheStatsPath:
    def test_stats_file_anchored_to_project_root(self, tmp_path, monkeypatch):
        fake_root = tmp_path / "root"
        fake_root.mkdir()
        monkeypatch.setattr(config_db, "project_root", lambda: fake_root)
        monkeypatch.chdir(tmp_path)

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        # 无运行中 loop → 走同步刷盘分支; 凑够 FLUSH_INTERVAL 触发一次
        for _ in range(Cache.STATS_FLUSH_INTERVAL):
            cache.record_hit()
        assert (fake_root / "logs" / "cache_stats.json").is_file()
        assert not (tmp_path / "logs").exists()
        data = json.loads((fake_root / "logs" / "cache_stats.json").read_text())
        assert data["hits"] == Cache.STATS_FLUSH_INTERVAL

    def test_stats_write_failure_does_not_raise_from_hot_path(self, tmp_path):
        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        cache.STATS_FILE = str(blocker / "nested" / "cache_stats.json")
        # 旧实现无 loop 同步分支直接把 OSError 抛进 record_hit 热路径
        for _ in range(Cache.STATS_FLUSH_INTERVAL):
            cache.record_hit()  # 不抛
