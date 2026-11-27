# -*- coding: utf-8 -*-
"""PRTG 历史数据导入：把 5 分钟粒度的历史灌进 `prtg_channel_samples_5m`。

用途（Issue #229）：异常规则的「7 天同期基线」需要历史 5 分钟数据，而实时
采集只能从上线时刻开始积累。PRTG 的 historicdata 支持 ``avg=300`` 一次取
多天（实测 7 天/传感器约 2000 行、1 MB），25 个传感器共 25 次请求即可补齐。

与 #227 回填的区别：
  - #227 的 60 秒回填走「传感器 × 天」窗口 + ≤5 次/分钟滑动窗口，灌的是
    原始 `prtg_historical_samples`；
  - 本导入是**一次性/按需**的 5 分钟聚合补齐，直接写 `_5m` 表，请求数少、
    不参与实时轮询，跑完即止。

用法（MSNOC1）：
    python -m backend.services.prtg.history_import --days 7
    python -m backend.services.prtg.history_import --days 14 --sensors 2721,2717
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.models.prtg_collector import PrtgChannelSample5m
from backend.services.prtg import store
from backend.services.prtg.client import PrtgClient
from backend.services.prtg.parsing import parse_historic_csv
from backend.services.prtg.settings import load_settings

CHUNK = 1000


def import_history(
    *,
    days: int = 7,
    avg: int = 300,
    sensor_ids: Iterable[int] | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """拉取最近 days 天的 avg 秒聚合，写进 5m 表（幂等 upsert）。"""
    now = now or datetime.utcnow()
    sensors = list(sensor_ids or store.sensor_objids())
    if not sensors:
        raise RuntimeError("库里没有 PRTG 传感器：先运行采集服务或目录同步")
    owns_client = client is None
    client = client or PrtgClient(load_settings().config)
    start = now - timedelta(days=days)
    total_rows = 0
    per_sensor: dict[int, int] = {}
    try:
        for sensor_id in sensors:
            text = client.historicdata_csv(
                sensor_id,
                start.strftime("%Y-%m-%d-%H-%M-%S"),
                now.strftime("%Y-%m-%d-%H-%M-%S"),
                avg=avg,
            )
            rows = parse_historic_csv(text)
            payload = []
            for row in rows:
                # Excel 序列日换算会带微秒抖动（16:44:59.999996）；
                # 先取最近分钟再落库，避免同一 5 分钟槽出现两条不同时间戳。
                bucket = (row["datetime"] + timedelta(seconds=30)).replace(second=0, microsecond=0)
                if bucket.tzinfo is not None:
                    bucket = bucket.replace(tzinfo=None)
                for channel_id, values in row["channels"].items():
                    if values.get("value_num") is None and values.get("volume_num") is None:
                        continue
                    payload.append({
                        "sensor_objid": sensor_id,
                        "channel_objid": channel_id,
                        "bucket_time": bucket,
                        "avg_value": values.get("value_num"),
                        "max_value": values.get("value_num"),
                        "avg_volume": values.get("volume_num"),
                        "max_volume": values.get("volume_num"),
                        "samples": 1,
                        "updated_at": now,
                    })
            for i in range(0, len(payload), CHUNK):
                total_rows += store.upsert_aggregates(PrtgChannelSample5m, payload[i:i + CHUNK])
            per_sensor[sensor_id] = len(rows)
    finally:
        if owns_client:
            client.close()

    from backend.services.prtg.anomaly import IMPORT_PROTECT_SECONDS

    protect_until = (now + timedelta(seconds=IMPORT_PROTECT_SECONDS)).isoformat()
    # detail：JSON（含 protect_until）+ summary 文本兼容旧「days=… avg=…」可读格式
    summary = (
        f"days={days} avg={avg}s sensors={len(sensors)} rows={total_rows} "
        f"protect_until={protect_until}"
    )
    store.set_state(
        "history_import", status="ok", last_run_at=now, last_success_at=now, error="",
        detail=json.dumps({
            "summary": summary,
            "days": days, "avg": avg, "sensors": len(sensors), "rows": total_rows,
            "protect_until": protect_until,
            "protect_seconds": IMPORT_PROTECT_SECONDS,
        }, ensure_ascii=False),
    )
    return {"sensors": len(sensors), "rows": total_rows, "per_sensor": per_sensor,
            "protect_until": protect_until}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入 PRTG 历史 5 分钟数据（#229 基线用）")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--avg", type=int, default=300, help="PRTG 聚合秒数（默认 300）")
    parser.add_argument("--sensors", default="", help="逗号分隔的传感器 objid；空=全部")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sensor_ids = [int(x) for x in args.sensors.split(",") if x.strip()] or None
    result = import_history(days=args.days, avg=args.avg, sensor_ids=sensor_ids)
    print(f"导入完成：{result['sensors']} 个传感器，共 {result['rows']} 行（5m 聚合）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
