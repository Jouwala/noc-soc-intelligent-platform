# -*- coding: utf-8 -*-
"""NGI 数据落 PG（`app` schema）。

## 表

| 表 | 粒度 | 写入方式 |
| --- | --- | --- |
| `app.ngi_lte_cell_param` | 一行一个 LTE 小区（主键 `cell_uid`） | 按 `cell_uid` upsert；本轮没出现的行删除（上游删小区） |
| `app.ngi_nr_cell_param` | 一行一个 NR 小区（主键 `cell_uid`） | 同上 |
| `app.ngi_coverage_grid` | 一行一个 50 m 覆盖格点（主键 `kpi + earthid + offsetx + offsety`） | 按 `kpi` 先删后插（每周全量替换），`run_id` 指向本轮日志 |
| `app.ngi_sync_log` | 一轮一个阶段（`kind=param` / `coverage`） | 成功/失败都写，含行数、耗时、最新任务号、原始错误 |

## 为什么表结构用服务内 DDL 而不是 Alembic

与 `services/RANElementManagement` 一致：这几张表**只由本任务写**，且任务必须
能在任意环境（含尚未跑过迁移的副本）自建。`CREATE TABLE IF NOT EXISTS` 幂等，
并发建表的 TOCTOU 竞态由 `database._PsycopgRaw.executescript` 兜底重试。
改字段映射时**必须同步改 DDL 与索引**（`test_ngi_store_ddl_matches_fields` 会拦）。

## 事务

`get_raw_connection()` 在 PG 模式是 autocommit，所以每阶段都显式
`BEGIN` / `COMMIT`，异常 `ROLLBACK` —— 不会出现"网格表删了但没写回"的半截状态。
工参与覆盖分两个阶段、两个事务：覆盖失败不回滚已成功的工参，但任务整体判失败
（错误信息里带已完成/未完成的部分）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta

logger = logging.getLogger("ngi_store")

LTE_TABLE = "app.ngi_lte_cell_param"
NR_TABLE = "app.ngi_nr_cell_param"
GRID_TABLE = "app.ngi_coverage_grid"
LOG_TABLE = "app.ngi_sync_log"

CHUNK = max(1, int(os.environ.get("NGI_DB_CHUNK", "500")))

# (PG 列, 工参字段名, 转换器)。工参字段名 = ZIP 里第 3 行的英文列名。
# 转换器：s=文本 / f=浮点 / i=整数。
LTE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("rat", "RAT Type", "s"),
    ("subnetwork_id", "SubNetwork ID", "s"),
    ("me_id", "ME ID", "s"),
    ("enodeb_name", "eNodeB Name", "s"),
    ("enodeb_id", "eNodeB ID", "s"),
    ("enodeb_type", "eNodeB type", "s"),
    ("site_lon", "eNodeB Longitude", "f"),
    ("site_lat", "eNodeB Latitude", "f"),
    ("cell_name", "Cell Name", "s"),
    ("cell_id", "Cell ID", "s"),
    ("cell_type", "Cell Type", "s"),
    ("sector_id", "SectorID", "i"),
    ("cell_lon", "Cell Longitude", "f"),
    ("cell_lat", "Cell Latitude", "f"),
    ("active", "Active", "s"),
    ("tac", "TAC", "s"),
    ("plmn_num", "Plmn Num", "s"),
    ("height_m", "Height (m)", "f"),
    ("azimuth", "Azimuth", "f"),
    ("hbwd", "HBWD", "f"),
    ("vbwd", "VBWD", "f"),
    ("mech_downtilt", "Mechanical Downtilt", "f"),
    ("elec_downtilt", "Electrical Downtilt", "f"),
    ("antenna_type", "Antenna Type", "s"),
    ("antenna_gain", "AntennaGAIN (dBi)", "f"),
    ("rs_power", "RS Power (dBm)", "f"),
    ("pci", "PCI", "i"),
    ("coverage_radius_10m", "Coverage Radius (10m)", "i"),
    ("calc_radius_m", "Calculation Radius (m)", "f"),
    ("propagation_model", "Propagation Model", "s"),
    ("cover_character", "Cover Character", "s"),
    ("pci_group_id", "PCI Group ID", "s"),
    ("band_indicator", "Band Indicator for Frequency", "s"),
    ("dl_system_bw", "Downlink System Bandwidth", "s"),
    ("dl_center_freq", "Downlink Center Frequency", "f"),
    ("ul_system_bw", "Uplink System Bandwidth", "s"),
    ("ul_center_freq", "Uplink Center Frequency", "f"),
    ("cell_max_tx_power", "Cell max transmit power", "f"),
    ("cell_tx_power", "Cell Transmit Power", "f"),
    ("mcc", "MCC", "s"),
    ("mnc", "MNC", "s"),
    ("vendor", "Vendor", "s"),
    ("province", "Province_r", "s"),
    ("city", "City_r", "s"),
    ("district", "District_r", "s"),
    ("city_character", "City Character", "s"),
    ("cover_type", "CoverType", "s"),
    ("geo_attribute", "GeoAttribute", "s"),
    ("dl_earfcn", "Dl_earfcn", "s"),
    ("bandclass", "Bandclass", "s"),
)

NR_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("rat", "RATType", "s"),
    ("province", "Province", "s"),
    ("city", "City", "s"),
    ("district", "District", "s"),
    ("gnb_name", "gNodeBName", "s"),
    ("gnb_id", "gNodeBID", "s"),
    ("gnb_lon", "gNodeBLon", "f"),
    ("gnb_lat", "gNodeBLat", "f"),
    ("cell_name", "CellName", "s"),
    ("cell_id", "CellID", "s"),
    ("cell_lon", "CellLongitude", "f"),
    ("cell_lat", "CellLatitude", "f"),
    ("height_m", "Height", "f"),
    ("azimuth", "Azimuth", "f"),
    ("hbwd", "HBWD", "f"),
    ("vbwd", "VBWD", "f"),
    ("rs_power", "RSPower", "f"),
    ("pci", "PCI", "i"),
    ("cover_character", "CoverCharacter", "s"),
    ("dl_bandwidth", "DLBandwidth", "s"),
    ("dl_frequency", "DLFrequency", "f"),
    ("dl_center_freq", "DLCenterFrequency", "f"),
    ("vendor", "Vendor", "s"),
    ("city_character", "CityCharacter", "s"),
    ("ssb_arfcn", "ssb_arfcn", "s"),
    ("ncgi", "NCGI", "s"),
    ("freq_band_index", "FrequencyBandIndex", "s"),
    ("ssb_id", "ssbID", "s"),
    ("beam_azimuth", "BeamAzimuth", "s"),
    ("beam_width_h", "BeamWidthH", "s"),
    ("cell_tx_power", "Cell_TransmitPower", "f"),
    ("trx_type", "transmitter/receiver", "s"),
    ("duplexing_mode", "Duplexing Mode", "s"),
    ("mech_downtilt", "Mechanical Downtilt", "f"),
    ("elec_downtilt", "Electrical Downtilt", "f"),
    ("lte_share_cell", "LTE Sharing Antenna Cell", "s"),
    ("geo_attribute", "GeoAttribute", "s"),
    ("cover_type", "CoverType", "s"),
    ("antenna_type", "AntennaType", "s"),
    ("antenna_gain", "AntennaGAIN", "f"),
    ("ul_band", "ULBand", "s"),
    ("ul_frequency", "ULFrequency", "f"),
    ("max_power", "MAXPower", "f"),
    ("band_class", "BandClass", "s"),
    ("ssb_beam_tilt", "SSBBeamTilt", "f"),
    ("ssb_beam_width_v", "SSBBeamWidthV", "f"),
    ("subcarrier_spacing", "SubCarrierSpacing", "s"),
    ("networking_mode", "NetworkingMode", "s"),
    ("mcc", "MCC", "s"),
    ("mnc", "MNC", "s"),
)

_FIELD_SETS = {"LTE": (LTE_TABLE, LTE_FIELDS), "NR": (NR_TABLE, NR_FIELDS)}

GRID_FIELDS: tuple[tuple[str, str], ...] = (
    ("earthid", "s"),
    ("offsetx", "i"),
    ("offsety", "i"),
    ("bin_index", "i"),
    ("cell_keys", "s"),
    ("pcis", "s"),
    ("top_sample_points", "i"),
    ("total_sample_points", "i"),
    ("rsrp", "f"),
    ("good_ratio", "f"),
    ("center_lon", "f"),
    ("center_lat", "f"),
    ("polygon", "s"),
)


# ---------------------------------------------------------------- 转换器
def _conv(kind: str, v):
    if v is None:
        return None
    if kind == "s":
        s = str(v).strip()
        return s or None
    if kind == "f":
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    if kind == "i":
        if isinstance(v, int):
            return v
        f = _conv("f", v)
        return None if f is None else int(round(f))
    raise ValueError(f"未知转换器: {kind}")


def _column_ddl(name: str, kind: str, nullable: bool = True) -> str:
    t = {"s": "TEXT", "f": "DOUBLE PRECISION", "i": "INTEGER"}[kind]
    if not nullable:
        t += " NOT NULL"
    return f"    {name:<22} {t}"


def _param_ddl(table: str, fields: tuple[tuple[str, str, str], ...]) -> str:
    cols = ",\n".join(
        [_column_ddl("cell_uid", "s", nullable=False)]
        + [_column_ddl(c, k) for c, _h, k in fields]
        + [
            _column_ddl("sector_source", "s"),
            "    snapshot_date          DATE NOT NULL",
            "    fetched_at             TIMESTAMPTZ NOT NULL",
            "    first_seen_at          TIMESTAMPTZ NOT NULL",
            "    raw_json               JSONB",
        ]
    )
    return f"""CREATE TABLE IF NOT EXISTS {table} (
{cols},
    PRIMARY KEY (cell_uid)
);
"""


DDL = f"""
CREATE SCHEMA IF NOT EXISTS app;

