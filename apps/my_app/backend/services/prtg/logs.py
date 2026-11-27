# -*- coding: utf-8 -*-
"""PRTG 日志采集、分类与派生指标（P0-1 / P0-2，2026-11-26）。

为什么要单独采日志
------------------
2026-11-26 两轮只读摸底（``TMP/prtg_survey``）实测：PRTG 14.2 的日志
（``table.json?content=messages``）**只保留约 30 天**（2963 条，``start>=10000``
起全空；``treesize=1000000`` 是假总数，不能拿来判断还有多少页）。而状态快照
（``prtg_sensor_status``）只到分钟、没有原因文本。日志是唯一带
「ICMP 超时 / SNMP 无响应 / unusually low…」原因的数据源，不采就随 PRTG 滚动丢失。

口径
----
- 日志行的 ``objid`` 是**传感器 id**（与 ``prtg_sensors.objid`` 一致，摸底已对齐），
  ``parent`` 是设备名，``name`` 是传感器名；
- 时间取 ``datetime_raw``（Excel 序列日）。PRTG 服务器时钟实测为 UTC
  （getstatus ``Clock`` 与本机 UTC 一致），入库 **naive UTC**，接口层补 ``Z``；
- 去重键 ``sha1(objid | datetime_raw | status_raw | message)``：重叠翻页、重复运行都幂等；
- 分类 ``reason`` 是纯文本规则（``classify_message``），规则与样例见
  ``docs/workspace/prtg-logs.md``；PRTG 换版本改文案时只改这里。
- 本地保留 ``PRTG_LOG_RETENTION_DAYS``（默认 730 天），**不跟随** 90 天原始采样清理。

开关：``PRTG_MESSAGES_SYNC_ENABLED``（默认 1）；置 0 后 runner 不再拉日志、不再合并事件，
其余采集不受影响。该变量读自 ``backend/.env``（进程启动时加载），**改后需重启 backend**
才会生效（runner 在 ``run_forever`` 入口读一次，不热切换）。
"""
import hashlib
import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, func, select

from backend.models.prtg_logs import PrtgLogMessage
from backend.services.prtg import store
from backend.services.prtg.parsing import excel_serial_to_datetime

# ---------------- 配置 ----------------

LOG_COLUMNS = ("objid", "datetime", "parent", "type", "name", "status", "message")
PAGE_SIZE = 2000
MAX_PAGES = 10  # treesize 是假总数，必须有硬上限
OVERLAP_MINUTES = 10  # 增量翻页与已入库最新时间的重叠区，靠 dedup_key 去重
MESSAGES_INTERVAL_SECONDS = 300

ENV_ENABLED = "PRTG_MESSAGES_SYNC_ENABLED"
ENV_RETENTION_DAYS = "PRTG_LOG_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 730

# ---------------- 原因分类 ----------------

