# -*- coding: utf-8 -*-
"""International MOU Daily Traffic 图表邮件
==========================================

每天 07:00 取 `?page=cn_kpi` 上「International MOU」图表的数据，分别画
Outgoing MOU / Incoming MOU 两张折线图，经项目统一的 EWS 邮件脚本
（email_reports/owa_mail_report.py）内嵌图片发送。

数据来源与「同一个图表」的保证
------------------------------
本服务**直接复用** backend/services/cn/cn_kpi_service.py 里 c16 组件的 SQL
（INTERNATIONAL_WIDGET_ID），不做第二份手写 SQL：
  - 表：远程 db_ps_cs_cn.sqlite3 的 International_MOU_OSS_EMS
        （PS_CS_CORE 的 CSV_EXTRA 以 daily_sum 入库，每天一行）；
  - 指标：Outgoing MOU / Incoming MOU（分钟），按天 SUM；
  - 窗口：与看板默认一致，近 31 天。
这样看板改口径时邮件自动跟着变，不会出现「图上和邮件里两个数」。

日期标签为什么减一天
--------------------
c16 的日期键是 substr("End Time",1,10)。该表的 End Time 是区间右端点
（次日 00:00），所以图上 2026-11-22 这个点对应的其实是 11-21 的流量。
日报是给人转发、按日期对事故用的，标签必须写成真实统计日，故这里统一
把 End Time 减一天还原成 Start Time 口径 —— 与 MTN MOU 日报邮件的写法
一致，**数值与看板逐点相同**。
若上游把 c16 改成别的日期键，本服务会在取数时显式报错（不静默错标）。

收件人：Settings → 邮件收件人 →「International MOU 日报图表邮件」分组
（key=intl_mou_daily_email），未保存时回退默认名单 dong.xinmin@zte.com.cn。

调度：task_id = intl_mou_daily_email，default_cron = 0 7 * * *
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from backend.services.mou_chart_common import (
    COLOR_IN,
    COLOR_OUT,
    INLINE_IMAGES_PLACEHOLDER,
    cleanup_old_run_dirs,
    plot_series,
    summary_table,
)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

logger = logging.getLogger("intl_mou_chart_service")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception as exc:  # noqa: BLE001 — 控制台编码问题不该影响任务
    logger.debug("设置 stdout 编码失败: %s", exc)

# ===== 路径 / 数据源配置 =====
MY_APP_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = MY_APP_DIR / "TMP" / "intl_mou_charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_REPORTS_DIR = MY_APP_DIR / "email_reports"
_MANAGED_VENV_PY = os.environ.get("MY_APP_REPORT_PY") or sys.executable

SUBJECT = "International MOU Daily Traffic"
EMAIL_GROUP_KEY = "intl_mou_daily_email"

# cn_kpi 页面上「International MOU」那张图的组件 id（见 cn_kpi_service.WIDGETS）。
# 复用它的 SQL 是「与图表同数」的唯一保证；改了 id 这里会显式报错，不会静默换表。
INTERNATIONAL_WIDGET_ID = "c16"

# 展示窗口（完整自然日），与看板默认区间一致
DISPLAY_DAYS = 31


def _widget_sql() -> str:
    """取 cn_kpi 页面上 International MOU 组件的 SQL（已替换默认时间区间）。"""
    from backend.services.cn import cn_kpi_service

    for w in cn_kpi_service.WIDGETS:
        if w["id"] == INTERNATIONAL_WIDGET_ID:
            break
    else:
        raise RuntimeError(
            f"cn_kpi 组件 {INTERNATIONAL_WIDGET_ID} 不存在，"
            "International MOU 图表可能已改名/下线"
        )
    # 不传区间 = 看板默认的「近 31 天」；start/end 必须显式给 None（签名没有默认值）。
    sql = cn_kpi_service._apply_range(w["sql"], None, None)
    if 'substr("End Time"' not in sql:
        # 下面的「减一天」依赖 End Time = 次日 00:00 这个事实（见表内 End Time 列）。
        # 上游若改成 Start Time 或别的键，继续减一天就会把整条曲线错标一天。
        raise RuntimeError(
            f"cn_kpi 组件 {INTERNATIONAL_WIDGET_ID} 的日期键已不是 End Time，"
            "请复核日期换算后再启用本邮件"
        )
    return sql


def _fetch_mou_data() -> Optional[pd.DataFrame]:
    """读取与 ?page=cn_kpi 的 International MOU 图表相同的数据。"""
    from backend.services.cn.cn_kpi_service import REMOTE_DB
    from backend.services.db.pg_query import query_data

    try:
        sql = _widget_sql()
        # 本机 PG 优先、远端回退（2026-11-22）
        res = query_data(sql, db=REMOTE_DB)
        if not res.get("success", True):
            raise RuntimeError(res.get("error") or "远端返回 success=false")
        rows = res.get("rows") or []
        if not rows:
            logger.warning("International MOU 图表在近 %d 天窗口内没有数据", DISPLAY_DAYS)
            return None
        cols = list(res.get("columns") or ["t", "Outgoing MOU", "Incoming MOU"])
    except Exception as e:  # noqa: BLE001 — 取数失败由调用方转成任务错误
        logger.error("读取 International MOU 数据失败: %s", e, exc_info=True)
        return None

    df = pd.DataFrame(rows, columns=cols)

    # End Time（区间右端点 = 次日 00:00）→ 统计日；与 MTN MOU 邮件同口径。
    df["Start Time"] = pd.to_datetime(df["t"], errors="coerce") - pd.Timedelta(days=1)
    df = df.dropna(subset=["Start Time"])
    for col in ("Outgoing MOU", "Incoming MOU"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")

    # 今天还没过完，不展示也不发送（正常情况下上游本就不会有今天这一行，
    # 这里兜住「导出区间临时扩到今天」的情况）
    today = pd.Timestamp(datetime.now().date())
    before = len(df)
    df = df[df["Start Time"] < today]
    if before != len(df):
        logger.info("剔除运行当天未完整数据 %d 行", before - len(df))

    if df.empty:
        return None

    df = df.sort_values("Start Time").tail(DISPLAY_DAYS).reset_index(drop=True)
    logger.info("读取 %d 天数据：%s ~ %s",
                len(df), df["Start Time"].iloc[0].date(), df["Start Time"].iloc[-1].date())
    return df


def _send_email(to: List[str], cc: List[str], body: str,
                inline_images: List[Path], data_dir: Path) -> dict:
    """经统一 EWS 邮件脚本发送（内嵌图片，不带 Excel）。

    ⚠️ 刻意留在本模块、没有抽到 mou_chart_common：email_task_coverage 用 AST
    在任务入口模块里找 `owa_mail_report` 标记，据此把任务自动纳入 EMAIL_TASK_IDS
    （EWS 降级后的补发名单）。挪走会让本任务从补发名单里静默消失。
    """
    script = EMAIL_REPORTS_DIR / "owa_mail_report.py"
    if not script.is_file():
        return {"success": False, "error": f"邮件脚本不存在: {script}"}

    cmd = [
        _MANAGED_VENV_PY, str(script),
        "--data-dir", str(data_dir),
        "--email-dir", str(EMAIL_REPORTS_DIR),
        "--no-excel",
        "--subject", SUBJECT,
        "--body", body,
        "--html",
        "--to", ";".join(to),
        "--cc", ";".join(cc) if cc else "",
    ]
    for img in inline_images:
        cmd += ["--images", str(img)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, cwd=str(MY_APP_DIR), creationflags=_SUBPROCESS_FLAGS,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "发送超时"}
    except Exception as e:  # noqa: BLE001 — 发信失败要回传给任务执行日志
        return {"success": False, "error": str(e)}

    if proc.returncode == 0:
        return {"success": True, "message": (proc.stdout or "")[-500:]}
    return {"success": False, "error": (proc.stderr or proc.stdout or "")[-1000:]}


def run_intl_mou_daily_email(to_override: Optional[str] = None,
                             cc_override: Optional[str] = None) -> dict:
    """主入口：取数 → 出两张图 → 发邮件。"""
    t0 = time.perf_counter()

    from backend.services.email.email_recipient_store import get_email_recipients
    to_arg, cc_arg = get_email_recipients(EMAIL_GROUP_KEY, to_override, cc_override)
    to_list = [a.strip() for a in (to_arg or "").split(";") if a.strip()]
    cc_list = [a.strip() for a in (cc_arg or "").split(";") if a.strip()]
    if not to_list:
        return {"success": False, "error": "收件人为空（Settings → 邮件收件人）"}
    logger.info("收件人: %d 主送, %d 抄送", len(to_list), len(cc_list))

    df = _fetch_mou_data()
    if df is None or df.empty:
        return {"success": False, "error": "International MOU 无可用数据"}

    run_dir = OUTPUT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    charts: List[Path] = []
    p = plot_series(df, "Outgoing MOU", "International Outgoing MOU (Daily)",
                    COLOR_OUT, run_dir / "International Outgoing MOU.png")
    if p:
        charts.append(p)
    p = plot_series(df, "Incoming MOU", "International Incoming MOU (Daily)",
                    COLOR_IN, run_dir / "International Incoming MOU.png")
    if p:
        charts.append(p)

    if not charts:
        return {"success": False, "error": "两张图都没有生成，跳过发送"}

    last = df.iloc[-1]
    body = (
        "<p>Dear Team,</p>"
        f"<p>Please find the International MOU traffic for the last {len(df)} complete days "
        f"(latest: <b>{last['Start Time'].strftime('%Y-%m-%d')}</b>).</p>"
        "<p>Last 7 days:</p>"
        + summary_table(df, ["Outgoing MOU", "Incoming MOU"])
        + INLINE_IMAGES_PLACEHOLDER
        + "<br><p>Best regards,<br>ZTENOC OLR</p>"
    )

    result = _send_email(to_list, cc_list, body, charts, run_dir)
    result["charts_generated"] = len(charts)
    result["rows"] = len(df)
    result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)

    cleanup_old_run_dirs(OUTPUT_DIR, keep_days=14)
    logger.info("任务完成: %s", result)
    return result


def run_intl_mou_chart_email() -> dict:
    """供 Task Manager 调用的入口。"""
    logger.info("开始 International MOU Daily Traffic 图表邮件任务")
    try:
        return run_intl_mou_daily_email()
    except Exception as e:  # noqa: BLE001 — 任务入口必须把异常转成失败结果
        logger.error("任务异常: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(run_intl_mou_chart_email())
