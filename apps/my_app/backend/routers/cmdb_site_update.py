# -*- coding: utf-8 -*-
"""cmdb_site_update.py - CMDB 站点同步比对（Email Reports →「站点同步」页签）

配套任务：`eflow_site_update`（backend/services/eflow/eflow_site_update_task.py）。

- GET  /api/cmdb-site-update/summary   上次运行摘要 + modify 目录产出文件清单
- GET  /api/cmdb-site-update/download  下载产出文件（只允许 modify 目录内）
- GET  /api/cmdb-site-update/sources   可选 CMDB 主数据 xlsx 清单
- POST /api/cmdb-site-update/generate  按选定源文件生成差异文件（操作台专用，同步返回）

老的「生成差异文件」入口仍走统一任务接口 `POST /api/tasks/eflow_site_update/run`
——复用队列、禁用态校验、执行历史与失败记录，永远取最新源表、不接受参数。
操作台需要指定源文件并同步拿到 A/M/D，才另开 `/generate`（同一任务函数，传 src_file）。

站点同步操作页的**写库**走本模块 /writeback/*：预览令牌 + confirm 双开关，
一次性人工动作，刻意不注册成定时任务、不进队列（避免被调度器误跑）；
写库目标仅 app.master_site_db，脚本先自动备份，且默认不删站。
除 /writeback/apply 外本模块全部只读。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.security import require_protected
from backend.services.eflow import eflow_site_update_task as task
from backend.services.eflow import master_site_writeback_service as writeback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cmdb-site-update", tags=["cmdb-site-update"])


class SourceChoice(BaseModel):
    source: str | None = None


class WritebackApply(BaseModel):
    token: str
    confirm: bool = False
    source: str | None = None


class GenerateBody(BaseModel):
    source: str | None = None


@router.get("/sources")
def cmdb_site_update_sources(_=Depends(require_protected)):
    """可选 CMDB 主数据 xlsx 清单（最新在前）。写死文件名不可取，页面据此下拉选择。"""
    return {"sources": task.list_sources()}


@router.post("/generate")
def cmdb_site_update_generate(body: GenerateBody, _=Depends(require_protected)):
    """按选定源文件生成差异文件（只产文件不写库）。

    与统一任务接口 `POST /api/tasks/eflow_site_update/run` 的区别：那个不接受
    参数、只能取最新源表；本接口是操作台专用同步调用，可指定 --src-file，
    前端要拿本次结果（A/M/D）而不是排队异步，故不复用队列。
    """
    try:
        return task.run_eflow_site_update(src_file=body.source)
    except Exception as exc:  # noqa: BLE001 - 转成前端可读错误
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/writeback/preview")
def writeback_preview(source: str | None = None, _=Depends(require_protected)):
    """写库预览（不写库）：预期 update/insert + 令牌。令牌绑定选中源文件指纹。"""
    return writeback.preview(source)


@router.post("/writeback/apply")
def writeback_apply(body: WritebackApply, _=Depends(require_protected)):
    """确认写库：必须带预览令牌 + confirm=true。默认不删站，脚本先自动备份 PG 表。"""
    return writeback.apply(body.token, body.confirm, body.source)


@router.get("/writeback/last")
def writeback_last():
    """上次写库结果摘要（只读）。"""
    return {"last_run": writeback.last_writeback()}


@router.get("/summary")
def cmdb_site_update_summary():
    """上次运行摘要（A/M/D 计数、源文件、耗时）+ 当前产出文件清单。

    摘要不存在时 `last_run` 为 None —— 页面显示「尚未运行过」而不是报错：
    文件可能是历史遗留（脚本还没从这个入口跑过），清单照样要给。
    """
    return {
        "work_dir": str(task.WORK_DIR),
        "out_dir": str(task.OUT_DIR),
        "script_exists": task.SCRIPT_PATH.is_file(),
        "last_run": task.read_last_summary(),
        "files": task._list_outputs(),
    }


@router.get("/download")
def cmdb_site_update_download(
    path: str = Query(..., description="相对 modify 目录的路径，如 eflow_site_update.xlsx 或 import/eflow_site_import.xlsx"),
):
    """下载产出文件。只允许 modify 目录内的文件（防目录穿越）。"""
    root = task.OUT_DIR.resolve()
    target = (root / path).resolve()
    # 目录穿越防护：resolve 之后必须仍在 modify 下，且必须是文件
    if root not in target.parents and target != root:
        raise HTTPException(status_code=400, detail="非法路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在：{path}")
    if target.suffix.lower() not in {".xlsx", ".xlsm", ".csv", ".json", ".md", ".txt", ".log"}:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    return FileResponse(
        str(target),
        filename=target.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if target.suffix.lower() in {".xlsx", ".xlsm"} else "application/octet-stream",
    )
