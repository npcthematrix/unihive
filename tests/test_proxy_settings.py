"""ProxySettings 加载/保存/原子写/mtime watch 测试."""
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.unihive.core.proxy_settings import ProxySettings


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    """创建临时 proxy_settings.yaml."""
    p = tmp_path / "proxy_settings.yaml"
    p.write_text("proxy:\n  enabled: false\n", encoding="utf-8")
    return p


class TestProxySettingsLoad:
    def test_load_returns_enabled_false_when_default(self, tmp_yaml):
        s = ProxySettings(tmp_yaml)
        assert s.enabled is False

    def test_load_returns_enabled_true_when_set(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        s = ProxySettings(p)
        assert s.enabled is True

    def test_load_falls_back_to_default_on_missing_file(self, tmp_path):
        """YAML 文件不存在时 fallback 到默认 (enabled=False), 不抛."""
        s = ProxySettings(tmp_path / "nonexistent.yaml")
        assert s.enabled is False

    def test_load_falls_back_to_default_on_corrupt_yaml(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text("proxy: : invalid: yaml:", encoding="utf-8")
        s = ProxySettings(p)
        assert s.enabled is False


class TestProxySettingsSave:
    def test_set_enabled_persists_to_yaml(self, tmp_yaml):
        s = ProxySettings(tmp_yaml)
        s.set_enabled(True)
        assert s.enabled is True
        # 重新读取, 确认落盘
        import yaml
        data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
        assert data["proxy"]["enabled"] is True

    def test_set_enabled_atomic_write_no_partial_file(self, tmp_yaml):
        """原子写: 写 .tmp 后 rename, 不应在主文件存在时出现半截内容."""
        s = ProxySettings(tmp_yaml)
        s.set_enabled(True)
        leftover = list(tmp_yaml.parent.glob("proxy_settings.yaml.tmp"))
        assert leftover == [], f"原子写应清掉 tmp 文件, 残留 {leftover}"


class TestProxySettingsMtimeWatch:
    def test_reload_picks_up_external_yaml_change(self, tmp_yaml):
        """外部编辑 yaml 后, reload() 应读到新值."""
        s = ProxySettings(tmp_yaml)
        assert s.enabled is False
        # 模拟外部编辑 (强制 mtime 变化)
        time.sleep(0.05)
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        s.reload()
        assert s.enabled is True

    def test_reload_is_noop_when_mtime_unchanged(self, tmp_yaml):
        """未修改时 reload() 不重新读取 (cache by mtime)."""
        s = ProxySettings(tmp_yaml)
        # 第二次调用不应抛异常, 即使 mtime 没变
        s.reload()
        assert s.enabled is False

    def test_concurrent_reload_no_lost_updates(self, tmp_yaml):
        """并发 reload 不应丢更新 (thread safety)."""
        s = ProxySettings(tmp_yaml)
        results: list[bool] = []
        errors: list[Exception] = []

        def reload_n_times():
            try:
                for _ in range(50):
                    s.reload()
                    results.append(s.enabled)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reload_n_times) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"reload 并发应不抛, 但有 {errors}"
        assert len(results) == 250  # 5 threads × 50
        assert all(v is False for v in results), "未改 yaml, 应一致 False"
