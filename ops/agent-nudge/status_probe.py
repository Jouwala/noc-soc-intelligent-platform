#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""noc-soc 多 Muse 状态巡检 + 主动提醒（止血工具）

背景（2026-11-20）：多 Muse 体系是 pull 模型——各 Muse 会话必须自己定时
去 GitHub 拉任务，但「定时拉活」只是 agent-polling-setup.md 里的人肉约定，
仓库里没有任何机器机制保证它在跑。结果状态视图显示「在线 0 人」、任务
无人认领最久 5 天、告警收件箱积压 11 条无人读，全靠用户问一句动一下。

本脚本补的就是这一层：只读地汇总 GitHub 上的真实状态，判断是否需要「提醒
人」，并在 --push 时走微信推送，让任务堆积/告警主动找人，不再等人问。

设计约束（沿用本仓库铁律）：
- 默认 dry-run：只读、只打印摘要，不写任何 GitHub 状态、不发消息；加 --push
  才推送，加 --act 才在 GitHub 发催办评论（本版 --act 预留，默认不启用）。
- 幂等：推送内容算签名存 TMP，签名不变（状态没变化）就不重复推，避免刷屏。
- 凭据不落盘、不打印：微信 SendKey/Token 从 credentials 表运行时读取，只用于
  发请求，绝不写日志；推送失败只记日志，不影响巡检退出码。
- 自包含：只用标准库 + gh CLI，不依赖 sqlalchemy / 后端运行环境，系统 Python
  即可跑（自动化任务用）。
- 不改 GitHub：本脚本对仓库只读（gh api GET），催办动作由治本 automation 的
  agent 按职责判断后手工执行，脚本不代做。

用法：
    python status_probe.py                 # 只读巡检，打印摘要（dry-run）
    python status_probe.py --push          # 巡检 + 有异常则推微信（幂等）
    python status_probe.py --json          # 输出结构化 JSON（供上层消费）

退出码：0=巡检成功（无论是否有异常）；2=配置/依赖错误；1=巡检过程异常。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
REPO = os.environ.get("NOCSOC_REPO", "Jouwala/noc-soc-intelligent-platform")
GH = os.environ.get("NOCSOC_GH", "gh")

# 生产树相对路径：本脚本在 <root>/ops/agent-nudge/ 下
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[1]  # ops/agent-nudge -> <root>
TMP_DIR = _REPO_ROOT / "TMP"
STATE_FILE = TMP_DIR / "_agent_nudge_last_push.json"
DB_PATH = _REPO_ROOT / "apps" / "my_app" / "db" / "app.db"

# 触发提醒的阈值
MIN_UNCLAIMED = 1        # 无人认领任务 >=1 就提醒
MIN_ALERT_COMMENTS = 1   # 告警收件箱评论 >=1 就提醒（积压即提醒）
ONLINE_ZERO_IS_ALERT = True  # 在线 0 人本身算异常

STATUS_TITLE_MARKER = "muse1-status-view"
INBOX_TITLE_MARKER = "muse1-alert-inbox"


# ---------------------------------------------------------------------------
# GitHub 只读读取（gh api）
# ---------------------------------------------------------------------------
def gh_api(path: str, *, jq: Optional[str] = None) -> Any:
    """调用 gh api；jq 为 --jq 表达式。返回解析后的 JSON 或文本。"""
    cmd = [GH, "api", path]
    if jq:
        cmd += ["--jq", jq]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api 失败 ({path}): {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    if jq:
        # jq 输出可能是 JSON 字符串或数字，尝试解析
        try:
            return json.loads(out)
        except (ValueError, TypeError):
            return out
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return out


def find_issue_by_marker(marker: str) -> Optional[dict]:
    """按正文机器可读标记找 open Issue（状态视图/告警收件箱各自唯一）。"""
    issues = gh_api(
        f"repos/{REPO}/issues?state=open&per_page=100",
        jq=f'.[] | select(.pull_request == null) | select(.body // "" | contains("{marker}")) | {{number,title,body,comments,updated_at}}',
    )
    if isinstance(issues, dict):  # gh jq 单结果返回 dict
        issues = [issues]
    if not issues:
        return None
    # 多个则取更新最新的
    return max(issues, key=lambda x: x.get("updated_at", ""))


# ---------------------------------------------------------------------------
# 解析状态视图正文
# ---------------------------------------------------------------------------
@dataclass
class StatusSnapshot:
    generated_at: str = ""
    online: int = 0
    idle: int = 0
    working: int = 0
    waiting: int = 0
    blocked: int = 0
    failed: int = 0
    total: int = 0
    running: int = 0
    unclaimed: int = 0
    qa_failed_now: int = 0
    longest_waiter: str = ""
    raw_excerpt: str = ""

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "online": self.online, "idle": self.idle, "working": self.working,
            "waiting": self.waiting, "blocked": self.blocked, "failed": self.failed,
            "total": self.total, "running": self.running,
            "unclaimed": self.unclaimed, "qa_failed_now": self.qa_failed_now,
            "longest_waiter": self.longest_waiter,
        }


