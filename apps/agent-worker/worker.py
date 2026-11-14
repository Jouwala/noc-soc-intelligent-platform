"""分布式 Agent Worker 主循环（TASK-03）。

全流程（对 agent-protocol.md 的落地）：
注册 → 心跳 → 轮询认领（agent:<我> + state:todo 且无活租约）→ 一任务一
Worktree（钩子校验）→ 文件范围锁（重叠串行化）→ 执行（attempt 防重复）
→ 结果回传（TASK_COMPLETED + state:in-review）。

断线恢复：执行结果先落盘到 Worktree 的 TMP/ 再回传；回传遇 TransportError
时退避重试，重连后先查租约——
- 租约仍属我且未过期：续租后继续回传（同一 attempt，天然幂等）；
- 租约已过期且任务被重派/他人已回传：放弃本地结果，删 Worktree，
  绝不二次回传（适配器 complete() 对过期 attempt 也会 StaleAttempt 拒绝）。

在 Windows 目标机上以 Worker 自身 GitHub 身份运行：
    python worker.py --agent muse-2 --repo-root D:\\02_olbr_portal
环境变量：GITHUB_OWNER / GITHUB_REPO / GITHUB_TOKEN（或 GH_TOKEN）。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: E402

_gi = _bootstrap.load_adapter()
_worktree_mod = _bootstrap.load_sibling("worktree")
_executor_mod = _bootstrap.load_sibling("executor")

GitHubIssueAdapter = _gi.GitHubIssueAdapter
RestGitHubTransport = _gi.RestGitHubTransport
TransportError = _gi.TransportError
LeaseConflict = _gi.LeaseConflict
StaleAttempt = _gi.StaleAttempt
ScopeConflict = _gi.ScopeConflict
parse_file_scope = _gi.parse_file_scope
WorktreeAllocator = _worktree_mod.WorktreeAllocator
Executor = _executor_mod.Executor


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class WorkerConfig:
    agent_id: str = "muse-2"
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    poll_interval_s: float = 30.0
    executor_timeout_s: float = 1800.0
    scope_wait_timeout_s: float = 1800.0    # 等范围锁的最长秒数，超时发 TASK_BLOCKED
    recovery_budget_s: float = 900.0        # 断线后回传重试的总预算
    recovery_backoff_s: float = 10.0
    capabilities: tuple[str, ...] = ("python", "windows-worker")


@dataclass
class _ActiveTask:
    issue: int
    slug: str
    worktree: Path
    attempt_id: str = ""
    result_summary: str = ""
    commit_sha: Optional[str] = None


class AgentWorker:
    def __init__(
        self,
        adapter: GitHubIssueAdapter,
        allocator: WorktreeAllocator,
        executor: Executor,
        config: WorkerConfig,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        runner: Optional[Callable[[Path], int]] = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.adapter = adapter
        self.allocator = allocator
        self.executor = executor
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.runner = runner  # 注入的任务执行体（测试用）；None 时解析 Issue 的「执行命令」
        self.log = log
        self._active: Optional[_ActiveTask] = None

    # -- 基础设施 ---------------------------------------------------------------
    @property
    def heartbeat_file(self) -> Path:
        return self.allocator.base / f".heartbeat-{self.config.agent_id}.json"

    def _write_heartbeat(self) -> None:
        self.allocator.base.mkdir(parents=True, exist_ok=True)
        payload = {
            "agent": self.config.agent_id,
            "instance": self.config.instance_id,
            "at": self.clock().astimezone(timezone.utc).isoformat(),
        }
        self.heartbeat_file.write_text(json.dumps(payload), encoding="utf-8")

    def register(self) -> None:
        info = self.adapter.register(self.config.capabilities, vm=os.environ.get("COMPUTERNAME", ""))
        self.log(f"[worker] 已注册：{info}")

    # -- 命令解析 -----------------------------------------------------------------
    @staticmethod
    def _command_from_issue(body: str) -> Optional[list[str]]:
        """从 Issue 正文「执行命令」一节的代码块取第一行命令；没有返回 None。"""
        in_section = False
        in_block = False
        for line in (body or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("##"):
                in_section = "执行命令" in stripped
                in_block = False
                continue
            if in_section and stripped.startswith("```"):
                if in_block:
                    break
                in_block = True
                continue
            if in_section and in_block and stripped:
                # shlex 而非 str.split：命令里带引号的参数（如 python3 -c "..."）
                # 用 split 会把引号内的空格也切开，导致 SyntaxError。
                # 引号不成对时 shlex 抛 ValueError，退回 split 保底。
                try:
                    return shlex.split(stripped)
                except ValueError:
                    return stripped.split()
        return None

    # -- 主流程 ---------------------------------------------------------------------
    def run_once(self) -> str:
        """一个轮询周期：心跳 + 最多处理一个任务。返回本周期结果码。"""
        self._write_heartbeat()
        try:
            self.adapter.heartbeat()
        except TransportError as exc:
            self.log(f"[worker] 心跳失败（疑似断网）：{exc}")
            return "offline"
        claimable = self.adapter.list_claimable()
        if not claimable:
            return "idle"
        return self.handle_task(claimable[0])

    def handle_task(self, issue) -> str:
        cfg = self.config
        try:
            self.adapter.claim(issue.number)
        except LeaseConflict as exc:
            self.log(f"[worker] 认领被拒：{exc}")
            return "claim-conflict"
        slug = f"task-{issue.number}"
        worktree = self.allocator.allocate(slug)
        active = _ActiveTask(issue=issue.number, slug=slug, worktree=worktree)
        self._active = active

        hooks = self.allocator.validate_hooks(worktree)
        if not hooks.ok:
            self.adapter.report_blocked(
                issue.number,
                "Worktree 钩子校验未通过，任务阻塞：\n- " + "\n- ".join(hooks.problems),
            )
            return "blocked-hooks"

        scope = parse_file_scope(issue.body)
        if scope:
            waited = 0.0
            while True:
                try:
                    self.adapter.acquire_scope(issue.number, scope)
                    break
                except ScopeConflict as exc:
                    if waited >= cfg.scope_wait_timeout_s:
                        self.adapter.report_blocked(
                            issue.number, f"文件范围被占用且等待超时，任务阻塞：{exc}"
                        )
                        return "blocked-scope"
                    self.log(f"[worker] 范围冲突，串行化等待：{exc}")
                    self.sleep(cfg.poll_interval_s)
                    waited += cfg.poll_interval_s

        attempt_id = uuid.uuid4().hex[:12]
        active.attempt_id = attempt_id
        self.adapter.begin_attempt(issue.number, attempt_id)

        command = self.runner or self._command_from_issue(issue.body)
        if command is None:
            self.adapter.report_blocked(
                issue.number, "Issue 未提供「执行命令」，且未注入 runner，任务阻塞。"
            )
            return "blocked-no-command"

        result = self.executor.run(
            command,
            worktree,
            scope=scope,
            timeout_s=cfg.executor_timeout_s,
            log_dir=worktree / "TMP",
        )
        active.result_summary = result.summary
        # 结果先落盘再回传：断网/崩溃后可凭 attempt 幂等续传，不重复执行
        (worktree / "TMP").mkdir(parents=True, exist_ok=True)
        (worktree / "TMP" / "execution-result.json").write_text(
            json.dumps(
                {"attempt": attempt_id, "exit_code": result.exit_code, "summary": result.summary},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if not result.ok:
            self._safe_call(
                lambda: self.adapter.report_blocked(
                    issue.number, f"执行未成功：{result.summary}（attempt: {attempt_id}）"
                ),
                issue.number,
            )
            return "failed"

        if scope:
            active.commit_sha = self.executor.commit_in_scope(
                worktree,
                scope,
                f"{cfg.agent_id}: task-{issue.number} 执行结果（attempt {attempt_id}）",
            )
        summary = result.summary
        if active.commit_sha:
            summary += f"；提交 {active.commit_sha[:12]}"
        outcome = self._safe_call(
            lambda: self.adapter.complete(issue.number, attempt_id, summary), issue.number
        )
        if outcome == "aborted":
            return "aborted-reassigned"
        self._safe_call(lambda: self.adapter.release_scope(issue.number), issue.number)
        self._active = None
        return "completed"

    # -- 断线恢复 ---------------------------------------------------------------------
    def _safe_call(self, fn: Callable[[], None], issue_number: int) -> str:
        """执行一次适配器写操作；断网时退避重试并在重连后核对租约。

        返回 "ok" / "aborted"（租约已不属于我或尝试已过期——放弃，防重复执行）。
        """
        deadline = self.clock() + timedelta(seconds=self.config.recovery_budget_s)
        while True:
            try:
                fn()
                return "ok"
            except (StaleAttempt, LeaseConflict):
                return "aborted"
            except TransportError as exc:
                if self.clock() >= deadline:
                    raise
                self.log(f"[worker] 断网，退避重试：{exc}")
                self.sleep(self.config.recovery_backoff_s)
                # 重连后先核对租约：已重派给他人就立刻放弃，不再重试回传
                try:
                    lease = self.adapter.lease_of(issue_number)
                except TransportError:
                    continue
                if lease is not None and lease.holder != self.config.agent_id:
                    return "aborted"
                if lease is not None and lease.holder == self.config.agent_id and lease.is_live(self.clock()):
                    try:
                        self.adapter.renew(issue_number, note="断线恢复后续租")
                    except TransportError:
                        continue

    def serve(self, max_cycles: Optional[int] = None) -> None:
        self.register()
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                outcome = self.run_once()
                self.log(f"[worker] 周期结果：{outcome}")
            except TransportError as exc:
                self.log(f"[worker] 周期遇断网：{exc}")
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                self.sleep(self.config.poll_interval_s)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="分布式 Agent Worker（TASK-03）")
    parser.add_argument("--agent", default="muse-2")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--owner", default=os.environ.get("GITHUB_OWNER", "Jouwala"))
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPO", "noc-soc-intelligent-platform")
    )
    parser.add_argument("--registry-issue", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="只跑一个轮询周期")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    transport = RestGitHubTransport(args.owner, args.repo)
    adapter = GitHubIssueAdapter(transport, args.agent, registry_issue=args.registry_issue)
    worker = AgentWorker(
        adapter,
        WorktreeAllocator(repo_root),
        Executor(),
        WorkerConfig(agent_id=args.agent),
    )
    if args.once:
        worker.register()
        print(worker.run_once())
        return 0
    worker.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
