# -*- coding: utf-8 -*-
"""snapshot_store.py — 看板预计算快照的存取层（Web 进程与 Worker 进程共用）

设计要点
--------
1. 采集任务运行在独立的 Worker 进程（backend/worker.py）里，Web 进程的内存缓存
   它够不着。所以快照落一张小表 `_dashboard_snapshot`，两边都通过它交换数据。
   不需要 Redis：一次主键查询不到 1ms。
2. Web 进程在内存里保存一份「key → (version, JSON bytes)」。每个 key 最多每
   VERSION_CHECK_SEC 秒查一次库里的 version，变了才重新拉 payload。
   大多数请求零 SQL。
3. payload 存的是**已经序列化好的 JSON 字符串**，接口直接返回 bytes，
   跳过 FastAPI 对大字典做 jsonable_encoder 的开销。
4. ETag = key + version。浏览器带 If-None-Match 时内容没变，直接回 304。
5. 表名以 `_` 开头：通用表浏览器的列表接口会隐藏它（注意 H8：/data 接口
   目前仍然能读，但这里存的都是聚合结果，不含敏感字段）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.database import get_raw_connection

logger = logging.getLogger(__name__)

TABLE = "_dashboard_snapshot"
VERSION_CHECK_SEC = 2.0          # Web 进程多久核对一次库里的 version

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    key            TEXT PRIMARY KEY,
    version        BIGINT NOT NULL,
    computed_at    TEXT   NOT NULL,   -- 快照算好的时间（UTC ISO）
    source_sync_at TEXT,              -- 源数据同步完成的时间（UTC ISO）
    compute_ms     BIGINT,
    payload        TEXT   NOT NULL
)
"""

_ddl_done = False
_ddl_lock = threading.Lock()


def _ensure_table(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    with _ddl_lock:
        if not _ddl_done:
            conn.execute(_DDL)
            conn.commit()
            _ddl_done = True


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(obj: Any) -> str:
    # default=str 兜住 datetime / Decimal / numpy 标量；与现有接口的输出口径保持一致
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# 写入端（Worker 进程：采集任务成功后调用）
# ---------------------------------------------------------------------------
def publish(key: str, compute: Callable[[], Any], source_sync_at: Optional[str] = None) -> int:
    """计算并发布一份快照，返回新 version。

    计算失败时抛异常，旧快照保持不动（调用方负责 catch，不能让它把采集任务标成失败）。
    """
    t0 = time.perf_counter()
    payload = _dumps(compute())
    ms = int((time.perf_counter() - t0) * 1000)
    now = _utcnow_iso()
    with get_raw_connection() as conn:
        _ensure_table(conn)
        # SQLite 3.24+ 与 PG 都支持 ON CONFLICT ... DO UPDATE；version 原子 +1
        conn.execute(
            f"INSERT INTO {TABLE} (key, version, computed_at, source_sync_at, compute_ms, payload) "
            f"VALUES (?, 1, ?, ?, ?, ?) "
            f"ON CONFLICT(key) DO UPDATE SET "
            f"version = {TABLE}.version + 1, computed_at = excluded.computed_at, "
            f"source_sync_at = excluded.source_sync_at, compute_ms = excluded.compute_ms, "
            f"payload = excluded.payload",
            (key, now, source_sync_at, ms, payload),
        )
        cur = conn.execute(f"SELECT version FROM {TABLE} WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.commit()
    version = int(row[0]) if row else 0
    logger.info("[SNAPSHOT] published key=%s v=%s compute=%sms size=%sKB",
                key, version, ms, len(payload) // 1024)
    return version


# ---------------------------------------------------------------------------
# 读取端（Web 进程）
# ---------------------------------------------------------------------------
@dataclass
class Snapshot:
    key: str
    version: int
    computed_at: str
    source_sync_at: Optional[str]
    body: bytes                    # 已序列化的 JSON

    @property
    def etag(self) -> str:
        return f'"{self.key}-v{self.version}"'

    def age_sec(self) -> float:
        try:
            t = datetime.strptime(self.computed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - t).total_seconds()
        except Exception:  # noqa: BLE001
            return float("inf")


_mem: dict[str, Snapshot] = {}
_last_check: dict[str, float] = {}
_mem_lock = threading.Lock()


def get(key: str, force: bool = False) -> Optional[Snapshot]:
    """读快照；库里没有时返回 None（由调用方决定是否现算兜底）。

    force=True 跳过 VERSION_CHECK_SEC 节流，刚 publish 完立刻读时用。
    """
    now = time.monotonic()
    snap = _mem.get(key)
    if not force and snap is not None and now - _last_check.get(key, 0) < VERSION_CHECK_SEC:
        return snap

    try:
        with get_raw_connection() as conn:
            _ensure_table(conn)
            cur = conn.execute(f"SELECT version FROM {TABLE} WHERE key = ?", (key,))
            row = cur.fetchone()
            if row is None:
                _last_check[key] = now
                return snap
            db_version = int(row[0])
            if snap is not None and snap.version == db_version:
                _last_check[key] = now
                return snap
            cur = conn.execute(
                f"SELECT version, computed_at, source_sync_at, payload FROM {TABLE} WHERE key = ?",
                (key,),
            )
            v, computed_at, source_sync_at, payload = cur.fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("[SNAPSHOT] read failed key=%s, serving in-memory copy", key, exc_info=True)
        return snap

    fresh = Snapshot(key, int(v), str(computed_at), source_sync_at, payload.encode("utf-8"))
    with _mem_lock:
        _mem[key] = fresh
        _last_check[key] = now
    return fresh


_parsed: dict[str, tuple[int, Any]] = {}


def peek_payload(key: str) -> Optional[Any]:
    """取快照并反序列化（下钻等需要在内存里再过滤的场景用）。

    按 version 缓存反序列化结果，同一版本只 json.loads 一次。
    """
    snap = get(key)
    if snap is None:
        return None
    hit = _parsed.get(key)
    if hit and hit[0] == snap.version:
        return hit[1]
    obj = json.loads(snap.body)
    _parsed[key] = (snap.version, obj)
    return obj
