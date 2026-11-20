# -*- coding: utf-8 -*-
"""data/ 残留回收的边界守卫（2026-11-20 新增）。

背景：2026-11-20 审计发现 `data/cnn_ume/exports/` 里躺着 2026-07-31 的导出文件，
四个月无人发现。随后加了 `backend/services/cleanup/data_residue_cleanup.py`
（由任务 `tmp_cleanup` 每天调用）。这个模块一旦写错就会**删业务数据**，
所以把三条边界锁进测试：

1. 根目录必须是 `my_app/data`（与 `test_cleanup_paths.py` 同类事故：模块搬进
   子包后少退一层，路径飘到 `backend/data` 就会空转或误伤）；
2. `RULES` 里**任何一条都不能指向业务库**（`*.sqlite3` 近 1GB，是看板数据源）；
3. 两段式语义：超期条目只**隔离**不直接删，未超期的不动，隔离期满才 `purge`。
"""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from backend.services.cleanup import data_residue_cleanup as residue


def _touch(p: Path, days_old: int) -> Path:
    """建一个 mtime 位于 N 天前的文件。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    ts = time.time() - days_old * 86400
    os.utime(p, (ts, ts))
    return p


def test_data_dir_is_app_data():
    assert residue.DATA_DIR.name == "data"
    assert residue.DATA_DIR.parent.name == "my_app", "必须落在 my_app/data，不能是 backend/data"


def test_rules_never_target_business_db():
    """规则只允许命中按次产物；业务库（*.sqlite3）一个都不许进。"""
    for rule in residue.RULES:
        assert "sqlite3" not in rule["glob"].lower(), rule
        assert rule["kind"] in ("file", "dir"), rule
        assert rule["days"] >= 1, rule
        assert rule["why"].strip(), "每条规则都必须写明「谁写的、为什么留着没用」"


def test_quarantine_only_stale_entries(tmp_path, monkeypatch):
    """超期 → 隔离（原位置消失、出现在隔离区）；未超期 → 原地不动。"""
    monkeypatch.setattr(residue, "DATA_DIR", tmp_path)
    monkeypatch.setattr(residue, "DRY_RUN", False)

    old = _touch(tmp_path / "cnn_ume" / "exports" / "old.xlsx", 10)
    fresh = _touch(tmp_path / "cnn_ume" / "exports" / "fresh.xlsx", 0)
    # 业务库即使很旧也绝不能动（不在任何规则里）
    db = _touch(tmp_path / "ran_ume" / "ran_kpi_daily.sqlite3", 400)

    stats = residue.quarantine_stale(datetime.now())

    assert stats["quarantined"] == 1, stats
    assert not old.exists(), "超期导出应已移入隔离区"
    assert fresh.exists(), "未超期的不许动"
    assert db.exists(), "业务库不许动"
    quarantined = list((tmp_path / residue._quarantine_dir_name(datetime.now())).rglob("old.xlsx"))
    assert len(quarantined) == 1, "隔离区应保留相对路径，便于原样挪回"


def test_purge_respects_keep_window(tmp_path, monkeypatch):
    """隔离区未满保留期不删；期满才删。"""
    monkeypatch.setattr(residue, "DATA_DIR", tmp_path)
    monkeypatch.setattr(residue, "DRY_RUN", False)
    monkeypatch.setattr(residue, "QUARANTINE_KEEP_DAYS", 30)

    now = datetime.now()
    young = tmp_path / residue._quarantine_dir_name(now - timedelta(days=5))
    old = tmp_path / residue._quarantine_dir_name(now - timedelta(days=40))
    for d in (young, old):
        _touch(d / "cnn_ume" / "exports" / "x.xlsx", 1)

    stats = residue.purge_old_quarantine(now)

    assert stats["purged"] == 1, stats
    assert young.exists(), "未满保留期的隔离区不许删"
    assert not old.exists(), "过期隔离区应被彻底删除"
