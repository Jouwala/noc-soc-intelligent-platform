"""卡死 CI 运行巡检（2026-11-24 流程提速批）。

背景：2026-11-24 实测过一次 quality 运行 **156 分钟、0 个 job** 的异常
（run 45389371289，PR #266）。工作流级卡死不报错、也不释放，PR 只能干等；
靠人盯 Actions 页面不可靠。

本模块每 15 分钟（`.github/workflows/run-watchdog.yml`）巡检 `quality`
工作流的运行，按事实做两件事：

1. **卡死则取消**：`queued` / `in_progress` 且超过阈值仍未结束的运行
   → `POST /actions/runs/{id}/cancel` 释放；下一次推送会重新触发。
2. **零 job 失败则重跑**：`completed` + `failure` + **一个 job 都没有**
   （工作流文件解析失败 / runner 分配异常）且 `run_attempt < MAX_ATTEMPTS`
   → `POST /actions/runs/{id}/rerun`。

判定函数 `classify_run()` 是纯函数（不读网络），单测覆盖；CLI 只做
HTTP 调用。**零第三方依赖**（标准库），可直接在 Actions 跑。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

#: 运行超过这个时长仍未结束，视为卡死（quality 单 job 超时 25 分钟）。
STUCK_AFTER = timedelta(minutes=30)

#: 零 job 失败最多自动重跑几次（含第一次失败本身）。
MAX_ATTEMPTS = 2

#: 单轮最多对多少个运行动手，避免脚本失控。
MAX_ACTIONS_PER_CYCLE = 10

#: 只巡检这个工作流（release-build / runtime-build 有各自的人工节奏）。
WATCHED_WORKFLOW = "quality.yml"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_run(
    run: dict, job_count: int, now: datetime
) -> tuple[str, str]:
    """判定单个运行该做什么，返回 (动作, 原因)。

    动作：`cancel` / `rerun` / `ok`。
    任何字段缺失或时间解析失败都返回 `ok` —— 宁可不动作，也不误杀。
    """
    status = (run.get("status") or "").strip()
    conclusion = (run.get("conclusion") or "").strip()
    attempt = int(run.get("run_attempt") or 1)

    if status in ("queued", "in_progress"):
        started = _parse_time(run.get("run_started_at")) or _parse_time(
            run.get("created_at")
        )
        if started is None:
            return "ok", "起止时间不可解析，不动作"
        age = now - started
        if age <= STUCK_AFTER:
            return "ok", f"运行中（{int(age.total_seconds() // 60)} 分钟）"
        return "cancel", (
            f"卡死：{status} 已 {int(age.total_seconds() // 60)} 分钟"
            f"（阈值 {int(STUCK_AFTER.total_seconds() // 60)} 分钟）"
        )

    if status == "completed" and conclusion == "failure":
        if job_count != 0:
            return "ok", "普通失败，留给代码修复"
        if attempt >= MAX_ATTEMPTS:
            return "ok", f"零 job 失败已重跑 {attempt - 1} 次，不再自动重跑"
        return "rerun", f"零 job 失败（第 {attempt} 次尝试），自动重跑"

    return "ok", f"状态 {status or '未知'}/{conclusion or '-'} 不需处理"


class GitHubAPI:
    """最小 GitHub REST 客户端（标准库，fail-soft）。"""

    def __init__(self, repo: str, token: str, base: str = "https://api.github.com"):
        self.repo = repo
        self.token = token
        self.base = base.rstrip("/")

    def _request(self, method: str, path: str, payload: dict | None = None):
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base}/repos/{self.repo}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}

    def list_runs(self, status: str, per_page: int = 50) -> list[dict]:
        data = self._request(
            "GET",
            f"/actions/workflows/{WATCHED_WORKFLOW}/runs"
            f"?status={status}&per_page={per_page}",
        )
        return list(data.get("workflow_runs") or [])

    def job_count(self, run_id: int) -> int:
        data = self._request(
            "GET", f"/actions/runs/{run_id}/jobs?per_page=1&filter=latest"
        )
        return int(data.get("total_count") or 0)

    def cancel(self, run_id: int) -> None:
        self._request("POST", f"/actions/runs/{run_id}/cancel")

    def rerun(self, run_id: int) -> None:
        self._request("POST", f"/actions/runs/{run_id}/rerun")


def run_cycle(
    api: GitHubAPI,
    now: datetime,
    log=print,
    max_actions: int = MAX_ACTIONS_PER_CYCLE,
) -> list[tuple[int, str]]:
    """跑一轮巡检，返回 [(run_id, 动作)]。单条失败只记录，不中断整轮。"""
    done: list[tuple[int, str]] = []
    actions = 0
    for status in ("in_progress", "queued", "completed"):
        try:
            runs = api.list_runs(status)
        except Exception as exc:  # noqa: BLE001 - 单个状态拉取失败不影响其他状态
            log(f"[watchdog] 拉取 {status} 运行失败：{exc}")
            continue
        for run in runs:
            if actions >= max_actions:
                log(f"[watchdog] 本轮动作数达上限 {max_actions}，提前结束")
                return done
            run_id = int(run.get("id") or 0)
            if not run_id:
                continue
            try:
                jobs = api.job_count(run_id)
            except Exception as exc:  # noqa: BLE001 - 读不到 job 数不动作
                log(f"[watchdog] 读 run {run_id} 的 job 数失败：{exc}")
                continue
            action, why = classify_run(run, jobs, now)
            if action == "ok":
                continue
            log(f"[watchdog] run {run_id}：{why} -> {action}")
            try:
                if action == "cancel":
                    api.cancel(run_id)
                else:
                    api.rerun(run_id)
            except Exception as exc:  # noqa: BLE001 - 单条失败继续下一条
                log(f"[watchdog] run {run_id} {action} 失败：{exc}")
                continue
            done.append((run_id, action))
            actions += 1
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="卡死 CI 运行巡检")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印判定，不调用 cancel/rerun"
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("缺少 GH_TOKEN / GITHUB_TOKEN", flush=True)
        return 1

    api = GitHubAPI(args.repo, token)
    if args.dry_run:
        for status in ("in_progress", "queued", "completed"):
            try:
                runs = api.list_runs(status)
            except Exception as exc:  # noqa: BLE001
                print(f"拉取 {status} 运行失败：{exc}", flush=True)
                continue
            for run in runs:
                run_id = int(run.get("id") or 0)
                try:
                    jobs = api.job_count(run_id)
                except Exception:  # noqa: BLE001
                    continue
                print(
                    f"run {run_id} status={run.get('status')} "
                    f"conclusion={run.get('conclusion')} jobs={jobs} -> "
                    f"{classify_run(run, jobs, datetime.now(timezone.utc))}",
                    flush=True,
                )
        return 0

    done = run_cycle(api, datetime.now(timezone.utc))
    print(f"本轮处理 {len(done)} 条：{done}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
