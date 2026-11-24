# -*- coding: utf-8 -*-
"""alarm_snapshot.py — 告警看板的预计算（在 Worker 进程里、采集成功后执行）

产出的快照
----------
| key                     | 内容                                   | 触发时机                  |
|-------------------------|----------------------------------------|---------------------------|
| alarm:overview          | GET /overview 的完整响应                | 实时同步后（每分钟）、历史同步后 |
| alarm:realtime_rows     | 实时告警增强行（下钻在内存里过滤）       | 实时同步后                |
| alarm:history_metrics   | 历史派单时效 5 项指标                   | 历史同步后（每 3 小时）    |
| alarm:recovery_mttr     | 恢复率与 MTTR（按 site_class）           | 历史同步后                |
| alarm:network_avail     | 网络可用率（xlsx）                      | xlsx 修改时间变化时        |
| alarm:alarm_overview    | GET /alarm-overview 的完整响应          | 以上任一更新后            |

另外：历史下钻改为查询物化表 `_history_alarm_enriched`（历史同步后重建）。

口径一致性
----------
所有计算**直接复用** routers/alarm_dashboard.py 里现有的函数，只把执行时机从
"用户请求时"挪到"数据写完后"，不改任何业务口径。第一步建议把这些纯计算函数
机械地搬到 services/alarm/alarm_metrics.py，路由和本模块都从那里 import；
为了让补丁最小，这里先直接 import 路由模块。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import pandas as pd

from backend.database import DB_MODE, get_raw_connection
from backend.dialect_helpers import df_to_sql
from backend.services.dashboard import snapshot_store

logger = logging.getLogger(__name__)

ENRICHED = "_history_alarm_enriched"

HISTORY_OUT_COLS = [
    "tt_id", "site_id", "site_name", "region", "alarm_name", "severity",
    "durationname", "alarm_raised_time", "server_time", "assigned_time_str",
    "assign_delay", "fetch_delay_min", "assign_delay_min", "total_assign_min",
]


def _ad():
    # 延迟导入：避免 Worker 启动时就加载路由模块
    from backend.routers import alarm_dashboard as ad
    return ad


def _sync_label(ts: datetime) -> str:
    """与现有 generated_at 的格式一致：'YYYY-MM-DD HH:MM UTC'。"""
    return ts.strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# 实时告警（eflow_active_alarm 成功后调用）
# ---------------------------------------------------------------------------
def after_realtime_sync() -> None:
    ad = _ad()
    ad._cache.clear()                     # 绕开路由模块里的 60s 内存缓存，保证用的是刚写入的数据
    synced_at = datetime.now(timezone.utc)
    label = _sync_label(synced_at)
    iso = synced_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1) 下钻用的增强行
    snapshot_store.publish("alarm:realtime_rows", ad._load_realtime_rows, source_sync_at=iso)

    # 2) 总览：历史指标直接取历史快照，不在每分钟的实时刷新里重扫 history_alarm
    def _overview():
        hist = snapshot_store.peek_payload("alarm:history_metrics")
        payload = ad._compute_overview_payload(history_metrics=hist)
        # 注意：本次任务还没写进 task_execution_log，_last_alarm_sync_iso() 会拿到上一轮的时间，
        # 所以这里显式覆盖成本次同步时间。
        payload["generated_at"] = label
        return payload

    snapshot_store.publish("alarm:overview", _overview, source_sync_at=iso)

    # 3) 网络可用率：xlsx 变了才重算
    _refresh_network_avail_if_changed()

    # 4) 告警总览
    _publish_alarm_overview(label, iso)


# ---------------------------------------------------------------------------
# 历史告警（eflow_history_alarm 成功后调用）
# ---------------------------------------------------------------------------
def after_history_sync() -> None:
    ad = _ad()
    ad._cache.clear()
    synced_at = datetime.now(timezone.utc)
    label = _sync_label(synced_at)
    iso = synced_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _metrics():
        m = ad._history_metrics_sql()
        if m:
            m["generated_at"] = label
        return m

    snapshot_store.publish("alarm:history_metrics", _metrics, source_sync_at=iso)

    def _mttr():
        with get_raw_connection() as conn:
            return ad._recovery_mttr(conn)

    snapshot_store.publish("alarm:recovery_mttr", _mttr, source_sync_at=iso)

    # 物化表重建失败不能连坐后面的总览发布（2026-11-24 审核：原来一抛异常就跳过下面两步，
    # 本轮历史同步后 overview / alarm_overview 要等到下一分钟才靠实时任务补上）。
    try:
        rebuilt = rebuild_history_enriched()
    except Exception:  # noqa: BLE001
        logger.warning("[SNAPSHOT] 物化表重建失败，历史下钻继续走 pandas 兜底", exc_info=True)
    else:
        # 单独发布一个「物化表已重建」的门禁快照：下钻新鲜度必须以**这张表**为准，
        # 不能拿先发布的 alarm:history_metrics 当门禁（两者之间 rebuild 可能失败）。
        snapshot_store.publish("alarm:history_enriched", lambda: {"rows": rebuilt},
                               source_sync_at=iso)

    # 历史指标是总览的一部分：顺带把总览和告警总览重新发布一次
    after_realtime_sync()


def rebuild_history_enriched() -> int:
    """把历史告警的派生字段（耗时、三个超时标记、格式化时间）算好，落一张带索引的表。

    复用 ad._load_history_df()，和现在下钻的计算逻辑逐字一致；
    只是从"每次下钻在 Web 进程里整表读进 pandas"变成"每 3 小时在 Worker 里算一次"。
    """
    ad = _ad()
    t0 = time.perf_counter()
    df, _ = ad._load_history_df()
    if df is None:
        return 0

    out = df.copy()
    out["alarm_raised_time"] = out["alarmraisedtime"].map(ad._ms_to_str)
    out["server_time"] = out["servertime"].map(ad._ms_to_str)
    out["assigned_time_str"] = out["assigned_time"].map(ad._ms_to_str)
    out["fetch_delay_min"] = out["fetch_delay"].map(ad._ms_to_min)
    out["assign_delay_min"] = out["assign_minus_server"].map(ad._ms_to_min)
    out["total_assign_min"] = out["assign_minus_raise"].map(ad._ms_to_min)
    out["severity"] = out["perceivedseverity"].map(
        lambda v: ad.HIS_SEV_MAP.get(v, f"P{v}" if pd.notna(v) else "--")
    )
    out["alarm_name"] = out["alarm_title"].where(
        out["alarm_title"].notna() & (out["alarm_title"].astype(str).str.strip() != ""),
        out["codename"],
    )
    table = out[HISTORY_OUT_COLS].copy()
    table["servertime_ms"] = out["servertime"]
    table["m_dispatch_delay"] = out["_m_dispatch_delay"].fillna(False).astype(int)
    table["m_fetch_delay"] = out["_m_fetch_delay"].fillna(False).astype(int)
    table["m_overdue"] = out["_m_overdue"].fillna(False).astype(int)

    new = f"{ENRICHED}__new"
    # 纳秒 + 序号：同一秒内连续两次重建（手动重跑 / 两个 Worker 组 / 重试）不会撞索引名。
    # 2026-11-24 审核实测：秒级后缀在同秒第二次调用必失败（PG 42P07 / SQLite index exists），
    # 失败还会留下半截 __new 表。
    suffix = f"{time.time_ns()}"
    with get_raw_connection() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{new}"')
        conn.commit()
        df_to_sql(table, new, conn, if_exists="replace")
        # 下钻的典型查询：WHERE m_xxx = 1 ORDER BY servertime_ms DESC NULLS LAST LIMIT n
        # PG 默认 DESC 时 NULL 排最前，与 pandas sort_values（NaN 排最后）不一致，所以要写 NULLS LAST；
        # SQLite 的索引定义不支持 NULLS LAST（它本来就把 NULL 当最小值），按方言分开写。
        order = "servertime_ms DESC NULLS LAST" if DB_MODE == "pg" else "servertime_ms DESC"
        conn.execute(f'CREATE INDEX "ix_hae_{suffix}_st" ON "{new}" ({order})')
        for flag in ("m_dispatch_delay", "m_fetch_delay", "m_overdue"):
            conn.execute(f'CREATE INDEX "ix_hae_{suffix}_{flag}" ON "{new}" ({flag}, {order})')
        conn.commit()
        # 原子切换：PG 和 SQLite 的 DDL 都支持事务；切换期间读者最多等几毫秒，不会读到"表不存在"
        conn.executescript(
            f'BEGIN; DROP TABLE IF EXISTS "{ENRICHED}"; '
            f'ALTER TABLE "{new}" RENAME TO "{ENRICHED}"; COMMIT;'
        )
    logger.info("[SNAPSHOT] %s rebuilt: %s rows in %.1fs", ENRICHED, len(table), time.perf_counter() - t0)
    return len(table)


# ---------------------------------------------------------------------------
# 告警总览 / 网络可用率
# ---------------------------------------------------------------------------
_last_xlsx_mtime: float | None = None


def _refresh_network_avail_if_changed() -> None:
    global _last_xlsx_mtime
    ad = _ad()
    try:
        mtime = os.path.getmtime(ad._NETWORK_KPI_XLSX)
    except OSError:
        mtime = -1.0
    if mtime == _last_xlsx_mtime and snapshot_store.get("alarm:network_avail") is not None:
        return
    ad._cache.pop("network_availability", None)
    fresh = ad._network_availability()
    rows = (fresh or {}).get("rows") or []
    prev = snapshot_store.peek_payload("alarm:network_avail")
    prev_rows = (prev or {}).get("rows") or []
    if not rows and prev_rows:
        # `_network_availability()` 内部吞异常并返回空 rows（例如 xlsx 正被 Excel 占用）。
        # 这时既不能把它固化进快照，也不能推进 `_last_xlsx_mtime` —— 否则 mtime 不变就
        # 再也不会重试，网络卡片会空到文件下次被改动（2026-11-24 审核实测的阻塞项）。
        logger.warning("[SNAPSHOT] network_avail 读数为空但旧快照有数据，保留旧快照并下轮重试")
        return
    snapshot_store.publish("alarm:network_avail", lambda: fresh)
    _last_xlsx_mtime = mtime


def _publish_alarm_overview(label: str, iso: str) -> None:
    ad = _ad()

    def _build():
        with get_raw_connection() as conn:
            counts = ad._alarm_category_counts(conn)          # realtime_alarm 只有约百行，GROUP BY 很快
        rec_snap = snapshot_store.get("alarm:recovery_mttr")
        recovery = snapshot_store.peek_payload("alarm:recovery_mttr")
        if recovery is None:                                 # 首次部署还没有历史快照
            with get_raw_connection() as conn:
                recovery = ad._recovery_mttr(conn)
        net_snap = snapshot_store.get("alarm:network_avail")
        network = snapshot_store.peek_payload("alarm:network_avail") or ad._network_availability()
        return {
            "generated_at": label,
            "alarm_counts": counts,
            "recovery_mttr": recovery,
            # 子快照各自的时间必须透出：顶层 generated_at 是「本次实时同步」，
            # 而恢复率/MTTR/网络可用率来自历史/文件快照，可能更旧。只给顶层时间
            # 等于让旧数据冒充实时（2026-11-24 审核指出）。
            "recovery_generated_at": rec_snap.computed_at if rec_snap else None,
            "network_availability": network["rows"],
            "network_threshold_pct": network["threshold_pct"],
            "network_latest_month": network["latest_month"],
            "network_generated_at": net_snap.computed_at if net_snap else None,
        }

    snapshot_store.publish("alarm:alarm_overview", _build, source_sync_at=iso)


# ---------------------------------------------------------------------------
# 给采集任务用的安全包装：快照失败只记日志，绝不影响采集任务本身的成功/失败
# ---------------------------------------------------------------------------
_non_pg_warned = False


def safe(fn) -> None:
    """采集任务成功后的安全包装：快照失败绝不影响采集任务本身的成败。

    仅 PostgreSQL 生效：快照链路复用的 `_history_metrics_sql` / `_recovery_mttr` 是
    PG 方言（`~`、`::bigint`，主分支既有实现），SQLite 副本（便携包/冒烟）上会直接
    语法错误。那里不发布快照，看板照旧现算 —— 与「快照只是加速层」的定位一致。
    """
    global _non_pg_warned
    if os.environ.get("ALARM_SNAPSHOT", "on").lower() == "off":
        return
    if DB_MODE != "pg":
        if not _non_pg_warned:
            _non_pg_warned = True
            logger.info("[SNAPSHOT] DB_MODE=%s：快照仅 PG 支持，跳过快照发布（看板走现算）", DB_MODE)
        return
    try:
        fn()
    except Exception:  # noqa: BLE001
        logger.warning("[SNAPSHOT] %s failed; old snapshot kept", getattr(fn, "__name__", fn), exc_info=True)
