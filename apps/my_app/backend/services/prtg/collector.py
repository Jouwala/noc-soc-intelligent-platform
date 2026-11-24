# -*- coding: utf-8 -*-
"""PRTG 采集服务（Issue #227）。

职责：状态/流量/Errors 每 60 秒轮询（与 PRTG 扫描对齐）、目录每小时同步、
historicdata 回填走**全局共享令牌桶 ≤5 次/分钟**（低峰分批、指数退避由
client 负责）、5 分钟/小时聚合并按保留策略清理。解析细节见 parsing.py，
通道语义见 channels.py，持久化见 store.py。

运行方式：生产由 main.py 的 lifespan 起一个 daemon 线程（默认关闭，需
``PRTG_COLLECTOR_ENABLED=1``）；测试直接调 poll_once()/backfill_step()，
不依赖真实 PRTG（client 可注入桩）。

失败口径：PRTG 不可达时本轮记 error 到 prtg_sync_state，线程不退出、下轮
继续；数据新鲜度由 last_success_at 暴露给接口，界面按「数据过期」提示，
绝不把旧值当实时值。
"""
import json
import logging
import time
from datetime import datetime, timedelta
from collections import deque
from typing import Any, Callable, Iterable

from backend.services.prtg import store
from backend.services.prtg.channels import HISTORIC_CHANNEL_ORDER, get_channel
from backend.services.prtg.client import PrtgClient
from backend.services.prtg.parsing import (
    excel_serial_to_datetime,
    parse_channels_xml,
    parse_historic_csv,
)
from backend.services.prtg.settings import PrtgSettings

logger = logging.getLogger(__name__)

# 实时轮询保留的通道：9 个有值的通道（-4 Downtime 无值，不采）。
LIVE_CHANNEL_IDS = (-1, 0, 1, 2, 3, 10, 11, 12, 13)

SENSOR_COLUMNS = "objid,group,device,sensor,status,status_raw,type,priority,lastvalue,lastvalue_raw"
DEVICE_COLUMNS = "objid,group,device,type"

CATALOG_INTERVAL_SECONDS = 3600
DETAIL_INTERVAL_SECONDS = 300
AGGREGATE_INTERVAL_SECONDS = 300
PRUNE_INTERVAL_SECONDS = 86400
LOOP_TICK_SECONDS = 5


class TokenBucket:
    """回填限流：**60 秒滑动窗口**，任意窗口内最多 capacity 次（默认 5/分钟）。

    2026-11-24 审核实测：固定令牌桶在空闲后会补满到 capacity，恢复后的
    60 秒内可放行 9 次（突发 5 + 补 5），不满足 #227「不超 5 次/分钟」。
    改用滑动窗口：只记最近 window 秒内的放行时间戳，窗口内满额就拒绝，
    空闲再久也不会突破窗口上限。
    """

    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = max(1, int(capacity))
        # 由「每秒补几次」推窗口长度：5 次/分钟 → 60 秒窗口
        self.window_seconds = self.capacity / max(1e-9, float(refill_per_second))
        self._clock = clock
        self._grants: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._grants and now - self._grants[0] >= self.window_seconds:
            self._grants.popleft()

    def try_acquire(self, amount: float = 1.0) -> bool:
        now = self._clock()
        self._prune(now)
        if len(self._grants) + amount <= self.capacity:
            self._grants.append(now)
            return True
        return False

    def available(self) -> float:
        self._prune(self._clock())
        return float(self.capacity - len(self._grants))