_RE_GEN = re.compile(r"生成时间：(\S+)")
_RE_ONLINE = re.compile(r"- 在线：(\d+) 人（online (\d+) / idle (\d+) / working (\d+)）")
_RE_WAIT = re.compile(r"- 等待：(\d+) 人 / 阻塞：(\d+) 人 / 失败：(\d+) 人")
_RE_TASKS = re.compile(
    r"- 任务总数：(\d+)（执行中 (\d+) / 无人认领 (\d+) / 阻塞 (\d+) / 当前 QA 失败 (\d+)）"
)
_RE_WAITER = re.compile(r"- 等最久的未认领任务：(#\d+)（([^）]+)）")


def parse_status(body: str) -> StatusSnapshot:
    s = StatusSnapshot()
    m = _RE_GEN.search(body);  s.generated_at = m.group(1) if m else ""
    m = _RE_ONLINE.search(body)
    if m:
        s.online, s.idle, s.working = int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _RE_WAIT.search(body)
    if m:
        s.waiting, s.blocked, s.failed = int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _RE_TASKS.search(body)
    if m:
        s.total, s.running, s.unclaimed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        s.qa_failed_now = int(m.group(5))
    m = _RE_WAITER.search(body)
    if m:
        s.longest_waiter = f"{m.group(1)}（{m.group(2)}）"
    s.raw_excerpt = body[:1200]
    return s


# ---------------------------------------------------------------------------
# 判断是否需要提醒
# ---------------------------------------------------------------------------
@dataclass
class NudgeDecision:
    need_push: bool = False
    reasons: list[str] = field(default_factory=list)
    status: Optional[StatusSnapshot] = None
    inbox_comments: int = 0
    inbox_number: Optional[int] = None
    status_number: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "need_push": self.need_push,
            "reasons": self.reasons,
            "status": self.status.as_dict() if self.status else None,
            "inbox_comments": self.inbox_comments,
            "inbox_number": self.inbox_number,
            "status_number": self.status_number,
        }


def decide() -> NudgeDecision:
    d = NudgeDecision()
    status_issue = find_issue_by_marker(STATUS_TITLE_MARKER)
    inbox_issue = find_issue_by_marker(INBOX_TITLE_MARKER)

    if status_issue:
        d.status_number = status_issue["number"]
        d.status = parse_status(status_issue.get("body") or "")
        online_total = d.status.online + d.status.idle + d.status.working
        if ONLINE_ZERO_IS_ALERT and online_total == 0:
            d.reasons.append(f"在线 Muse 0 人（无 Worker 在轮询拉活）")
        if d.status.unclaimed >= MIN_UNCLAIMED:
            d.reasons.append(
                f"{d.status.unclaimed} 个任务无人认领"
                + (f"，最久 {d.status.longest_waiter}" if d.status.longest_waiter else "")
            )
        if d.status.qa_failed_now > 0:
            d.reasons.append(f"当前 QA 失败 {d.status.qa_failed_now} 个")
    else:
        d.reasons.append("找不到状态视图 Issue（#156）")

    if inbox_issue:
        d.inbox_number = inbox_issue["number"]
        d.inbox_comments = int(inbox_issue.get("comments") or 0)
        if d.inbox_comments >= MIN_ALERT_COMMENTS:
            d.reasons.append(f"告警收件箱 #{d.inbox_number} 积压 {d.inbox_comments} 条告警无人读")
    else:
        d.reasons.append("找不到告警收件箱 Issue（#148）")

    d.need_push = bool(d.reasons)
    return d


