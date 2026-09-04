"""Regression: no 'rhths' (case-insensitive) in active code, config, console, env.

文档类文件 (docs/) 是历史审计追踪, 显式排除.
"""
import re
from pathlib import Path


_SCAN_PATTERNS = (
    "src/**/*.py",
    "config/upstreams.yaml",
    "tests/**/*.py",
    "console.html",
    ".env.example",
    "scripts/**/*.py",
)
_EXCLUDE_PARTS = {"docs", "__pycache__", ".git", ".pytest_cache", "node_modules", "test_migrate_rhths", "migrate_rhths_env"}
_RHTHS = re.compile(r"rhths", re.IGNORECASE)


def test_no_rhths_in_active_code():
    root = Path(".")
    hits: list[str] = []
    # 文件名级别的排除（不含测试文件本身）
    _EXCLUDE_FILES = {"test_no_rhths_stragglers.py", "test_migrate_rhths_env.py", "migrate_rhths_env.py"}
    for pattern in _SCAN_PATTERNS:
        for path in root.glob(pattern):
            # 排除目录
            if any(part in _EXCLUDE_PARTS for part in path.parts):
                continue
            # 排除特定文件
            if path.name in _EXCLUDE_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if _RHTHS.search(line):
                    hits.append(f"{path}:{i}: {line.rstrip()}")
    assert hits == [], (
        "rhths references found in active code (rename incomplete):\n"
        + "\n".join(hits)
    )
