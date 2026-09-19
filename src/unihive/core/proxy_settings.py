"""Proxy settings loader.

Loads `config/proxy_settings.yaml` and exposes a simple `enabled` bool.
Hot reloads on mtime change. Atomic write via tmp + rename.

Why this is a standalone file and not part of config.yaml / config.db
(2026-09-19 决策):
- Router.route() 在每次工具调用的热路径上读 is_enabled(), 需要逐调用热生效;
  config.yaml 是启动时一次性合并的静态 bootstrap, config.db 的 config_section
  只在启动/reconcile 时装配, 两者都不提供逐调用读语义。
- 控制台热切换必须持久化; config.yaml 含 password_hash/session_secret, 不应
  被控制台写 (整文件 dump 冲掉注释/格式, 定向文本手术已在 P1 刻意删除)。
- 本开关在 db 与 yaml 逃生模式下行为一致 (永远可写), 并入 config.db section
  会让 yaml 模式失效或引入双写; 也刻意不参与 revision/lint/出厂三方合并。
待 proxy 从单 bool 长成多字段配置时, 再评估迁入 config.db + Router 注入钩子。
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {"proxy": {"enabled": False}}


class ProxySettings:
    """Single-bool switch + atomic yaml writer.

    Thread-safe via internal Lock. mtime-based hot reload — no external
    dependency on file-watch library. on_change callback optional.
    """

    def __init__(
        self,
        path: str | Path = "config/proxy_settings.yaml",
        on_change: Any = None,
    ) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._on_change = on_change
        self._enabled: bool = False
        self._mtime: float = 0.0
        # 初次加载 — 失败 fallback 默认, 不抛
        self.reload()

    @property
    def enabled(self) -> bool:
        """当前值 (热 reload 后即时更新)."""
        with self._lock:
            return self._enabled

    def is_enabled(self) -> bool:
        """Router 调用入口."""
        with self._lock:
            # 每次检查时 reload (cheap; mtime 不变则跳过)
            self._reload_locked()
            return self._enabled

    def reload(self) -> None:
        """显式 reload — 公开 API for tests / on_change 触发后."""
        with self._lock:
            self._reload_locked()

    def _reload_locked(self) -> None:
        """Caller holds lock."""
        try:
            if not self._path.exists():
                # 文件不存在 — 默认值, 不更新 mtime
                # (创建文件后下次 reload 会读到)
                return
            mtime = self._path.stat().st_mtime
            if mtime == self._mtime and self._mtime != 0.0:
                return  # 未变化, 跳过
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            proxy = raw.get("proxy") or {}
            new_enabled = bool(proxy.get("enabled", False))
            changed = new_enabled != self._enabled
            self._enabled = new_enabled
            self._mtime = mtime
            if changed and self._on_change is not None:
                try:
                    self._on_change(self._enabled)
                except Exception as e:
                    logger.warning(f"proxy on_change callback failed: {e}")
        except Exception as e:
            logger.warning(
                f"proxy_settings reload failed (using default): {e}"
            )
            # 保留上次有效值, 不强制 reset

    def set_enabled(self, value: bool) -> None:
        """更新内存 + 持久化到 yaml (atomic write)."""
        with self._lock:
            self._enabled = bool(value)
            self._save_locked()

    def _save_locked(self) -> None:
        """Caller holds lock. Atomic write: tmp + rename."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            payload = yaml.safe_dump(
                {"proxy": {"enabled": self._enabled}},
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
            # 更新 mtime 防止 reload() 误判为外部修改
            self._mtime = self._path.stat().st_mtime
        except Exception as e:
            logger.error(f"proxy_settings save failed: {e}")
            raise