REASON_ICMP_TIMEOUT = "icmp_timeout"
REASON_SNMP_NO_RESPONSE = "snmp_no_response"
REASON_UNUSUAL_LOW = "unusual_low"
REASON_UNUSUAL_HIGH = "unusual_high"
REASON_PAUSED = "paused"
REASON_VALUE = "value"
REASON_OTHER = "other"
REASONS = (
    REASON_ICMP_TIMEOUT, REASON_SNMP_NO_RESPONSE, REASON_UNUSUAL_LOW,
    REASON_UNUSUAL_HIGH, REASON_PAUSED, REASON_VALUE, REASON_OTHER,
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_VALUE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(msec|kbit/s|mbit/s|gbit/s|#/s|%)", re.I)
_UNUSUAL = re.compile(
    r"(?P<window>\d+\s+(?:minute|hour|day)s?)\s+interval average of\s+"
    r"(?P<value>-?[\d,]+(?:\.\d+)?)\s*(?P<unit>kbit/s|mbit/s|gbit/s|msec|%)\s*"
    r"\((?P<channel>[^)]+)\)\s+is unusually\s+(?P<direction>low|high)"
    r"(?:\s+for this\s+(?P<period>[^.<]+))?",
    re.I,
)
_TO_KBIT = {"kbit/s": 1.0, "mbit/s": 1000.0, "gbit/s": 1_000_000.0}


def sync_enabled(environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get(ENV_ENABLED) or "1").strip().lower() in ("1", "true", "yes", "on")


def retention_days(environ: dict | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        value = int(str(env.get(ENV_RETENTION_DAYS) or DEFAULT_RETENTION_DAYS).strip())
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return value if value > 0 else DEFAULT_RETENTION_DAYS


def strip_html(text: Any) -> str:
    """去 HTML 标签与实体、压空白（PRTG 的 message 列带 <div class="status">…）。"""
    if text is None:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", str(text)))).strip()


def classify_message(message: str | None) -> str:
    """日志原文 → reason。顺序即优先级，命中即停（摸底样例见单测）。"""
    m = (message or "").lower()
    if "icmp error" in m or "request timed out" in m:
        return REASON_ICMP_TIMEOUT
    if "snmp error" in m or "no response" in m:
        return REASON_SNMP_NO_RESPONSE
    if "unusually low" in m:
        return REASON_UNUSUAL_LOW
    if "unusually high" in m:
        return REASON_UNUSUAL_HIGH
    if "paused" in m:
        return REASON_PAUSED
    if _VALUE.search(m):
        return REASON_VALUE
    return REASON_OTHER


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_value(message: str | None) -> tuple[float | None, str | None]:
    """取原文里的第一个「数字 + 单位」，如 "6 msec" → (6.0, "msec")；单位原样小写。"""
    match = _VALUE.search(message or "")
    if not match:
        return None, None
    return _number(match.group(1)), match.group(2).lower()


def parse_unusual(message: str | None) -> dict[str, Any] | None:
    """PRTG 基线判定原文 → 结构化字段；不是 unusually low/high 文案时返回 None。

    例："1 day interval average of 213,373 kbit/s (Traffic Out) is unusually low for this weekday"
    """
    match = _UNUSUAL.search(message or "")
    if not match:
        return None
    unit = match.group("unit").lower()
    value = _number(match.group("value"))
    value_mbps = None
    if value is not None and unit in _TO_KBIT:
        value_mbps = round(value * _TO_KBIT[unit] / 1000.0, 3)
    period = match.group("period")
    return {
        "window": _WS.sub(" ", match.group("window")).lower(),
        "value": value,
        "unit": unit,
        "value_mbps": value_mbps,
        "channel": match.group("channel").strip(),
        "direction": match.group("direction").lower(),
        "period": period.strip().lower() if period else None,
    }


def make_dedup_key(sensor_objid: int | None, datetime_raw: float,
                   status_raw: int | None, message: str) -> str:
    raw = f"{sensor_objid}|{datetime_raw:.8f}|{status_raw}|{message}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def serial_to_utc(serial: float) -> datetime:
    """Excel 序列日 → naive UTC，四舍五入到秒（序列日的浮点尾差会落在 .999 秒）。"""
    value = excel_serial_to_datetime(serial) + timedelta(microseconds=500_000)
    return value.replace(microsecond=0)


def normalize_row(row: dict[str, Any], collected_at: datetime) -> dict[str, Any] | None:
    """PRTG messages 一行 → prtg_log_messages 一行；没有可解析时间的行丢弃（返回 None）。"""
    try:
        serial = float(row.get("datetime_raw"))
    except (TypeError, ValueError):
        return None
    message = strip_html(row.get("message_raw") or row.get("message"))
    status = (str(row.get("status") or "").strip()) or None
    status_raw = _to_int(row.get("status_raw"))
    sensor_objid = _to_int(row.get("objid"))
    reason = classify_message(message)
    value_num, value_unit = parse_value(message) if reason == REASON_VALUE else (None, None)
    return {
        "sensor_objid": sensor_objid,
        "device_name": (str(row.get("parent") or "").strip()) or None,
        "sensor_name": (str(row.get("name") or "").strip()) or None,
        "sensor_type": (str(row.get("type") or "").strip()) or None,
        "happened_at": serial_to_utc(serial),
        "datetime_raw": serial,
        "status": status,
        "status_raw": status_raw,
        "message": message,
        "reason": reason,
        "value_num": value_num,
        "value_unit": value_unit,
        "dedup_key": make_dedup_key(sensor_objid, serial, status_raw, message),
        "collected_at": collected_at,
    }


def _load_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text, strict=False)
    except ValueError:
        # PRTG 14 的 JSON 偶有非法反斜杠转义（摸底遇到过），补成 \\ 后再解析
        payload = json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text), strict=False)
    if not isinstance(payload, dict):
        raise ValueError("PRTG messages 响应不是 JSON 对象")
    return payload


