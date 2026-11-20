# -*- coding: utf-8 -*-
"""data/ 目录「按次产物」回收（2026-11-20 新增，随任务 tmp_cleanup 一起跑）

背景
----
2026-11-20 排查 ``data/cnn_ume/exports/`` 时发现：2026-07-31 的两个 UME 导出
xlsx 一直留到 11 月。根因不是「忘了写清理代码」，是三件事叠在一起：

  1. 写入方改成内存模式后不再落盘（``ccn_ume_kpi_service.export_history``
     注释「不落盘」），旧落盘遗留再没人读；
  2. 唯一的删除逻辑只删「自己刚读到的那一个」（同文件 ``files_to_delete``），
     没人读的文件永远不会被删 —— 清理逻辑和「还会被读到」绑死，就必然漏；
  3. ``data/`` 整目录被根 ``.gitignore`` 的 ``data/`` 挡住，``git status``
     看不到，代码审查、仓库整洁度守卫、pre-commit 全部照不到。

顺着这条线一次性盘点出 6 类同类残留（见 ``RULES``），所以把「data/ 下的
按次产物」统一交给本模块回收，而不是散落到 8 个业务模块各写一遍：

  - 各业务模块只负责「写」，保留期策略集中在这里，加一类只加一行 RULES；
  - 业务模块改版（内存模式、换目录、任务停用）后遗留的旧文件，这里照样兜住。

两条硬约束
----------
- **只处理 RULES 里显式列出的路径**。``data/`` 下绝大多数文件是业务库
  （``ran_kpi_avalability_1day.sqlite3`` 近 1GB）、报表源、镜像库，
  通用「超过 N 天就删」在这里是灾难，不是策略。
- **两段式（同 ``tmp_cleanup``）**：先同盘 ``mv`` 进
  ``data/_quarantine_YYYYMMDD/``（保留相对路径，可原样挪回），
  隔离再超 ``QUARANTINE_KEEP_DAYS`` 天（默认 30）才真删。

安全边界
--------
  - 只作用于 ``<项目根>/data``，不碰 TMP（那是 ``tmp_cleanup`` 的事）；
  - 目录形态的规则（临时 profile）取子树内 mtime 最大值，正在写的不动；
  - 被进程占用导致 mv/删除失败 → 记日志跳过，绝不强制；
  - ``DATA_RESIDUE_DRY_RUN=1`` 只报告不改动，供首次上线验证。

环境变量
--------
  DATA_RESIDUE_DRY_RUN        =1 只报告不改动（默认 0）
  DATA_RESIDUE_KEEP_DAYS      隔离区二次保留天数（默认 30）
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("data_residue_cleanup")

# 与 tmp_cleanup/log_cleanup 同：backend/services/cleanup/ 深三层，parents[3] 才是 my_app 根。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
QUARANTINE_PREFIX = "_quarantine_"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


QUARANTINE_KEEP_DAYS = _env_int("DATA_RESIDUE_KEEP_DAYS", 30)
DRY_RUN = os.environ.get("DATA_RESIDUE_DRY_RUN", "0") == "1"

# 规则表：glob 相对 DATA_DIR；days = 最后活动时间超过多少天才回收。
# 每条都必须能说清「这是谁写的、为什么留着没用」，说不清的不许加进来。
RULES = [
    {
        # 必须写成 `**/*`：pathlib 里以 `**` 结尾的 pattern **只匹配目录、不匹配文件**
        # （实测 `*/exports/**` → 只出 ['exports', 'exports/sub']，文件一个都不出）。
        # 写成 `*/exports/**` 会让本条规则静默空转 —— 正是本项目反复踩的「守卫看着
        # 通过、其实什么都没查」那类坑。tests/test_data_residue_cleanup.py 锁住这点。
        "glob": "*/exports/**/*",
        "days": 3,
        "kind": "file",
        "why": "各网管（UME/BI）导出下载的中间产物，任务只用当次那份；"
               "cnn_ume/exports 的 2026-07-31 遗留就是这么攒下来的",
    },
    {
        "glob": "portal_profiles/_tmp_*",
        "days": 2,
        "kind": "dir",
        "why": "Playwright 登录用的临时浏览器配置，仅在登录进程存活期间有效；"
               "进程被杀时 portal_login_service 的 finally 跑不到，实测残留 215MB/份",
    },
    {
        "glob": "native_board_mirror/*.corrupt_*",
        "days": 7,
        "kind": "file",
        "why": "镜像库损坏时的手工快照（单份 628MB），只供事故取证；"
               "镜像重建并 quick_check=ok 后即无用途",
    },
    {
        "glob": "olib_soc_report/*.summary.json",
        "days": 30,
        "kind": "file",
        "why": "olib_soc_report_service 每次运行写一份摘要；运行记录已在 "
               "report_runs 表里，落盘副本只用于当时排查",
    },
    {
        "glob": "ip_trace/*.csv",
        "days": 30,
        "kind": "file",
        "why": "用户手动触发的 IP 追踪留档，ip_trace_service 写盘后无任何保留期逻辑",
    },
    {
        "glob": "ip_trace/*.json",
        "days": 30,
        "kind": "file",
        "why": "同上（csv 与 json 成对产出，需同时回收）",
    },
    {
        "glob": "_remote_kpi/**/*.xlsx",
        "days": 30,
        "kind": "file",
        "why": "远端 KPI 接口下载的 xlsx 缓存，按次落盘、无上限；"
               "ran_availability 在用目录每次刷新会更新 mtime，不受影响",
    },
]


def _entry_size(p: Path) -> int:
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for name in files:
            try:
                total += Path(root, name).stat().st_size
            except OSError as exc:
                logger.debug("统计残留大小失败: %s", exc)
    return total


def _latest_mtime(p: Path) -> float:
    """目录取子树内 mtime 最大值；读不到子项视为「正在用」，返回 inf 不回收。"""
    try:
        latest = p.stat().st_mtime
    except OSError:
        return float("inf")
    if p.is_file():
        return latest
    try:
        for root, dirs, files in os.walk(p):
            for name in dirs + files:
                try:
                    latest = max(latest, Path(root, name).stat().st_mtime)
                except OSError:
                    return float("inf")
    except OSError:
        return float("inf")
    return latest


def _iter_candidates(rule: dict):
    """按规则展开候选；只接受规则声明的那种形态（file/dir）。"""
    if not DATA_DIR.is_dir():
        return
    for p in DATA_DIR.glob(rule["glob"]):
        try:
            if p.is_symlink():
                continue
            if rule["kind"] == "file" and not p.is_file():
                continue
            if rule["kind"] == "dir" and not p.is_dir():
                continue
        except OSError:
            continue
        yield p


def _quarantine_dir_name(now: datetime) -> str:
    return f"{QUARANTINE_PREFIX}{now:%Y%m%d}"


def _move_to_quarantine(p: Path, now: datetime) -> Path:
    """同盘 mv 到隔离区，保留相对 data/ 的路径，避免同名互相覆盖。"""
    rel = p.relative_to(DATA_DIR)
    dest = DATA_DIR / _quarantine_dir_name(now) / rel
    seq = 0
    while dest.exists():
        seq += 1
        dest = dest.parent / f"{rel.name}.{seq}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest))
    return dest


def quarantine_stale(now: datetime | None = None) -> dict:
    """把超过各规则保留期的条目移入隔离区。"""
    now = now or datetime.now()
    detail, skipped = [], []
    moved = 0
    freed = 0
    for rule in RULES:
        cutoff = now - timedelta(days=rule["days"])
        for p in sorted(_iter_candidates(rule)):
            latest = _latest_mtime(p)
            if latest == float("inf"):
                skipped.append(f"{p.relative_to(DATA_DIR)} 读不到子项，视为在用")
                continue
            if datetime.fromtimestamp(latest) >= cutoff:
                continue
            size = _entry_size(p)
            try:
                dest = _move_to_quarantine(p, now) if not DRY_RUN else None
            except (OSError, PermissionError, shutil.Error) as e:
                skipped.append(f"{p.relative_to(DATA_DIR)} 仍被占用/失败: {type(e).__name__}")
                logger.warning("[data_residue] 隔离失败 %s: %s", p, e)
                continue
            moved += 1
            freed += size
            age = (now - datetime.fromtimestamp(latest)).days
            target = dest.parent.name if dest else _quarantine_dir_name(now)
            detail.append(
                f"{p.relative_to(DATA_DIR)} ({size / 1048576:.2f}MB, {age}天前) -> {target}/"
            )
    return {"quarantined": moved, "quarantine_mb": round(freed / 1048576, 2),
            "detail": detail, "skipped": skipped}


def purge_old_quarantine(now: datetime | None = None) -> dict:
    """彻底删除隔离超过 QUARANTINE_KEEP_DAYS 天的 _quarantine_ 目录。"""
    now = now or datetime.now()
    purged, freed, detail = 0, 0, []
    if not DATA_DIR.is_dir():
        return {"purged": 0, "purged_mb": 0.0, "detail": detail}
    for p in sorted(DATA_DIR.glob(f"{QUARANTINE_PREFIX}*")):
        if not p.is_dir() or len(p.name) != len(QUARANTINE_PREFIX) + 8:
            continue
        try:
            qdate = datetime.strptime(p.name[len(QUARANTINE_PREFIX):], "%Y%m%d")
        except ValueError:
            continue
        if qdate > now - timedelta(days=QUARANTINE_KEEP_DAYS):
            continue
        size = _entry_size(p)
        try:
            if not DRY_RUN:
                shutil.rmtree(p)
        except (OSError, PermissionError) as e:
            detail.append(f"{p.name} 删除失败: {type(e).__name__}")
            logger.warning("[data_residue] 隔离区删除失败 %s: %s", p, e)
            continue
        purged += 1
        freed += size
        detail.append(f"{p.name} ({size / 1048576:.2f}MB, 隔离于 {qdate:%Y-%m-%d})")
    return {"purged": purged, "purged_mb": round(freed / 1048576, 2), "detail": detail}


def run_data_residue_cleanup() -> dict:
    """回收 data/ 下按 RULES 判定的残留，返回汇总 dict（禁 sys.exit）。"""
    start = time.time()
    errors: list = []
    try:
        q = quarantine_stale()
    except Exception as e:  # noqa: BLE001
        logger.warning("[data_residue] 隔离阶段失败: %s", e, exc_info=True)
        q = {"quarantined": 0, "quarantine_mb": 0.0, "detail": [], "skipped": []}
        errors.append(f"隔离阶段异常: {type(e).__name__}: {e}")
    try:
        pg = purge_old_quarantine()
    except Exception as e:  # noqa: BLE001
        logger.warning("[data_residue] 隔离区清空失败: %s", e, exc_info=True)
        pg = {"purged": 0, "purged_mb": 0.0, "detail": []}
        errors.append(f"清空阶段异常: {type(e).__name__}: {e}")

    summary = {
        "success": not errors,
        "data_dir": str(DATA_DIR),
        "dry_run": DRY_RUN,
        "rules": [f"{r['glob']} > {r['days']}天" for r in RULES],
        "quarantine_keep_days": QUARANTINE_KEEP_DAYS,
        "quarantined": q["quarantined"],
        "quarantine_mb": q["quarantine_mb"],
        "quarantine_detail": q["detail"][:30],
        "skipped": q["skipped"][:10],
        "purged": pg["purged"],
        "purged_mb": pg["purged_mb"],
        "purge_detail": pg["detail"][:30],
        "errors": errors,
        "elapsed_seconds": round(time.time() - start, 2),
        "message": (
            f"data 残留清理：隔离 {q['quarantined']} 项 {q['quarantine_mb']:.1f}MB、"
            f"彻底删除过期隔离 {pg['purged']} 项 {pg['purged_mb']:.1f}MB"
            + ("[DRY RUN 未实际改动]" if DRY_RUN else "")
        ),
    }
    logger.info(summary["message"])
    return summary


if __name__ == "__main__":
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug("设置 stdout 编码失败: %s", exc)
    t0 = time.time()
    print(json.dumps(run_data_residue_cleanup(), ensure_ascii=False, indent=2))
    print(f"总运行时间: {time.time() - t0:.2f} 秒")