class PrtgCollector:
    """单个实例即一个采集循环；所有公开方法都可单独调用（便于测试/手动补采）。"""

    def __init__(
        self,
        settings: PrtgSettings,
        client: Any | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._now = now or (lambda: datetime.utcnow())
        self.token_bucket = TokenBucket(
            capacity=settings.backfill_per_minute,
            refill_per_second=settings.backfill_per_minute / 60.0,
        )
        self._sensor_ids: list[int] | None = None

    # ---------------- client ----------------

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = PrtgClient(self.settings.config)
        return self._client

    def now(self) -> datetime:
        """当前时间（测试可注入固定时钟）。"""
        return self._now()

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()
        self._client = None

    # ---------------- 目录 ----------------

    def sync_catalog(self) -> dict[str, Any]:
        """设备 + 传感器 + 通道目录（每小时）。"""
        client = self._get_client()
        now = self._now()
        devices = _rows(client.table_json("devices", columns=list(DEVICE_COLUMNS.split(",")), count=500), "devices")
        sensors = _rows(
            client.table_json("sensors", columns=list(SENSOR_COLUMNS.split(",")), count=500), "sensors"
        )
        store.upsert_devices(devices, now)
        store.upsert_sensors(sensors, now)
        channel_count = 0
        for sensor in sensors:
            channel_count += self._sync_sensor_channels(int(sensor["objid"]))
        self._sensor_ids = [int(s["objid"]) for s in sensors]
        store.set_state(
            "catalog", status="ok", last_run_at=now, last_success_at=now,
            error="", detail=f"devices={len(devices)} sensors={len(sensors)} channels={channel_count}",
        )
        return {"devices": len(devices), "sensors": len(sensors), "channels": channel_count}

    def _sync_sensor_channels(self, sensor_objid: int) -> int:
        client = self._get_client()
        now = self._now()
        rows = parse_channels_xml(client.channels_xml(sensor_objid))
        catalog = []
        for row in rows:
            channel_id = row["channel_objid"]
            info = _channel_meta(channel_id, row["name"])
            catalog.append({
                "objid": channel_id,
                "name": row["name"],
                "unit": info["unit"],
                "value_kind": info["value_kind"],
            })
        return store.upsert_channels(sensor_objid, catalog, now)

    # ---------------- 实时轮询 ----------------

    def poll_once(self) -> dict[str, Any]:
        """一分钟一轮：传感器状态 + 全部通道最新值。

        先读旧状态再 upsert，保证能识别 Up→Unusual→Down 的变化并落事件。
        """
        client = self._get_client()
        collected_at = self._now()
        sample_time = collected_at.replace(second=0, microsecond=0)
        sensors = _rows(
            client.table_json("sensors", columns=list(SENSOR_COLUMNS.split(",")), count=500), "sensors"
        )
        previous = store.current_sensor_statuses()
        store.upsert_sensors(sensors, collected_at)

        statuses: list[dict[str, Any]] = []
        events = 0
        samples = 0
        for sensor in sensors:
            sensor_id = int(sensor["objid"])
            status = (sensor.get("status") or "Unknown").strip()
            status_raw = _to_int(sensor.get("status_raw"))
            changed = previous.get(sensor_id) != status
            heartbeat_due = collected_at.minute == 0
            if changed or heartbeat_due or sensor_id not in previous:
                statuses.append({
                    "sensor_objid": sensor_id,
                    "status": status,
                    "status_raw": status_raw,
                    "sampled_at": sample_time,
                })
            if changed and sensor_id in previous:
                store.insert_event(
                    sensor_id,
                    _event_type(status),
                    previous.get(sensor_id),
                    status,
                    sample_time,
                    message=f"PRTG 状态 {previous.get(sensor_id)} → {status}",
                )
                events += 1

            channel_rows = _select_live_channels(parse_channels_xml(client.channels_xml(sensor_id)))
            samples += store.insert_samples(sensor_id, sample_time, channel_rows, collected_at)

        if statuses:
            store.insert_statuses(statuses, collected_at)
        summary = {
            "sensors": len(sensors),
            "samples": samples,
            "status_changes": events,
            "sample_time": sample_time.isoformat(),
        }
        store.set_state(
            "live", status="ok", last_run_at=collected_at, last_success_at=collected_at,
            error="", detail=f"sensors={len(sensors)} samples={samples} changes={events}",
        )
        return summary

    def refresh_details(self) -> int:
        """每 5 分钟补 Uptime/Downtime/Last Down/扫描间隔（getsensordetails.xml）。"""
        client = self._get_client()
        now = self._now()
        updated = 0
        for sensor_id in self._sensor_ids or store.sensor_objids():
            text = client.sensor_details_xml(sensor_id)
            fields = _parse_sensor_details(text)
            if not fields:
                continue
            fields["updated_at"] = now
            store.update_sensor_details(sensor_id, fields)
            updated += 1
        store.set_state("catalog", status="ok", last_run_at=now, detail=f"details={updated}")
        return updated

    # ---------------- 历史回填 ----------------

    def backfill_step(self) -> dict[str, Any] | None:
        """回填一步（一个「传感器 × 一天」窗口），受全局令牌桶 ≤N 次/分钟约束。"""
        if not self.token_bucket.try_acquire():
            return None
        client = self._get_client()
        now = self._now()
        cursor = _load_cursor(store.get_state("backfill"))
        sensors = self._sensor_ids or sorted(store.sensor_objids())
        if not sensors:
            return None
        index = int(cursor["sensor_index"]) % len(sensors)
        end_day = cursor["end_day"] or now.date().isoformat()
        day_end = datetime.fromisoformat(end_day)
        day_start = day_end - timedelta(days=1)

        sensor_id = sensors[index % len(sensors)]
        text = client.historicdata_csv(
            sensor_id, day_start.strftime("%Y-%m-%d-%H-%M-%S"),
            day_end.strftime("%Y-%m-%d-%H-%M-%S"),
        )
        parsed = parse_historic_csv(text)
        rows = []
        for row in parsed:
            for channel_id, values in row["channels"].items():
                rows.append({
                    "sensor_objid": sensor_id,
                    "channel_objid": channel_id,
                    "sample_time": row["datetime"].replace(second=0, microsecond=0),
                    "value_num": values.get("value_num"),
                    "value_display": values.get("value_display"),
                    "volume_num": values.get("volume_num"),
                    "volume_display": values.get("volume_display"),
                    "source": "historic",
                })
        inserted = store.insert_historical(rows, now)
        # 回填的历史行要能进 7d/30d 视图：把这一天按小时聚一次（幂等 upsert），
        # 否则 prtg_channel_samples_1h 只覆盖实时那 3 小时，回填数据无人读取。
        self._aggregate_range(day_start, day_end, minutes=60, model_name="1h")

        # 推进游标（2026-11-24 审核修复）：同一传感器**逐日**往回走，
        # 走满 raw_retention_days 天才换下一个传感器；旧实现同时推进
        # sensor_index 与 end_day，1000 步只覆盖 450 个「传感器×天」。
        next_index = index
        next_end = day_start
        oldest_date = (now - timedelta(days=self.settings.raw_retention_days)).date()
        if next_end.date() <= oldest_date:
            next_index = (index + 1) % len(sensors)
            next_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor_out = json.dumps({
            "sensor_index": next_index,
            "end_day": next_end.date().isoformat(),
        })
        store.set_state(
            "backfill", status="ok", last_run_at=now, last_success_at=now,
            error="", cursor=cursor_out,
            detail=f"sensor={sensor_id} day={day_start.date()} rows={inserted}",
        )
        return {"sensor_objid": sensor_id, "day": day_start.date().isoformat(), "rows": inserted}

    # ---------------- 聚合与清理 ----------------

    def aggregate(self) -> dict[str, int]:
        now = self._now()
        five_min = self._aggregate_window(minutes=5, model_name="5m", since_minutes=20)
        hourly = self._aggregate_window(minutes=60, model_name="1h", since_minutes=180)
        store.set_state(
            "aggregate", status="ok", last_run_at=now, last_success_at=now,
            error="", detail=f"5m={five_min} 1h={hourly}",
        )
        return {"5m": five_min, "1h": hourly}

    def _aggregate_window(self, *, minutes: int, model_name: str, since_minutes: int) -> int:
        now = self._now()
        return self._aggregate_range(now - timedelta(minutes=since_minutes), now,
                                     minutes=minutes, model_name=model_name)

    def _aggregate_range(self, since: datetime, until: datetime, *,
                         minutes: int, model_name: str) -> int:
        now = self._now()
        model = _aggregate_model(model_name)
        raw = store.fetch_raw_samples(since, until)
        buckets: dict[tuple[int, int, datetime], dict[str, Any]] = {}
        for sensor_objid, channel_objid, sample_time, value_num, volume_num in raw:
            bucket = _floor_time(sample_time, minutes)
            key = (sensor_objid, channel_objid, bucket)
            slot = buckets.get(key)
            if slot is None:
                slot = {"value": [], "volume": [], "max_value": None, "max_volume": None}
                buckets[key] = slot
            if value_num is not None:
                slot["value"].append(value_num)
                slot["max_value"] = value_num if slot["max_value"] is None else max(slot["max_value"], value_num)
            if volume_num is not None:
                slot["volume"].append(volume_num)
                slot["max_volume"] = volume_num if slot["max_volume"] is None else max(slot["max_volume"], volume_num)
        rows = []
        for (sensor_objid, channel_objid, bucket), slot in buckets.items():
            rows.append({
                "sensor_objid": sensor_objid,
                "channel_objid": channel_objid,
                "bucket_time": bucket,
                "avg_value": _avg(slot["value"]),
                "max_value": slot["max_value"],
                "avg_volume": _avg(slot["volume"]),
                "max_volume": slot["max_volume"],
                "samples": max(len(slot["value"]), len(slot["volume"])),
                "updated_at": now,
            })
        return store.upsert_aggregates(model, rows)

    def prune(self) -> dict[str, int]:
        now = self._now()
        raw_cutoff = now - timedelta(days=self.settings.raw_retention_days)
        result = {
            "raw": store.delete_samples_before("raw", raw_cutoff),
            "historic": store.delete_samples_before("historic", raw_cutoff),
            "5m": store.delete_bucket_before("5m", now - timedelta(days=365)),
            "1h": store.delete_bucket_before("1h", now - timedelta(days=1000)),
        }
        store.set_state(
            "prune", status="ok", last_run_at=now, last_success_at=now,
            error="", detail=json.dumps(result, ensure_ascii=False),
        )
        return result

# ---------------- 纯函数辅助 ----------------

def _rows(text: str, key: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"PRTG table 响应缺少 {key} 数组")
    return rows


def _select_live_channels(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        channel_id = row["channel_objid"]
        if channel_id == -4:
            continue
        if channel_id in LIVE_CHANNEL_IDS or row.get("name"):
            selected.append(row)
    return selected


def _channel_meta(channel_id: int, name: str) -> dict[str, Any]:
    try:
        info = get_channel(channel_id)
        return {"unit": info.unit_raw, "value_kind": info.value_kind}
    except ValueError:
        return {"unit": None, "value_kind": "raw"}  # Ping 等未入字典的通道


def _parse_sensor_details(text: str) -> dict[str, Any]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    fields: dict[str, Any] = {}

    def text_of(tag: str) -> str | None:
        el = root.find(tag)
        return (el.text or "").strip() if el is not None and el.text else None

    def datetime_of(tag: str) -> datetime | None:
        raw = text_of(tag)
        if not raw:
            return None
        serial = raw.split()[0]
        try:
            return excel_serial_to_datetime(float(serial))
        except ValueError:
            return None

    interval = text_of("interval")
    if interval:
        fields["interval_seconds"] = _to_int(interval)
    for tag, column in (("uptime", "uptime_pct"), ("downtime", "downtime_pct")):
        raw = text_of(tag)
        if raw:
            fields[column] = _to_float(raw.replace("%", "").strip())
    for tag, column in (("lastup", "last_up"), ("lastdown", "last_down"), ("lastcheck", "last_check")):
        value = datetime_of(tag)
        if value is not None:
            fields[column] = value
    statustext = text_of("statustext")
    if statustext:
        fields["status"] = statustext
    return fields


def _event_type(status: str) -> str:
    lowered = status.lower()
    if lowered == "down":
        return "down"
    if lowered == "unusual":
        return "unusual"
    if lowered == "up":
        return "up"
    return "status_change"


def _load_cursor(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state or not state.get("cursor"):
        return {"sensor_index": 0, "end_day": None}
    try:
        cursor = json.loads(state["cursor"])
    except ValueError:
        return {"sensor_index": 0, "end_day": None}
    return {
        "sensor_index": int(cursor.get("sensor_index") or 0),
        "end_day": cursor.get("end_day"),
    }


def _floor_time(value: datetime, minutes: int) -> datetime:
    if minutes >= 60:
        return value.replace(minute=0, second=0, microsecond=0)
    floored = (value.minute // minutes) * minutes
    return value.replace(minute=floored, second=0, microsecond=0)


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate_model(name: str):
    from backend.models.prtg_collector import PrtgChannelSample1h, PrtgChannelSample5m

    return PrtgChannelSample5m if name == "5m" else PrtgChannelSample1h


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