{_param_ddl(LTE_TABLE, LTE_FIELDS)}
CREATE INDEX IF NOT EXISTS ix_ngi_lte_param_enb ON {LTE_TABLE} (enodeb_id);
CREATE INDEX IF NOT EXISTS ix_ngi_lte_param_cell ON {LTE_TABLE} (cell_name);
CREATE INDEX IF NOT EXISTS ix_ngi_lte_param_freq ON {LTE_TABLE} (dl_center_freq);
CREATE INDEX IF NOT EXISTS ix_ngi_lte_param_pci ON {LTE_TABLE} (pci);
CREATE INDEX IF NOT EXISTS ix_ngi_lte_param_sector ON {LTE_TABLE} (enodeb_id, sector_id);

{_param_ddl(NR_TABLE, NR_FIELDS)}
CREATE INDEX IF NOT EXISTS ix_ngi_nr_param_gnb ON {NR_TABLE} (gnb_id);
CREATE INDEX IF NOT EXISTS ix_ngi_nr_param_cell ON {NR_TABLE} (cell_name);
CREATE INDEX IF NOT EXISTS ix_ngi_nr_param_pci ON {NR_TABLE} (pci);

CREATE TABLE IF NOT EXISTS {GRID_TABLE} (
    kpi                    TEXT NOT NULL,
    earthid                TEXT NOT NULL,
    offsetx                INTEGER NOT NULL,
    offsety                INTEGER NOT NULL,
    bin_index              SMALLINT,
    cell_keys              TEXT,
    pcis                   TEXT,
    top_sample_points      INTEGER,
    total_sample_points    INTEGER,
    rsrp                   DOUBLE PRECISION,
    good_ratio             DOUBLE PRECISION,
    center_lon             DOUBLE PRECISION,
    center_lat             DOUBLE PRECISION,
    polygon                TEXT,
    run_id                 BIGINT NOT NULL,
    PRIMARY KEY (kpi, earthid, offsetx, offsety)
);
CREATE INDEX IF NOT EXISTS ix_ngi_grid_run ON {GRID_TABLE} (run_id);
CREATE INDEX IF NOT EXISTS ix_ngi_grid_rsrp ON {GRID_TABLE} (kpi, rsrp);

CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
    id                     BIGSERIAL PRIMARY KEY,
    kind                   TEXT NOT NULL,
    started_at             TIMESTAMPTZ NOT NULL,
    finished_at            TIMESTAMPTZ NOT NULL,
    status                 TEXT NOT NULL,
    row_count              INTEGER NOT NULL DEFAULT 0,
    duration_ms            INTEGER,
    message                TEXT,
    auth_source            TEXT,
    task_log_id            TEXT,
    task_log_name          TEXT,
    task_start_time        TIMESTAMP,
    grid_size_m            INTEGER,
    zip_bytes              BIGINT,
    detail                 JSONB
);
CREATE INDEX IF NOT EXISTS ix_ngi_log_kind_time ON {LOG_TABLE} (kind, finished_at DESC);
"""


class NotPgError(RuntimeError):
    """当前 DB_MODE 不是 pg。"""


def ensure_schema() -> None:
    """幂等建表/建索引（DDL 走 autocommit，不进业务写事务）。"""
    from backend.database import DB_MODE, get_raw_connection

    if DB_MODE != "pg":
        raise NotPgError(
            f"NGI 工参会写到 PostgreSQL，但当前 MY_APP_DB_MODE={DB_MODE!r}；"
            "请检查 backend/.env 的 MY_APP_DB_MODE / DATABASE_URL"
        )
    with get_raw_connection() as conn:
        conn.executescript(DDL)


def _bulk_insert(cur, table: str, columns: list, records: list, tail: str = "") -> None:
    """分批多行 INSERT；`?` 占位符由 database 的兼容层转成 psycopg2 的 `%s`。"""
    if not records:
        return
    ph = "(" + ",".join("?" for _ in columns) + ")"
    head = f"INSERT INTO {table} ({','.join(columns)}) VALUES "
    for start in range(0, len(records), CHUNK):
        part = records[start:start + CHUNK]
        sql = head + ",".join(ph for _ in part) + " " + tail
        cur.execute(sql, [rec[c] for rec in part for c in columns])


# ---------------------------------------------------------------- 工参
def normalize_param_records(rat: str, rows: list[dict], snapshot_date: date,
                            fetched_at: datetime) -> list[dict]:
    """工参行 → PG 记录（含 `cell_uid` / `sector_source` / `raw_json`）。"""
    from backend.services.ngi_data.ngi_param import normalize_uid

    table, fields = _FIELD_SETS[rat]
    out: list[dict] = []
    for row in rows:
        rec = {col: _conv(kind, row.get(header)) for col, header, kind in fields}
        rec["cell_uid"] = normalize_uid(rat, row)
        rec["sector_source"] = row.get("_sector_source")
        rec["snapshot_date"] = snapshot_date
        rec["fetched_at"] = fetched_at
        rec["first_seen_at"] = fetched_at
        # raw_json 保留整行（含未建列的字段），键名即第 3 行英文列名
        rec["raw_json"] = json.dumps(
            {k: (None if v is None else str(v)) for k, v in row.items() if not k.startswith("_")},
            ensure_ascii=False, separators=(",", ":"),
        )
        out.append(rec)
    return out


def persist_param(rat: str, records: list[dict], fetched_at: datetime) -> dict:
    """工参整批 upsert + 清理本轮未出现的旧小区，单事务。"""
    from backend.database import get_raw_connection

    table, fields = _FIELD_SETS[rat]
    cols = ["cell_uid"] + [c for c, _h, _k in fields] + [
        "sector_source", "snapshot_date", "fetched_at", "first_seen_at", "raw_json",
    ]
    updatable = [c for c in cols if c not in ("cell_uid", "first_seen_at")]
    upsert = (
        f"ON CONFLICT (cell_uid) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in updatable)
        + f", first_seen_at = {table}.first_seen_at"
    )

    with get_raw_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        try:
            _bulk_insert(cur, table, cols, records, tail=upsert)
            # 上游已删除的小区：本轮没被 upsert 的行 fetched_at 还是旧值
            cur.execute(f"DELETE FROM {table} WHERE fetched_at <> ?", (fetched_at,))
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[NGI] 回滚失败，事务可能悬挂: %s", exc)
            raise
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        total = cur.fetchone()["n"]
    return {"table": table, "rows": len(records), "table_rows": int(total)}


# ---------------------------------------------------------------- 覆盖
def normalize_grid_records(rows: list[dict], kpi: str, run_id: int) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        rec = {col: (_conv(kind, row.get(col)) if kind != "s" else row.get(col))
               for col, kind in GRID_FIELDS}
        rec["kpi"] = kpi
        rec["run_id"] = run_id
        out.append(rec)
    return out


def persist_grid(kpi: str, records: list[dict]) -> dict:
    """按 kpi 先删后插（当前快照语义），单事务。"""
    from backend.database import get_raw_connection

    cols = ["kpi"] + [c for c, _k in GRID_FIELDS] + ["run_id"]
    with get_raw_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        try:
            cur.execute(f"DELETE FROM {GRID_TABLE} WHERE kpi = ?", (kpi,))
            _bulk_insert(cur, GRID_TABLE, cols, records)
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[NGI] 回滚失败，事务可能悬挂: %s", exc)
            raise
        cur.execute(f"SELECT count(*) AS n FROM {GRID_TABLE} WHERE kpi = ?", (kpi,))
        total = cur.fetchone()["n"]
    return {"table": GRID_TABLE, "kpi": kpi, "rows": len(records), "table_rows": int(total)}


# ---------------------------------------------------------------- 日志
def open_log(*, kind: str, started_at: datetime, auth_source: str = "",
             task_log_id: str = "", task_log_name: str = "",
             task_start_time=None) -> int:
    """开一条 `status='running'` 的同步日志并返回 id（覆盖网格的 `run_id` 用它）。

    先开日志再干活的原因：覆盖导出是分钟级异步作业，若只用"结束后写一条"，
    跑到一半崩掉时界面上什么都看不到；先落 running 行，失败时再改 failed，
    排障能直接看到"哪一轮卡在哪一步"。

    代价是进程被强杀（重启/崩溃）时会留下一条永远 `running` 的行。所以每次开
    新日志前把**超过 2 小时**的 `running` 行标成 `failed`（`NGI_STALE_RUNNING_HOURS`
    可调）—— 只动明显过期的行，不会误伤正在跑的并发轮次。
    """
    try:
        from backend.database import get_raw_connection

        with get_raw_connection() as conn:
            cur = conn.cursor()
            hours = float(os.environ.get("NGI_STALE_RUNNING_HOURS", "2"))
            cur.execute(
                f"UPDATE {LOG_TABLE} SET status = 'failed', finished_at = ?,"
                " message = COALESCE(NULLIF(message, ''), '上一轮进程异常退出（未收尾），自动标记')"
                " WHERE status = 'running' AND started_at < ?",
                (datetime.now(), started_at - timedelta(hours=hours)),
            )
            cur.execute(
                f"INSERT INTO {LOG_TABLE} "
                "(kind, started_at, finished_at, status, row_count, auth_source,"
                " task_log_id, task_log_name, task_start_time) "
                "VALUES (?,?,?,?,0,?,?,?,?) RETURNING id",
                (kind, started_at, started_at, "running", auth_source,
                 task_log_id, task_log_name, task_start_time),
            )
            row = cur.fetchone()
        return int(row["id"]) if row else 0
    except Exception as e:  # noqa: BLE001
        logger.warning("[NGI] 开同步日志失败（不影响主流程判定）: %s", e)
        return 0


def finish_log(log_id: int, *, status: str, row_count: int, duration_ms: int,
               message: str = "", auth_source: str | None = None,
               task_log_id: str | None = None, task_log_name: str | None = None,
               task_start_time=None, grid_size_m: int | None = None,
               zip_bytes: int | None = None, detail: dict | None = None) -> None:
    """回填一条日志的终态。写失败只告警，不能掩盖主流程的原始异常。

    任务号 / 网格米数等字段用 `COALESCE` 语义：没传（None）就保留 `open_log`
    时写入的值。覆盖阶段是先开日志、再取最新任务，所以任务号只能在收尾时补。
    """
    if not log_id:
        return
    try:
        from backend.database import get_raw_connection

        with get_raw_connection() as conn:
            conn.execute(
                f"UPDATE {LOG_TABLE} SET finished_at = ?, status = ?, row_count = ?,"
                " duration_ms = ?, message = ?,"
                " auth_source = COALESCE(?, auth_source),"
                " task_log_id = COALESCE(?, task_log_id),"
                " task_log_name = COALESCE(?, task_log_name),"
                " task_start_time = COALESCE(?, task_start_time),"
                " grid_size_m = COALESCE(?, grid_size_m),"
                " zip_bytes = COALESCE(?, zip_bytes), detail = ?"
                " WHERE id = ?",
                (datetime.now(), status, row_count, duration_ms, (message or "")[:2000],
                 auth_source, task_log_id, task_log_name, task_start_time,
                 grid_size_m, zip_bytes,
                 json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
                 log_id),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[NGI] 回填同步日志失败（不影响主流程判定）: %s", e)


def count_table_rows(table: str) -> int:
    from backend.database import get_raw_connection

    with get_raw_connection() as conn:
        row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
    return int(row["n"]) if row else 0


__all__ = [
    "LTE_TABLE", "NR_TABLE", "GRID_TABLE", "LOG_TABLE",
    "LTE_FIELDS", "NR_FIELDS", "GRID_FIELDS", "DDL",
    "ensure_schema", "normalize_param_records", "persist_param",
    "normalize_grid_records", "persist_grid", "open_log", "finish_log",
    "count_table_rows", "NotPgError",
]