# ---------------- 持久化 ----------------

def fetch_page(client, start: int, count: int = PAGE_SIZE) -> list[dict[str, Any]]:
    text = client.table_json("messages", columns=list(LOG_COLUMNS), count=count, start=start)
    rows = _load_json(text).get("messages")
    if not isinstance(rows, list):
        raise ValueError("PRTG messages 响应缺少 messages 数组")
    return rows


def insert_rows(rows: Iterable[dict[str, Any]]) -> int:
    """按 dedup_key 幂等写入，返回**真正新增**的行数。"""
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["dedup_key"], row)
    if not unique:
        return 0
    keys = list(unique)
    with store.SessionLocal() as session:
        existing: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            existing.update(
                k for (k,) in session.execute(
                    select(PrtgLogMessage.dedup_key).where(PrtgLogMessage.dedup_key.in_(chunk))
                ).all()
            )
        fresh = [unique[k] for k in keys if k not in existing]
        if fresh:
            store.upsert(session, PrtgLogMessage, fresh, ["dedup_key"])
        session.commit()
    return len(fresh)


def latest_happened_at() -> datetime | None:
    with store.SessionLocal() as session:
        return session.execute(select(func.max(PrtgLogMessage.happened_at))).scalar()


def sync_messages(client, now: datetime, *, max_pages: int = MAX_PAGES,
                  page_size: int = PAGE_SIZE, overlap_minutes: int = OVERLAP_MINUTES) -> dict[str, Any]:
    """增量拉日志（新→旧翻页）。

    停止条件（任一）：空页 / 不满一页 / 本页最老一行早于「已入库最新 − 重叠区」/ 达到 max_pages。
    首次运行库里为空，没有游标，会一直翻到空页 —— 即一次性存下 PRTG 现存的约 30 天。
    """
    latest = latest_happened_at()
    stop_before = latest - timedelta(minutes=overlap_minutes) if latest else None
    pages = fetched = inserted = 0
    stop_reason = "max_pages"
    for page in range(max_pages):
        rows = fetch_page(client, page * page_size, page_size)
        pages += 1
        if not rows:
            stop_reason = "empty"
            break
        fetched += len(rows)
        normalized = [r for r in (normalize_row(x, now) for x in rows) if r is not None]
        inserted += insert_rows(normalized)
        oldest = min((r["happened_at"] for r in normalized), default=None)
        if stop_before is not None and oldest is not None and oldest < stop_before:
            stop_reason = "cursor"
            break
        if len(rows) < page_size:
            stop_reason = "last_page"
            break
    summary = {"pages": pages, "fetched": fetched, "inserted": inserted, "stop": stop_reason}
    store.set_state(
        "messages", status="ok", last_run_at=now, last_success_at=now, error="",
        detail=" ".join(f"{k}={v}" for k, v in summary.items()),
    )
    return summary


def prune(now: datetime, environ: dict | None = None) -> int:
    cutoff = now - timedelta(days=retention_days(environ))
    with store.SessionLocal() as session:
        result = session.execute(delete(PrtgLogMessage).where(PrtgLogMessage.happened_at < cutoff))
        session.commit()
        return result.rowcount or 0


# ---------------- 查询与派生指标 ----------------

def has_data() -> bool:
    """日志采集是否成功跑过；没跑过时派生指标一律返回 None（不是 0）。"""
    return store.latest_success("messages") is not None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def row_to_dict(row: PrtgLogMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "sensor_id": row.sensor_objid,
        "sensor_name": row.sensor_name,
        "device_name": row.device_name,
        "sensor_type": row.sensor_type,
        "happened_at": _iso(row.happened_at),
        "status": row.status,
        "status_raw": row.status_raw,
        "message": row.message,
        "reason": row.reason,
        "value_num": row.value_num,
        "value_unit": row.value_unit,
    }


