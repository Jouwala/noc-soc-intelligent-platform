# -*- coding: utf-8 -*-
"""NGI 工参 + 覆盖数据同步任务入口（每周日 02:00）。

## 一轮做两件事

| 阶段 | 动作 | 落地表 |
| --- | --- | --- |
| `param` | `projectParamFiles?exportType=1,0` → `ProjectData.zip` → 解析 LTE/NR 两个工作簿（第 3 行做表头）、给空的 `SectorID` 按「站点 + 下行中心频点」的方位角顺序补号 | `app.ngi_lte_cell_param` / `app.ngi_nr_cell_param` |
| `coverage` | 取该评估任务**最新一次**运行 → 图层与 legend → `exportMap(CSV)` 异步作业 → 轮询 → 下载 `CSV.zip` → 解析 50 m 栅格 | `app.ngi_coverage_grid` |

两阶段各自一个事务、各写一条 `app.ngi_sync_log`：覆盖阶段失败**不回滚**
已成功的工参，但任务整体判失败（错误信息写明哪一阶段失败、哪一阶段已完成）。

## 行数下限（拒绝用残片覆盖好数据）

* `NGI_MIN_LTE_ROWS`（默认 5000，实测 8 227）
* `NGI_MIN_NR_ROWS`（默认 50，实测 101）
* `NGI_MIN_GRID_ROWS`（默认 50000，实测 178 370）

低于下限直接判失败、**不写库** —— 上游返回空表/半页时，全量替换会把上一周的
好数据整表抹掉，这是本项目已经踩过的坑（见 `RANElementManagement` 同款守卫）。

## 可调环境变量

`NGI_HOST` / `NGI_PORT` / `NGI_TASK_ID` / `NGI_PARAM_EXPORT_TYPE` /
`NGI_MIN_LTE_ROWS` / `NGI_MIN_NR_ROWS` / `NGI_MIN_GRID_ROWS` /
`NGI_EXPORT_TIMEOUT` / `NGI_DB_CHUNK` / `NGI_USER` / `NGI_PASSWORD`。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone

logger = logging.getLogger("ngi_sync_service")

MIN_LTE_ROWS = int(os.environ.get("NGI_MIN_LTE_ROWS", "5000"))
MIN_NR_ROWS = int(os.environ.get("NGI_MIN_NR_ROWS", "50"))
MIN_GRID_ROWS = int(os.environ.get("NGI_MIN_GRID_ROWS", "50000"))


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ---------------------------------------------------------------- 工参
def _sync_param(fetched_at: datetime) -> dict:
    """拉工参 ZIP → 校验行数 → 补 SectorID → 两张表全量刷新。"""
    from backend.services.ngi_data import ngi_client, ngi_param, ngi_store

    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    log_id = ngi_store.open_log(kind="param", started_at=started_at)
    try:
        zip_bytes, auth_source = ngi_client.with_auth(ngi_client.fetch_param_zip)

        parsed: dict[str, list[dict]] = {}
        sector_stats: dict[str, dict] = {}
        for rat in ("LTE", "NR"):
            rows = ngi_param.read_sheet(zip_bytes, rat)
            parsed[rat] = rows
            # LTE 只补空值；NR 没有 SectorID 列，整列生成（见 ngi_param.SECTOR_KEYS）
            sector_stats[rat] = ngi_param.fill_missing_sector_ids(rows, rat)

        # 先校验再落库：残片不该覆盖上一周的好数据
        if len(parsed["LTE"]) < MIN_LTE_ROWS:
            raise RuntimeError(
                f"LTE 工参只有 {len(parsed['LTE'])} 行，低于下限 {MIN_LTE_ROWS}"
                "（NGI_MIN_LTE_ROWS）；拒绝落库"
            )
        if len(parsed["NR"]) < MIN_NR_ROWS:
            raise RuntimeError(
                f"NR 工参只有 {len(parsed['NR'])} 行，低于下限 {MIN_NR_ROWS}"
                "（NGI_MIN_NR_ROWS）；拒绝落库"
            )

        counts = {}
        for rat in ("LTE", "NR"):
            records = ngi_store.normalize_param_records(
                rat, parsed[rat], fetched_at.date(), fetched_at)
            counts[rat] = ngi_store.persist_param(rat, records, fetched_at)

        duration = _ms(started)
        message = (f"LTE={counts['LTE']['rows']} NR={counts['NR']['rows']} "
                   f"sector_lte={sector_stats.get('LTE', {}).get('filled', 0)} "
                   f"sector_nr={sector_stats.get('NR', {}).get('filled', 0)}")
        ngi_store.finish_log(
            log_id, status="success",
            row_count=counts["LTE"]["rows"] + counts["NR"]["rows"],
            duration_ms=duration, message=message, zip_bytes=len(zip_bytes),
            detail={"lte": counts["LTE"], "nr": counts["NR"], "sector": sector_stats},
        )
        logger.info("[NGI] 工参入库完成：%s（%.1fs）", message, duration / 1000)
        return {"lte": counts["LTE"]["rows"], "nr": counts["NR"]["rows"],
                "sector_filled": {rat: s.get("filled", 0) for rat, s in sector_stats.items()},
                "zip_bytes": len(zip_bytes), "auth_source": auth_source,
                "duration_ms": duration}
    except Exception as e:  # noqa: BLE001
        ngi_store.finish_log(log_id, status="failed", row_count=0,
                             duration_ms=_ms(started),
                             message=f"{type(e).__name__}: {e}")
        raise


# ---------------------------------------------------------------- 覆盖
def _sync_coverage() -> dict:
    """最新覆盖评估任务 → 网格 CSV 导出 → `app.ngi_coverage_grid` 全量替换。"""
    from backend.services.ngi_data import ngi_client, ngi_coverage, ngi_store

    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    log_id = ngi_store.open_log(kind="coverage", started_at=started_at)
    task: dict = {}
    grid_size = None
    try:
        task, auth_source = ngi_client.with_auth(ngi_client.latest_task)
        logger.info("[NGI] 最新覆盖评估任务：taskLogId=%s name=%s start=%s",
                    task.get("taskLogId"), task.get("taskLogName"), task.get("starttime"))
        templates, _ = ngi_client.with_auth(
            lambda c, t: ngi_client.kpi_templates(c, t, task))

        results = []
        total_rows = 0
        zip_total = 0
        for tpl in templates:
            rule = tpl["rule"]
            task_id, layer_name, grid_size, legend = ngi_client.with_auth(
                lambda c, t, r=rule: ngi_client.layer_and_legend(
                    c, t, str(task["taskLogId"]), r))[0]
            zip_bytes, _src = ngi_client.with_auth(
                lambda c, t, tid=task_id, ln=layer_name, gs=grid_size, lg=legend, r=rule:
                ngi_client.export_grid_zip(c, t, tid, ln, gs, lg, r))
            zip_total += len(zip_bytes)

            csv_name, rows = ngi_coverage.parse_zip(zip_bytes)
            if len(rows) < MIN_GRID_ROWS:
                raise RuntimeError(
                    f"覆盖网格 {rule} 只有 {len(rows)} 行，低于下限 {MIN_GRID_ROWS}"
                    "（NGI_MIN_GRID_ROWS）；拒绝覆盖上一周的数据"
                )
            records = ngi_store.normalize_grid_records(rows, rule, log_id)
            counts = ngi_store.persist_grid(rule, records)
            total_rows += counts["rows"]
            # legend 一并留痕到 sync_log.detail：位置页覆盖图层的 meta 接口会带出它
            # （档位 1~7 的原始区间定义），前端配色以库内实测 RSRP 统计为准、legend 作参考。
            results.append({"kpi": rule, "csv": csv_name, "rows": counts["rows"],
                            "grid_size_m": grid_size, "zip_bytes": len(zip_bytes),
                            "legend": legend})

        duration = _ms(started)
        message = (f"taskLogId={task.get('taskLogId')} "
                   f"kpis={','.join(r['kpi'] for r in results)} rows={total_rows}")
        ngi_store.finish_log(
            log_id, status="success", row_count=total_rows, duration_ms=duration,
            message=message, auth_source=auth_source,
            task_log_id=str(task.get("taskLogId")),
            task_log_name=task.get("taskLogName"),
            task_start_time=task.get("starttime"),
            grid_size_m=grid_size, zip_bytes=zip_total, detail={"kpis": results},
        )
        logger.info("[NGI] 覆盖入库完成：%s（%.1fs）", message, duration / 1000)
        return {"task_log_id": str(task.get("taskLogId")),
                "task_log_name": task.get("taskLogName"),
                "kpis": results, "rows": total_rows,
                "auth_source": auth_source, "duration_ms": duration}
    except Exception as e:  # noqa: BLE001
        ngi_store.finish_log(
            log_id, status="failed", row_count=0, duration_ms=_ms(started),
            message=f"{type(e).__name__}: {e}",
            task_log_id=str(task.get("taskLogId") or ""),
            task_log_name=task.get("taskLogName"),
            task_start_time=task.get("starttime"), grid_size_m=grid_size,
        )
        raise


# ---------------------------------------------------------------- 任务入口
def run_ngi_param_coverage_sync() -> dict:
    """任务入口：工参 + 覆盖两阶段同步。由 `TASK_SPECS` 懒加载调用。

    返回紧凑摘要（不要把 178 370 行明细塞进 task_execution_log）。
    """
    from backend.services.ngi_data import ngi_store

    ngi_store.ensure_schema()  # 非 pg 模式在这里就明确报错
    fetched_at = datetime.now(timezone.utc)
    logger.info("[NGI] 开始同步工参与覆盖数据 → PG")

    result: dict = {"success": False, "param": None, "coverage": None, "errors": []}
    for stage, fn in (("param", lambda: _sync_param(fetched_at)),
                      ("coverage", _sync_coverage)):
        try:
            result[stage] = fn()
        except Exception as e:  # noqa: BLE001
            logger.exception("[NGI] %s 阶段失败: %s", stage, e)
            result["errors"].append(f"{stage}: {type(e).__name__}: {e}")

    if result["errors"]:
        done = [s for s in ("param", "coverage") if result.get(s)]
        raise RuntimeError(
            "NGI 同步失败（已完成阶段：%s）：%s"
            % (", ".join(done) or "无", "；".join(result["errors"]))
        )

    result["success"] = True
    result["snapshot_date"] = date.today().isoformat()
    result["tables"] = [ngi_store.LTE_TABLE, ngi_store.NR_TABLE,
                        ngi_store.GRID_TABLE, ngi_store.LOG_TABLE]
    return result


# 便于本地单跑：python -m backend.services.ngi_data.ngi_sync_service
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run_ngi_param_coverage_sync(), ensure_ascii=False, indent=2))
