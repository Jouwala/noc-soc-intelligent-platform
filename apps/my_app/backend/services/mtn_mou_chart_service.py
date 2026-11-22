# -*- coding: utf-8 -*-
"""
MTN MOU Daily Traffic 图表邮件
==============================

每天 07:00 从远程库 db_ps_cs_cn.sqlite3 的 MTN_Outgoing_MOU_OSS_EMS 读取
按日汇总好的 MTN 去话量，分别画 Outgoing MOU / Incoming MOU 两张折线图，
经项目统一的 EWS 邮件脚本（email_reports/owa_mail_report.py）内嵌图片发送。

数据口径（与入表口径一致，见 Task/PS_CS_CORE_Scripts/ps_cs_core.py）：
  - 该表每行 = 一个自然日，两个指标按天从中继组（16/17）加总而来；
  - 运行当天的数据不完整，读出来也一律剔除（只发完整自然日）。

收件人：Settings → 邮件收件人 → 「MTN MOU 日报邮件」分组（key=mtn_mou_daily_email），
未保存时回退默认名单 dong.xinmin@zte.com.cn。

调度：task_id = mtn_mou_daily_email，default_cron = 0 7 * * *
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

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

logger = logging.getLogger("mtn_mou_chart_service")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception as exc:
    logger.debug("设置 stdout 编码失败: %s", exc)

import pandas as pd

from backend.services.mou_chart_common import (
    COLOR_IN,
    COLOR_OUT,
    INLINE_IMAGES_PLACEHOLDER,
    cleanup_old_run_dirs,
    plot_series,
    summary_table,
)

# ===== 路径 / 数据源配置 =====
MY_APP_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = MY_APP_DIR / "TMP" / "mtn_mou_charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_REPORTS_DIR = MY_APP_DIR / "email_reports"
_MANAGED_VENV_PY = os.environ.get("MY_APP_REPORT_PY") or sys.executable

REMOTE_DB = "db_ps_cs_cn.sqlite3"
REMOTE_TABLE = "MTN_Outgoing_MOU_OSS_EMS"

SUBJECT = "MTN MOU Daily Traffic"
EMAIL_GROUP_KEY = "mtn_mou_daily_email"

# 展示窗口（完整自然日）
DISPLAY_DAYS = 31


def _fetch_mou_data() -> Optional[pd.DataFrame]:
    """从远程 API 读取按日 MOU 数据；先剔除运行当天（不完整）。"""
    try:
        from backend.services.remote.remote_db_reader import get_remote_reader

        reader = get_remote_reader()
        health = reader.health_check()
        if health.get("status") != "ok":
            logger.warning("远程 API 健康检查未通过: %s", health)

        result = reader.read_table(
            db=REMOTE_DB,
            table=REMOTE_TABLE,
            columns=["Start Time", "Outgoing MOU", "Incoming MOU"],
            order_by='"Start Time"',
            limit=2000,
        )
        rows = result.get("rows") or []
        if not rows:
            logger.warning("远程表 %s 没有数据", REMOTE_TABLE)
            return None

        df = pd.DataFrame(rows, columns=result.get("columns")
                          or ["Start Time", "Outgoing MOU", "Incoming MOU"])
    except Exception as e:
        logger.error("读取远程库失败: %s", e, exc_info=True)
        return None

    df["Start Time"] = pd.to_datetime(df["Start Time"], errors="coerce")
    df = df.dropna(subset=["Start Time"])
    for col in ("Outgoing MOU", "Incoming MOU"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")

    # 今天还没过完，不展示也不发送
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
    """经统一 EWS 邮件脚本发送（内嵌图片，不带 Excel）。"""
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
    except Exception as e:
        return {"success": False, "error": str(e)}

    if proc.returncode == 0:
        return {"success": True, "message": (proc.stdout or "")[-500:]}
    return {"success": False, "error": (proc.stderr or proc.stdout or "")[-1000:]}


def _cleanup_old_run_dirs(keep_days: int = 14) -> None:
    """清理历史运行目录，避免 PNG 无限堆积。"""
    try:
        import shutil
        cutoff = time.time() - keep_days * 86400
        for d in OUTPUT_DIR.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except Exception as exc:
        logger.debug("清理旧运行目录失败: %s", exc)


def run_mtn_mou_daily_email(to_override: Optional[str] = None,
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
        return {"success": False, "error": f"{REMOTE_TABLE} 无可用数据"}

    run_dir = OUTPUT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    charts: List[Path] = []
    p = plot_series(df, "Outgoing MOU", "MTN Outgoing MOU (Daily)",
                    COLOR_OUT, run_dir / "Outgoing MOU.png")
    if p:
        charts.append(p)
    p = plot_series(df, "Incoming MOU", "MTN Incoming MOU (Daily)",
                    COLOR_IN, run_dir / "Incoming MOU.png")
    if p:
        charts.append(p)

    if not charts:
        return {"success": False, "error": "两张图都没有生成，跳过发送"}

    last = df.iloc[-1]
    # 图片由 owa_mail_report.py 按 --images 自动内嵌（每张图上方带文件名作说明），
    # 正文里不要自己再写 <img>，否则会出现重复/断图；但必须留占位符，
    # 否则图片被追加到末尾、签名跑到图片中间。
    body = (
        "<p>Dear Team,</p>"
        f"<p>Please find the MTN MOU traffic for the last {len(df)} complete days "
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


def run_mtn_mou_chart_email() -> dict:
    """供 Task Manager 调用的入口。"""
    logger.info("开始 MTN MOU Daily Traffic 图表邮件任务")
    try:
        return run_mtn_mou_daily_email()
    except Exception as e:
        logger.error("任务异常: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    print(run_mtn_mou_chart_email())