def query_logs(*, sensor_id: int | None = None, device_name: str | None = None,
               reason: str | None = None, since: datetime | None = None,
               until: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(PrtgLogMessage).order_by(PrtgLogMessage.happened_at.desc(), PrtgLogMessage.id.desc())
    if sensor_id is not None:
        stmt = stmt.where(PrtgLogMessage.sensor_objid == sensor_id)
    if device_name:
        stmt = stmt.where(PrtgLogMessage.device_name == device_name)
    if reason:
        stmt = stmt.where(PrtgLogMessage.reason == reason)
    if since is not None:
        stmt = stmt.where(PrtgLogMessage.happened_at >= since)
    if until is not None:
        stmt = stmt.where(PrtgLogMessage.happened_at < until)
    with store.SessionLocal() as session:
        rows = session.execute(stmt.limit(max(1, min(int(limit), 2000)))).scalars().all()
    return [row_to_dict(r) for r in rows]


def compute_sensor_stats(events: Iterable[tuple[int, datetime, str | None, str | None]],
                         now: datetime) -> dict[int, dict[str, Any]]:
    """纯函数：(sensor_objid, happened_at, status, reason) 时间升序 → 每传感器派生指标。

    - down_count_24h：近 24 小时 status=Down 的日志条数（PRTG 每次进入 Down 记一条）；
    - icmp_timeouts_24h：近 24 小时 reason=icmp_timeout 的条数（Ping 单通道时的丢包替代信号）；
    - mttr_minutes_7d：近 7 天每次 Down 到同传感器下一次 Up 的平均分钟数；没有完整的
      Down→Up 对时为 None（不是 0）。重复的 Down / 中间的 Warning 不打断计时。
    """
    since_24h = now - timedelta(hours=24)
    stats: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "down_count_24h": 0, "icmp_timeouts_24h": 0, "_durations": [], "_down_at": None,
    })
    for sensor_objid, happened_at, status, reason in events:
        if sensor_objid is None:
            continue
        item = stats[sensor_objid]
        st = (status or "").lower()
        if happened_at >= since_24h:
            if st == "down":
                item["down_count_24h"] += 1
            if reason == REASON_ICMP_TIMEOUT:
                item["icmp_timeouts_24h"] += 1
        if st == "down" and item["_down_at"] is None:
            item["_down_at"] = happened_at
        elif st == "up" and item["_down_at"] is not None:
            item["_durations"].append((happened_at - item["_down_at"]).total_seconds())
            item["_down_at"] = None
    result = {}
    for sensor_objid, item in stats.items():
        durations = item["_durations"]
        result[sensor_objid] = {
            "down_count_24h": item["down_count_24h"],
            "icmp_timeouts_24h": item["icmp_timeouts_24h"],
            "mttr_minutes_7d": round(sum(durations) / len(durations) / 60.0, 1) if durations else None,
            "recoveries_7d": len(durations),
        }
    return result


def sensor_stats(now: datetime) -> dict[int, dict[str, Any]] | None:
    """每传感器近 24h / 7d 派生指标；日志采集从未成功时返回 None。"""
    if not has_data():
        return None
    since = now - timedelta(days=7)
    with store.SessionLocal() as session:
        rows = session.execute(
            select(PrtgLogMessage.sensor_objid, PrtgLogMessage.happened_at,
                   PrtgLogMessage.status, PrtgLogMessage.reason)
            .where(PrtgLogMessage.happened_at >= since)
            .order_by(PrtgLogMessage.happened_at, PrtgLogMessage.id)
        ).all()
    return compute_sensor_stats(rows, now)


def top_noisy_device(now: datetime, days: int = 7) -> dict[str, Any] | None:
    """近 N 天日志最多的设备（摸底：AML TOKADEH1 一台占 87.5%）。"""
    if not has_data():
        return None
    since = now - timedelta(days=days)
    with store.SessionLocal() as session:
        total = session.execute(
            select(func.count(PrtgLogMessage.id)).where(PrtgLogMessage.happened_at >= since)
        ).scalar() or 0
        row = session.execute(
            select(PrtgLogMessage.device_name, func.count(PrtgLogMessage.id).label("n"))
            .where(PrtgLogMessage.happened_at >= since)
            .where(PrtgLogMessage.device_name.is_not(None))
            .group_by(PrtgLogMessage.device_name)
            .order_by(func.count(PrtgLogMessage.id).desc())
            .limit(1)
        ).first()
    if row is None or not total:
        return None
    return {
        "device_name": row[0],
        "messages": int(row[1]),
        "share_pct": round(int(row[1]) * 100.0 / total, 1),
        "days": days,
    }