# ---------------------------------------------------------------------------
# 微信推送（标准库，凭据运行时读，绝不打印）
# ---------------------------------------------------------------------------
def read_weixin_credential() -> tuple[str, str]:
    """从 credentials 表读 weixin 组：返回 (serverchan_sendkey, pushplus_token)。

    兼容两种存储：secret 为 JSON（{"sendkey":..,"pushplus":..}）或纯文本
    （视为 ServerChan SendKey，历史遗留）。username 也可能是 SendKey。
    """
    sendkey, token = "", ""
    if not DB_PATH.exists():
        return sendkey, token
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = con.execute(
            "SELECT username, secret FROM credentials WHERE key='weixin'"
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return sendkey, token
    if not row:
        return sendkey, token
    username, secret = row
    if secret:
        try:
            obj = json.loads(secret)
            if isinstance(obj, dict):
                sendkey = str(obj.get("sendkey") or obj.get("send_key") or "")
                token = str(obj.get("pushplus") or obj.get("pushplus_token") or obj.get("token") or "")
        except (ValueError, TypeError):
            sendkey = secret if str(secret).startswith("SCT") else ""
    if not sendkey and username and str(username).startswith("SCT"):
        sendkey = str(username)
    return sendkey, token


def push_wechat(title: str, text: str) -> dict:
    """走 ServerChan / PushPlus 推送；返回 {"success","backend","error"}。

    MODE 由环境变量 NOCSOC_PUSH_MODE 决定：serverchan|pushplus|auto（默认 auto）。
    """
    sendkey, token = read_weixin_credential()
    mode = os.environ.get("NOCSOC_PUSH_MODE", "auto").strip().lower()
    url, payload, backend = "", {}, ""

    if mode in ("serverchan", "auto") and sendkey:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {"title": title, "desp": text}
        backend = "serverchan"
    elif mode in ("pushplus", "auto") and token:
        url = "http://www.pushplus.plus/send"
        payload = {"token": token, "title": title, "content": text, "template": "txt"}
        backend = "pushplus"
    else:
        return {"success": False, "backend": mode, "error": "无可用推送凭据"}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        ok = body.get("code") in (0, 200)
        return {"success": ok, "backend": backend, "error": "" if ok else str(body)[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "backend": backend, "error": f"{type(exc).__name__}: {exc}"[:200]}


def signature(decision: NudgeDecision) -> str:
    """内容签名：状态没变化则不重复推。"""
    key = json.dumps(decision.as_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def already_pushed(sig: str) -> bool:
    try:
        obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return obj.get("signature") == sig
    except (OSError, ValueError, TypeError):
        return False


def mark_pushed(sig: str) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"signature": sig, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render_human(decision: NudgeDecision) -> str:
    lines = ["【noc-soc 多 Muse 巡检】"]
    if decision.status:
        s = decision.status
        online = s.online + s.idle + s.working
        lines.append(
            f"在线 {online} 人 / 任务 {s.total}（执行 {s.running}、无人认领 {s.unclaimed}、QA失败 {s.qa_failed_now}）"
        )
        if s.longest_waiter:
            lines.append(f"等最久未认领：{s.longest_waiter}")
    lines.append("异常：")
    for i, r in enumerate(decision.reasons, 1):
        lines.append(f"  {i}. {r}")
    lines.append("建议：在对应 Muse 会话里说一句『去看 GitHub 并处理 #148 告警 / 认领最久的任务』。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="noc-soc 多 Muse 状态巡检 + 主动提醒")
    parser.add_argument("--push", action="store_true", help="有异常则推微信（默认只打印）")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    parser.add_argument("--force", action="store_true", help="忽略幂等签名，强制推送")
    args = parser.parse_args(argv)

    try:
        decision = decide()
    except Exception as exc:  # noqa: BLE001
        print(f"[status-probe] 巡检异常：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
        return 0

    summary = render_human(decision)
    print(summary)

    if not args.push:
        print("\n[dry-run] 未推送（加 --push 启用）。")
        return 0

    if not decision.need_push:
        print("\n[push] 无异常，不推送。")
        return 0

    sig = signature(decision)
    if not args.force and already_pushed(sig):
        print("\n[push] 状态签名未变化，跳过重复推送。")
        return 0

    result = push_wechat("noc-soc 任务堆积/告警提醒", summary)
    if result["success"]:
        mark_pushed(sig)
        print(f"\n[push] 已推送（{result['backend']}）。")
    else:
        print(f"\n[push] 推送失败（{result['backend']}）：{result['error']}", file=sys.stderr)
    return 0  # 推送失败不影响巡检语义


if __name__ == "__main__":
    raise SystemExit(main())
