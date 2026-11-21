# -*- coding: utf-8 -*-
"""master_site_db 写库预览 / 执行（站点同步操作页专用）。

与 `eflow_site_update_task.py` 的关系：
- 那个任务是**铁律只产文件**（--pg none --cmdb none），每日 07:00 自动跑；
- 本服务是**人工审查后的一次性写库**，不注册定时、不进任务队列，
  只在用户点「预览」→「确认写库」时同步执行，且必须带 require_protected。

流程（防误点、防源表在预览后被换掉）：
1. preview()：跑 build 脚本 `--dry-run --pg upsert --cmdb none`，解析出
   update/insert 预期数；把「源文件名+mtime_ns+size」打成一个 token 返回。
2. apply(token, confirm=True)：重新校验当前最新源文件的指纹与 token 一致，
   再跑 `--pg upsert --cmdb none`（不带 --prune：默认不删站），
   脚本自己会先 pg_backup 到 backup/。

为什么用 subprocess：build 脚本是独立可执行程序（argparse+sys.exit），
且会 sys.stdout.reconfigure / 依赖 psycopg2，不能在长驻后端进程里 import。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[3] / "data" / "eflow" / "cmdb_site_update" / "build_cmdb_site_update_v2.py"
DB_DIR = Path(__file__).resolve().parents[3] / "db"
SUMMARY_JSON = SCRIPT.parent / "modify" / "last_writeback.json"

_LOCK = threading.Lock()
_TIMEOUT = 1800

_RE_SRC = re.compile(r"^源文件\s*:\s*(.+?)\s+\((\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})\)", re.M)
_RE_SRC_LINE = re.compile(r"^源文件\s*:\s*(.+?)\s+\(", re.M)
_RE_PG = re.compile(
    r"PG 写入\s*:\s*update (\d+)\s+insert (\d+)\s+表内 (\d+) -> (\d+)\s+清理 (\d+)")
_RE_BACKUP = re.compile(r"PG 备份\s*:\s*(.+?)\s+\((\d+) 行\)")
_RE_DROPPED = re.compile(r"PG 表里没有这些列，已忽略: (.+)")


def latest_source() -> Path | None:
    """当前 db 目录下最新的 CMDB 主数据 xlsx（与脚本 find_source 同口径）。"""
    cands = [p for p in DB_DIR.glob("ZTE OLR MS Master Site Database*.xlsx")
             if not p.name.startswith("~$") and p.is_file()]
    return max(cands, key=lambda p: (p.stat().st_mtime, p.name)) if cands else None


def source_fingerprint(path: Path) -> str:
    """源文件名 + mtime_ns + size 的短哈希：预览后文件被替换即失效。"""
    st = path.stat()
    raw = f"{path.name}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _run(extra_args: list[str]) -> dict:
    if not SCRIPT.is_file():
        return {"ok": False, "error": f"脚本不存在：{SCRIPT}"}
    cmd = [sys.executable, "-X", "utf8", str(SCRIPT),
           "--src-dir", str(DB_DIR),
           "--pg", "upsert", "--cmdb", "none",
           *extra_args]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            cmd, cwd=str(SCRIPT.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_TIMEOUT,
            env=env, creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"执行超时（>{_TIMEOUT}s）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    out = proc.stdout or ""
    parsed: dict = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "pg": None,
        "backup": None,
        "dropped_cols": [],
        "source_file": None,
        "source_mtime": None,
    }
    if m := _RE_SRC.search(out):
        parsed["source_file"] = m.group(1).strip()
        parsed["source_mtime"] = m.group(2).strip()
    if m := _RE_PG.search(out):
        parsed["pg"] = {
            "update": int(m.group(1)), "insert": int(m.group(2)),
            "before": int(m.group(3)), "after": int(m.group(4)),
            "pruned": int(m.group(5)),
        }
    if m := _RE_BACKUP.search(out):
        parsed["backup"] = {"path": m.group(1).strip(), "rows": int(m.group(2))}
    if m := _RE_DROPPED.search(out):
        parsed["dropped_cols"] = [x.strip() for x in m.group(1).split(",") if x.strip()]
    if not parsed["ok"]:
        parsed["error"] = (out[-3000:] + "\n" + (proc.stderr or "")[-1000:]).strip()
    parsed["stdout_tail"] = out[-3000:]
    return parsed


def preview() -> dict:
    """只算不写：返回预期 update/insert 与令牌（30 分钟糖原表）。"""
    src = latest_source()
    if src is None:
        return {"ok": False, "error": f"{DB_DIR} 下找不到 ZTE OLR MS Master Site Database*.xlsx"}
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "已有站点同步写库操作在运行，请稍后重试"}
    try:
        result = _run(["--dry-run"])
    finally:
        _LOCK.release()
    if not result.get("ok"):
        return result
    fp = source_fingerprint(src)
    result.update({
        "token": fp,
        "source_path": str(src),
        "source_size": src.stat().st_size,
        "note": "预览不写库、不备份；确认写库时脚本会先自动备份 app.master_site_db 到 backup/",
        "prune": False,
    })
    return result


def apply(token: str, confirm: bool) -> dict:
    """带令牌 + confirm 双开关写库。默认不删站（不带 --prune）。"""
    if not confirm:
        return {"ok": False, "error": "缺少 confirm=true，未执行写库"}
    src = latest_source()
    if src is None:
        return {"ok": False, "error": "找不到源文件"}
    if source_fingerprint(src) != token:
        return {"ok": False, "error": "源文件已变化（预览后有人更新了 xlsx），请重新预览后再写库"}

    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "已有站点同步写库操作在运行，请稍后重试"}
    try:
        result = _run([])
    finally:
        _LOCK.release()

    result["token"] = token
    result["source_file"] = src.name
    result["note"] = "已写入 app.master_site_db（update+insert），未删除任何站点；PG 备份见 backup 目录"
    _write_summary(result)
    return result


def _write_summary(payload: dict) -> None:
    try:
        SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
        tmp = SUMMARY_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SUMMARY_JSON)
    except OSError as exc:  # noqa: BLE001
        logger.warning("写库摘要落盘失败: %s", exc)


def last_writeback() -> dict | None:
    try:
        return json.loads(SUMMARY_JSON.read_text(encoding="utf-8")) if SUMMARY_JSON.is_file() else None
    except (OSError, ValueError):
        return None
