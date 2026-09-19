"""P3-6: `python -m unihive config upgrade-db` CLI 入口。

用真实出厂 YAML (config/upstreams.yaml) seed 临时库, 验证 dry-run 零漂移、
空库/缺文件/坏 resolutions 文件的退出码, 以及 --apply 幂等 (快照已前推)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.unihive.cli import main

REAL_CONFIG = "config/upstreams.yaml"

pytestmark = pytest.mark.skipif(
    not Path(REAL_CONFIG).exists(),
    reason="factory config/upstreams.yaml not present",
)


def test_upgrade_db_dry_run_zero_drift_after_seed(tmp_path, capsys):
    db = tmp_path / "config.db"
    seed_rc = main(
        ["config", "seed-db", "--db", str(db), "--config", REAL_CONFIG]
    )
    assert seed_rc == 0

    rc = main(
        ["config", "upgrade-db", "--db", str(db), "--config", REAL_CONFIG]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "factory template:" in out
    assert "added=0 removed=0 updated=0" in out


def test_upgrade_db_apply_is_idempotent_and_writes_no_backup(tmp_path, capsys):
    db = tmp_path / "config.db"
    main(["config", "seed-db", "--db", str(db), "--config", REAL_CONFIG])

    rc = main(
        [
            "config", "upgrade-db",
            "--db", str(db),
            "--config", REAL_CONFIG,
            "--apply",
        ]
    )
    assert rc == 0
    assert "already up to date" in capsys.readouterr().out
    # 无变化 → 不建备份目录
    assert not (tmp_path / "backups").exists()


def test_upgrade_db_empty_db_exits_2(tmp_path, capsys):
    db = tmp_path / "config.db"  # 不存在
    rc = main(
        ["config", "upgrade-db", "--db", str(db), "--config", REAL_CONFIG]
    )
    assert rc == 2
    assert "empty" in capsys.readouterr().err.lower()


def test_upgrade_db_missing_config_exits_2(tmp_path, capsys):
    rc = main(
        [
            "config",
            "upgrade-db",
            "--db",
            str(tmp_path / "config.db"),
            "--config",
            str(tmp_path / "nope.yaml"),
        ]
    )
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_upgrade_db_unreadable_resolutions_file_exits_2(tmp_path, capsys):
    db = tmp_path / "config.db"
    main(["config", "seed-db", "--db", str(db), "--config", REAL_CONFIG])
    bad = tmp_path / "resolutions.json"
    bad.write_text("{not json", encoding="utf-8")

    rc = main(
        [
            "config",
            "upgrade-db",
            "--db",
            str(db),
            "--config",
            REAL_CONFIG,
            "--resolutions",
            str(bad),
        ]
    )
    assert rc == 2
    assert "resolutions" in capsys.readouterr().err
