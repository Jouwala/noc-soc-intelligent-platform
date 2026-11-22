# -*- coding: utf-8 -*-
"""MOU 日报图表邮件的公共件（MTN / International 共用）。

为什么单独成文件（2026-11-22）
==============================
MTN MOU 与 International MOU 两支日报的数据源不同，但「按日折线图 +
最近 7 天汇总表 + 经 owa_mail_report.py 内嵌图片发信」完全同构。第二个
真实用例（International）出现时才把出图/汇总表/清理这三块抽出来，避免
两份 200 行代码各自漂移 —— 改这里的样式两边都会跟着变，这是刻意的单点。

⚠️ 发信函数 **刻意留在各任务自己的模块里**，没有抽到这里：
    backend/services/email/email_task_coverage.py 用 AST 在**任务入口模块内**
    找 `owa_mail_report` 标记，据此把任务自动并入 EMAIL_TASK_IDS（EWS 降级
    后的补发名单）。把发信调用挪进本模块，两个任务会从补发名单里静默消失，
    而且「任务没登记补发分支」的自检也会因为集合缩水而假绿。
    动这块之前先读 email_task_coverage.py 的文件头。

图表风格与 ccn_free_rating_chart_service / ran_ume 看板保持一致。
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger("mou_chart_common")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception as exc:  # noqa: BLE001 — 控制台编码问题不该影响任务
    logger.debug("设置 stdout 编码失败: %s", exc)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# 与既有看板统一的配色
COLOR_OUT = "#2E86AB"
COLOR_IN = "#F18F01"
BG_COLOR = "#E8F0FE"
TITLE_COLOR = "#2F5496"
GRID_COLOR = "#C5C5C5"
TEXT_COLOR = "#333333"

# 正文里的内嵌图片插入点。owa_mail_report.py 按这个占位符把 --images 的图片插进正文；
# 正文里没有占位符时图片会**追加到正文末尾**，签名 "Best regards" 就会被挤到图片中间
# （2026-11-17 用户反馈：MTN MOU Daily Traffic 的图落在 "Best regards, ZTENOC OLR" 下面）。
# 常量必须与 email_reports/owa_mail_report.py 的 _INLINE_PLACEHOLDER 完全一致。
INLINE_IMAGES_PLACEHOLDER = "<!-- inline images inserted here -->"


def safe_savefig(fig, output_path: Path) -> Path:
    """保存图表；同名文件被占用时重试，仍失败则换带序号的文件名。"""
    output_path = Path(output_path)
    for attempt in range(4):
        try:
            fig.savefig(output_path, dpi=150, bbox_inches="tight",
                        facecolor=BG_COLOR, edgecolor="none")
            return output_path
        except PermissionError:
            if attempt < 3:
                time.sleep(0.5)
                continue
            break
    idx = 1
    stem, suffix = output_path.stem, output_path.suffix
    while True:
        alt = output_path.with_name(f"{stem}_{idx}{suffix}")
        if not alt.exists():
            break
        idx += 1
    fig.savefig(alt, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    return alt


def compact_number(v: float, _pos=None) -> str:
    """Y 轴刻度用紧凑单位，避免 matplotlib 的 1e6 偏移量。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


def plot_series(df: pd.DataFrame, column: str, title: str, color: str,
                output_path: Path, x_col: str = "Start Time",
                y_label: str = "MOU") -> Optional[Path]:
    """画单指标折线图（按 x_col 的日期序列）。"""
    if column not in df.columns or df[column].notna().sum() == 0:
        logger.warning("无 %s 数据，跳过出图", column)
        return None

    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.set_facecolor(BG_COLOR)
    fig.patch.set_facecolor(BG_COLOR)

    x = list(range(len(df)))
    ax.plot(x, df[column], linewidth=1.6, color=color, label=column)
    ax.fill_between(x, df[column].fillna(0), color=color, alpha=0.12)
    ax.set_ylim(bottom=0)

    # 最新一个点的数值直接标在图上
    last_x = x[-1]
    last_v = df[column].iloc[-1]
    if pd.notna(last_v):
        ax.annotate(compact_number(last_v), (last_x, last_v),
                    textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize=8, color=color, fontweight="bold")

    ax.set_title(title, fontsize=13, fontweight="bold", color=TITLE_COLOR, pad=12)
    ax.set_ylabel(y_label, fontsize=9, color=TEXT_COLOR)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(compact_number))
    ax.grid(axis="y", linestyle="--", color=GRID_COLOR, linewidth=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#B0B0B0")
        spine.set_linewidth(0.5)

    labels = [t.strftime("%m-%d") for t in df[x_col]]
    n = len(labels)
    step = max(1, n // 16) if n > 16 else 1
    positions = list(range(0, n, step))
    ax.set_xticks(positions)
    ax.set_xticklabels([labels[i] for i in positions],
                       rotation=45, fontsize=7, ha="right")
    ax.tick_params(axis="both", labelsize=8)
    ax.legend(loc="upper left", fontsize=8, frameon=True,
              facecolor="white", framealpha=0.8, edgecolor="#CCCCCC")

    plt.tight_layout()
    final_path = safe_savefig(fig, output_path)
    plt.close(fig)
    logger.info("图表已保存: %s", Path(final_path).name)
    return Path(final_path)


def summary_table(df: pd.DataFrame, columns: Sequence[str],
                  headers: Optional[Sequence[str]] = None,
                  x_col: str = "Start Time") -> str:
    """正文里的最近 7 天汇总表（HTML）。"""
    headers = list(headers or columns)
    head_cells = "<th>Date</th>" + "".join(f"<th>{h}</th>" for h in headers)
    rows: List[str] = []
    for _, r in df.tail(7).iterrows():
        cells = [f"<td>{r[x_col].strftime('%Y-%m-%d')}</td>"]
        for col in columns:
            v = r.get(col)
            cells.append(f"<td align='right'>{'' if pd.isna(v) else f'{v:,.2f}'}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<table border='1' cellspacing='0' cellpadding='6' "
        "style='border-collapse:collapse;font-family:Segoe UI,Arial;font-size:12px'>"
        "<thead><tr style='background:#2F5496;color:#fff'>"
        + head_cells
        + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def cleanup_old_run_dirs(output_dir: Path, keep_days: int = 14) -> None:
    """清理历史运行目录，避免 PNG 无限堆积。"""
    try:
        cutoff = time.time() - keep_days * 86400
        for d in output_dir.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 — 清理失败不影响发信
        logger.debug("清理旧运行目录失败: %s", exc)
