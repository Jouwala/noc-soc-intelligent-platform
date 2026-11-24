"""分布式 Agent Worker 主循环（TASK-03 / TASK-06 / TASK-10 / TASK-11 / TASK-13）。

流程（agent-protocol.md + 多 Worker 中央调度器契约）：

注册（`scheduler.register_worker`，带 instance_id / hostname / platform）
→ 心跳（后台线程持续原子写文件；与任务租约续期是两条独立职责）
→ 轮询认领（`adapter.list_claimable()` 只读发现 → `scheduler.upsert_task()`
  → `scheduler.claim()`；认领权威只在 claim，带 attempt_id + fencing_token）
→ 一任务一 Worktree（钩子校验，POSIX 可执行位）
→ 文件范围锁（`scheduler.acquire_scope`）
→ 执行（executor：独立进程组，超时/取消杀整棵树；独立取消轮询线程）
→ 结果回传（`scheduler.complete` 幂等键 attempt_id；完成评论/标签由 ref 权威层直接写 GitHub）
→ 统一清理（try/finally：范围锁、租约、_active、worktree、子进程确认退出）。

清理策略（TASK-06 验收口径）：

- 成功：worktree 保留（交付物/分支要给人 review），租约与范围锁由调度器终结；
- 执行失败 / 超时 / 阻塞：保留 worktree 诊断产物（execution.log、
  execution-result.json），但**不得残留**租约、范围锁、_active；
- 取消 / 内部异常：确认子进程退出后强制回收 worktree（取消即放弃本轮产物）；
- 清理自身异常只记录（`cleanup_errors`）并写日志，绝不覆盖正在传播的原始异常；
- 子进程树未确认退出（`ExecutionResult.tree_exited=False`）时一律不删 worktree。

崩溃语义：结果先落盘到 worktree/TMP/execution-result.json 再回传；租约过期被
重派后旧实例的回传会被 fencing/attempt 校验拒绝（StaleFencing），同一 attempt
的幂等键保证不会产生第二条结果。

写操作与清理的完整实现集中在 `task_lifecycle.py`（本文件只做编排）。
"""

from __future__ import annotations

import argparse
import json
import os
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
_heartbeat = _bootstrap.load_sibling("heartbeat")
_cancel_mod = _bootstrap.load_sibling("cancellation")
_hostinfo = _bootstrap.load_sibling("hostinfo")
_command_parse = _bootstrap.load_sibling("command_parse")
_lifecycle = _bootstrap.load_sibling("task_lifecycle")

GitHubIssueAdapter = _gi.GitHubIssueAdapter
RestGitHubTransport = _gi.RestGitHubTransport
TransportError = _gi.TransportError
parse_file_scope = _gi.parse_file_scope
WorktreeAllocator = _worktree_mod.WorktreeAllocator
Executor = _executor_mod.Executor
ActiveTask = _lifecycle.ActiveTask

TASK_ID_PREFIX = "issue-"


def task_id_for_issue(issue_number: int) -> str:
    """GitHub Issue → 调度器 task_id（sched-core / dispatch 统一约定）。"""
    return f"{TASK_ID_PREFIX}{issue_number}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: 自动构建的 agent 任务书相对 worktree 的位置（TMP 已被 gitignore）。
AGENT_PROMPT_REL = Path("TMP") / "agent-task.md"


def render_task_prompt(issue) -> str:
    """把 Issue 渲染成给 coding agent 的任务书（纯函数，可单测）。"""
    labels = "、".join(getattr(issue, "labels", ()) or ()) or "（无）"
    body = (getattr(issue, "body", "") or "").strip() or "（正文为空）"
    title = (getattr(issue, "title", "") or "").strip() or f"Issue #{issue.number}"
    return (
        f"# Issue #{issue.number}：{title}\n\n"
        f"- 标签：{labels}\n"
        f"- 分支：当前 worktree 的分支（不要切分支，不要直接推 main）\n\n"
        "## 任务正文（GitHub Issue 原文）\n\n"
        f"{body}\n\n"
        "## 执行要求（常驻 Worker 自动派发）\n\n"
        "1. 先读仓库根 `AGENTS.md` 与相关子系统手册，按其约束执行。\n"
        "2. 只改 Issue「文件修改范围」声明的文件；超出范围先停下来写阻塞说明。\n"
        "3. 完成后把改动提交到当前分支，推送并创建/更新 PR；**不要合并 PR**。\n"
        "4. 缺凭据、需要业务口径决定、需要改 VMAX 数仓时不要猜，写清阻塞原因后退出。\n"
    )


def write_task_prompt(worktree: Path, issue) -> Path:
    """把任务书写进 worktree 的 `TMP/agent-task.md`，返回路径。"""
    target = Path(worktree) / AGENT_PROMPT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    # 固定文件名（不按 Issue 分）：worktree 是一任务一个，同名不会互相覆盖。
    target.write_text(render_task_prompt(issue), encoding="utf-8")
    return target


def build_agent_argv(
    template: tuple[str, ...] | list[str],
    issue_number: int,
    prompt_file: Path,
    worktree: Path,
    branch: str,
) -> list[str]:
    """按占位符展开 agent 命令模板（纯函数，可单测）。

    占位符只认这四个；未知 `{...}` 原样保留，交给被调用方处理。
    """
    mapping = {
        "{issue}": str(issue_number),
        "{prompt_file}": str(Path(prompt_file)),
        "{worktree}": str(Path(worktree)),
        "{branch}": branch,
    }
    argv: list[str] = []
    for part in template or ():
        for key, value in mapping.items():
            part = part.replace(key, value)
        argv.append(part)
    return argv


_scheduler_cache = None


def scheduler_module():
    """延迟加载调度器包：测试注入 FakeScheduler 时不强依赖实现层，且
    sched-core 并行开发期间 import 失败不会连累 Worker 模块加载。"""
    global _scheduler_cache
    if _scheduler_cache is None:
        _scheduler_cache = _bootstrap.load_scheduler()
    return _scheduler_cache


def scheduler_factory():
    """延迟加载 `scheduler.factory`（ADR-7 §五：PR-5 起 Worker 只接 GitHub 权威）。

    必须等 `scheduler_module()` 把 `apps/control-plane/backend` 放进 sys.path 之后
    才能按标准包导入子模块，因此单独封装，不在模块顶层 import。
    """
    scheduler_module()
    from scheduler import factory  # noqa: PLC0415 - 依赖上方 sys.path 就绪

    return factory


@dataclass
class WorkerConfig:
    agent_id: str = "muse-2"
    #: 运行实例身份：同一 agent 可扩多实例，租约/防重复执行按它区分
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    repo: str = ""
    poll_interval_s: float = 30.0
    executor_timeout_s: float = 1800.0
    scope_wait_timeout_s: float = 1800.0  # 等范围锁的最长秒数，超时标记 blocked
    recovery_budget_s: float = 900.0      # 保留字段：镜像重试预算（drain 内部处理）
    recovery_backoff_s: float = 10.0
    heartbeat_interval_s: float = 30.0    # 看门狗 stale 默认 180s，必须明显大于它
    cancel_poll_interval_s: float = 5.0
    lease_ttl_s: float = 600.0
    lease_renew_interval_s: float = 150.0  # 执行期续租间隔（< TTL）
    drain_limit: int = 100
    capabilities: tuple[str, ...] = field(default_factory=lambda: _hostinfo.capabilities())
    #: 只处理带齐这些标签的 Issue（空 = 不限制）。常驻 Worker 用
    #: `worker:auto` 做显式 opt-in，见 `AgentWorker._eligible`。
    require_labels: tuple[str, ...] = ()
    #: Issue 没写「执行命令」时使用的 agent argv 模板（占位符
    #: `{issue}` / `{prompt_file}` / `{worktree}` / `{branch}`）。
    #: 空 = 保持旧行为（阻塞在 blocked-no-command）。
    agent_command: tuple[str, ...] = ()


class AgentWorker:
    def __init__(
        self,
        adapter: GitHubIssueAdapter,
        allocator: WorktreeAllocator,
        executor: Executor,
        config: WorkerConfig,
        scheduler,
        *,
        mirror=None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
        runner: Optional[Callable[[Path], int]] = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.adapter = adapter
        self.allocator = allocator
        self.executor = executor
        self.config = config
        self.scheduler = scheduler
        self.mirror = mirror
        self.clock = clock
        self.sleep = sleep
        self.runner = runner  # 注入的任务执行体（测试用）；None 时解析 Issue 的「执行命令」
        self.log = log
        self._active: Optional[ActiveTask] = None
        self._heartbeat: Optional[object] = None
        self.cleanup_errors: list[str] = []
        self.lifecycle = _lifecycle.TaskLifecycle(
            scheduler, allocator, config, mirror=mirror, clock=clock, log=log
        )

    # -- 基础设施 ---------------------------------------------------------------
    @property
    def heartbeat_file(self) -> Path:
        return _heartbeat.heartbeat_path(self.allocator.base, self.config.agent_id)

    @property
    def heartbeat(self):
        return self._heartbeat

    @property
    def active(self) -> Optional[ActiveTask]:
        return self._active

    def _ensure_heartbeat(self):
        if self._heartbeat is None:
            self._heartbeat = _heartbeat.HeartbeatWriter(
                self.heartbeat_file,
                agent_id=self.config.agent_id,
                instance_id=self.config.instance_id,
                interval_s=self.config.heartbeat_interval_s,
                clock=self.clock,
                log=self.log,
            )
        return self._heartbeat

    def register(self) -> dict:
        """向中央调度器注册运行实例（TASK-11：主机名/平台/能力按真实环境）。"""
        info = _hostinfo.collect()
        snapshot = self.scheduler.register_worker(
            self.config.agent_id,
            self.config.instance_id,
            hostname=info["hostname"],
            platform=info["platform"],
            capabilities=self.config.capabilities,
        )
        self.log(
            f"[worker] 已注册：agent={self.config.agent_id} "
            f"instance={self.config.instance_id} host={info['hostname']!r} "
            f"platform={info['platform']} caps={list(self.config.capabilities)}"
        )
        self.lifecycle.drain_mirror()  # 启动/重连先重放上次没写回的 GitHub 操作
        return {
            "agent": self.config.agent_id,
            "instance": self.config.instance_id,
            "hostname": info["hostname"],
            "platform": info["platform"],
            "capabilities": tuple(self.config.capabilities),
            "snapshot": snapshot,
        }

    # -- 命令解析（TASK-13） -----------------------------------------------------
    @staticmethod
    def _command_from_issue(body: str) -> Optional[list[str]]:
        return _command_parse.extract_command(body)

    def _eligible(self, issues: list) -> list:
        """只保留带齐 `require_labels` 的 Issue（空配置 = 不过滤，兼容旧行为）。

        2026-11-24 流程提速批：常驻 Worker 只接显式打了 `worker:auto` 的
        任务，避免把「等人来做」的 Issue 也自动跑掉。
        """
        required = set(self.config.require_labels)
        if not required:
            return issues
        return [
            issue
            for issue in issues
            if required.issubset(set(getattr(issue, "labels", ()) or ()))
        ]

    def _auto_agent_command(
        self, active: ActiveTask, issue
    ) -> Optional[list[str]]:
        """按 `agent_command` 模板生成 argv；没配置或没 worktree 时返回 None。"""
        template = self.config.agent_command
        if not template or active.worktree is None:
            return None
        prompt_file = write_task_prompt(active.worktree, issue)
        return build_agent_argv(
            template,
            issue.number,
            prompt_file,
            active.worktree,
            f"agent/{active.slug}",
        )

    # -- 主流程 ---------------------------------------------------------------------
    def run_once(self) -> str:
        """一个轮询周期：本地心跳线程 + 拉取可认领任务 + 最多处理一个任务。

        调度器心跳在状态已知时写（无任务 → idle，认领后 → working），不再在
        周期开头写 online——否则 online beat 会抢占 60s 节流窗口，idle/working
        永远写不进去。连通性探测由 list_claimable 承担（失败即 offline）。
        """
        writer = self._ensure_heartbeat()
        writer.update(task_id="", state="polling")
        writer.start()
        try:
            self.lifecycle.drain_mirror()
            try:
                claimable = self.adapter.list_claimable()
            except TransportError as exc:
                self.log(f"[worker] 拉取任务失败（疑似断网）：{exc}")
                return "offline"
            claimable = self._eligible(claimable)
            if not claimable:
                try:
                    self.scheduler.heartbeat_worker(
                        self.config.agent_id, self.config.instance_id, state="idle"
                    )
                except Exception as exc:  # noqa: BLE001 - 调度器短暂不可用不退出常驻循环
                    self.log(f"[worker] 调度器心跳失败（疑似断网/DB 不可用）：{exc}")
                    return "offline"
                return "idle"
            return self.handle_task(claimable[0])
        finally:
            writer.update(state="idle")
            writer.stop()

    def handle_task(self, issue) -> str:
        S = scheduler_module()
        cfg = self.config
        task_id = task_id_for_issue(issue.number)
        active = ActiveTask(
            issue=issue.number,
            task_id=task_id,
            slug=f"task-{issue.number}",
            scope=tuple(parse_file_scope(issue.body) or ()),
        )
        original_exc: Optional[BaseException] = None
        outcome = "error"
        try:
            # 1) 登记 + 权威认领（先 upsert 后 claim；upsert 幂等且不改状态）
            try:
                self.lifecycle.upsert(issue, active)
                attempt_id = uuid.uuid4().hex[:12]
                lease = self.scheduler.claim(
                    task_id,
                    agent_id=cfg.agent_id,
                    instance_id=cfg.instance_id,
                    attempt_id=attempt_id,
                    lease_ttl=timedelta(seconds=cfg.lease_ttl_s),
                )
            except S.LeaseHeld as exc:
                self.log(f"[worker] 认领被拒：{exc}")
                try:
                    self.scheduler.heartbeat_worker(
                        cfg.agent_id, cfg.instance_id, state="waiting"
                    )
                except Exception:  # noqa: BLE001 - 心跳失败不影响主流程
                    pass
                return "claim-conflict"
            except S.AlreadyTerminal as exc:
                self.log(f"[worker] 认领被拒：{exc}")
                return "claim-conflict"
            active.attempt_id = lease.attempt_id or attempt_id
            active.fencing_token = lease.fencing_token
            active.lease_held = True
            self._active = active
            if self._heartbeat is not None:
                self._heartbeat.update(task_id=task_id, state="running")
            try:
                self.scheduler.heartbeat_worker(
                    cfg.agent_id, cfg.instance_id, state="working"
                )
            except Exception as exc:  # noqa: BLE001 - 心跳失败不影响已认领的任务
                self.log(f"[worker] 调度器心跳失败：{exc}")
            self.log(
                f"[worker] 已认领 {task_id} attempt={active.attempt_id} "
                f"token={active.fencing_token}"
            )

            # 2) worktree + 钩子校验（含 POSIX 可执行位）
            active.worktree = self.allocator.allocate(active.slug)
            hooks = self.allocator.validate_hooks(active.worktree)
            if not hooks.ok:
                self.lifecycle.mark_blocked(
                    active,
                    "hooks-invalid",
                    "Worktree 钩子校验未通过，任务阻塞：\n- " + "\n- ".join(hooks.problems),
                    S,
                )
                # 阻塞也要上报心跳（best-effort：节流窗口内可能写不出，见 #141 复审 P2-5）。
                try:
                    self.scheduler.heartbeat_worker(
                        cfg.agent_id, cfg.instance_id, state="blocked"
                    )
                except Exception:  # noqa: BLE001 - 心跳失败不影响主流程
                    pass
                outcome = "blocked-hooks"
                return outcome

            # 3) 范围锁
            if active.scope and not self.lifecycle.acquire_scope(active, S, self.sleep):
                outcome = "aborted-reassigned" if active.aborted else "blocked-scope"
                return outcome

            # 4) 执行命令（结构化 JSON argv 优先，平台感知 shlex 兜底）；
            #    Issue 没写「执行命令」时，若配置了 agent 模板（常驻 Worker），
            #    自动生成任务书并交给 coding agent 执行（2026-11-24）。
            command = (
                self.runner
                or self._command_from_issue(issue.body)
                or self._auto_agent_command(active, issue)
            )
            if command is None:
                self.lifecycle.mark_blocked(
                    active,
                    "no-command",
                    "Issue 未提供「执行命令」，且未注入 runner，任务阻塞。",
                    S,
                )
                # 阻塞也要上报心跳（best-effort：节流窗口内可能写不出，见 #141 复审 P2-5）。
                try:
                    self.scheduler.heartbeat_worker(
                        cfg.agent_id, cfg.instance_id, state="blocked"
                    )
                except Exception:  # noqa: BLE001 - 心跳失败不影响主流程
                    pass
                outcome = "blocked-no-command"
                return outcome

            # 5) 进入 running（状态机要求 claimed → running → 终态）
            if not self.lifecycle.mark_running(active, S):
                if self.lifecycle.cancel_requested(active.task_id, S):
                    ok = self.lifecycle.ack_cancel(active, "执行前已请求取消", S)
                    outcome = "cancelled" if ok else "aborted-reassigned"
                else:
                    outcome = "aborted-reassigned" if active.aborted else "error"
                return outcome

            # 6) 执行（取消轮询线程 + 执行期续租；杀进程组的细节在 executor）
            result = self._execute(active, command, S)
            active.result_summary = result.summary
            self._persist_result(active, result)

            # 7) 取消优先于完成（取消任务绝不 complete）
            if self.lifecycle.cancel_requested(active.task_id, S):
                ok = self.lifecycle.ack_cancel(active, f"{result.summary}（执行期检测到取消）", S)
                outcome = "cancelled" if ok else "aborted-reassigned"
                return outcome
            if result.killed_reason == "timeout":
                ok = self.lifecycle.complete_failure(active, "executor-timeout", result.summary, S)
                outcome = "timeout" if ok else "aborted-reassigned"
                return outcome
            if not result.ok:
                ok = self.lifecycle.complete_failure(active, "execution-failed", result.summary, S)
                outcome = "failed" if ok else "aborted-reassigned"
                return outcome

            # 8) 提交（只提交范围内文件）+ 幂等完成 + 镜像回写
            if active.scope:
                active.commit_sha = self.executor.commit_in_scope(
                    active.worktree,
                    active.scope,
                    f"{cfg.agent_id}: task-{issue.number} 执行结果（attempt {active.attempt_id}）",
                )
            summary = result.summary
            if active.commit_sha:
                summary += f"；提交 {active.commit_sha[:12]}"
            ok = self.lifecycle.complete(active, summary, S)
            outcome = "completed" if ok else "aborted-reassigned"
            return outcome
        except Exception as exc:  # noqa: BLE001 - 内部异常：清理后原样抛出
            original_exc = exc
            self.log(f"[worker] 任务 {task_id} 内部异常（清理后重新抛出）：{exc!r}")
            try:
                self.lifecycle.mark_retryable(active, "internal-error", repr(exc), S)
            except Exception as mark_exc:  # noqa: BLE001 - 清理期异常只记录
                self.log(f"[worker] 内部异常落状态失败：{mark_exc!r}")
            raise
        finally:
            reclaim = self.lifecycle.cleanup(
                active, outcome, original_exc=original_exc, heartbeat=self._heartbeat
            )
            self._active = None
            self.cleanup_errors = reclaim.errors

    # -- 执行期辅助 -------------------------------------------------------------
    def _execute(self, active: ActiveTask, command, S):
        cfg = self.config
        watcher = _cancel_mod.CancelWatcher(
            self.scheduler,
            active.task_id,
            interval_s=cfg.cancel_poll_interval_s,
            log=self.log,
        )
        last_renew = {"at": self.clock()}

        def on_tick() -> None:
            now = self.clock()
            if (now - last_renew["at"]).total_seconds() >= cfg.lease_renew_interval_s:
                last_renew["at"] = now
                if not self.lifecycle.renew(active, S):
                    watcher.trigger()  # 租约已丢：立刻停掉执行，别再替新实例干活

        def on_spawn(pid: int, pgid: int) -> None:
            # 把任务子进程 pid/pgid 写进心跳（#77）：本进程若异常退出，
            # 看门狗靠这条持久记录回收上一代的孤儿进程树。
            if self._heartbeat is not None:
                self._heartbeat.update(task_pid=pid, task_pgid=pgid)

        watcher.start()
        try:
            result = self.executor.run(
                command,
                active.worktree,
                scope=active.scope,
                timeout_s=cfg.executor_timeout_s,
                log_dir=active.worktree / "TMP",
                cancel_event=watcher.event,
                on_tick=on_tick,
                on_spawn=on_spawn,
            )
        except Exception:
            # 执行器自身异常：无法确认子进程树是否退出 → 保守起来不让删 worktree；
            # 心跳里的 task_pid 也不清除，留给看门狗按 #77 口径回收。
            active.executor_confirmed_exit = False
            raise
        finally:
            watcher.stop()
        active.executor_confirmed_exit = result.tree_exited
        if result.tree_exited and self._heartbeat is not None:
            # 进程树已确认退出：清掉心跳里的 pid 记录，免得下一代误回收。
            # tree_exited=False 时保留记录——那棵树可能还活着，正是 #77 要收的。
            self._heartbeat.update(task_pid=0, task_pgid=0)
        return result

    def _persist_result(self, active: ActiveTask, result) -> None:
        """执行结果先落盘再回传：崩溃后凭 attempt 幂等续传，不重复执行。"""
        if active.worktree is None:
            return
        tmp = active.worktree / "TMP"
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "execution-result.json").write_text(
            json.dumps(
                {
                    "attempt": active.attempt_id,
                    "task_id": active.task_id,
                    "exit_code": result.exit_code,
                    "killed_reason": result.killed_reason,
                    "tree_exited": result.tree_exited,
                    "summary": result.summary,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    # -- 常驻循环 -------------------------------------------------------------------
    def serve(self, max_cycles: Optional[int] = None) -> None:
        self.register()
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                outcome = self.run_once()
                self.log(f"[worker] 周期结果：{outcome}")
            except TransportError as exc:
                self.log(f"[worker] 周期遇断网：{exc}")
            except Exception as exc:  # noqa: BLE001 - 常驻循环不因单任务异常退出
                self.log(f"[worker] 周期异常（已清理，继续轮询）：{exc!r}")
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
    parser.add_argument(
        "--agent-cmd",
        default=os.environ.get("MUSE_AGENT_CMD", ""),
        help=(
            "Issue 未写「执行命令」时的 agent 命令模板（如 claude -p ...）；"
            "占位符 {issue} / {prompt_file} / {worktree} / {branch}"
        ),
    )
    parser.add_argument(
        "--require-labels",
        default=os.environ.get("MUSE_REQUIRE_LABELS", ""),
        help="只处理带齐这些标签的 Issue（逗号分隔，常驻 Worker 用 worker:auto）",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    agent_cmd_text = (args.agent_cmd or "").strip()
    agent_command = (
        tuple(_command_parse.split_command(agent_cmd_text)) if agent_cmd_text else ()
    )
    require_labels = tuple(
        part.strip() for part in (args.require_labels or "").split(",") if part.strip()
    )
    config = WorkerConfig(
        agent_id=args.agent,
        repo=f"{args.owner}/{args.repo}",
        require_labels=require_labels,
        agent_command=agent_command,
    )
    # ADR-7 §零：GitHub refs 是唯一权威。先建 GitHub 传输层，再交给工厂组装
    # GitHubRefScheduler；PR-5 起不再有 SQLite 回退分支（回滚靠 revert PR）。
    transport = RestGitHubTransport(args.owner, args.repo)
    try:
        scheduler = scheduler_factory().make_scheduler(
            transport=transport,
            repo=config.repo,
            lease_ttl=timedelta(seconds=config.lease_ttl_s),
            registry_issue=args.registry_issue,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"[worker] 调度器初始化失败：{exc}", file=sys.stderr)
        return 2
    adapter = GitHubIssueAdapter(transport, args.agent, registry_issue=args.registry_issue)
    worker = AgentWorker(
        adapter, WorktreeAllocator(repo_root), Executor(), config, scheduler
    )
    if args.once:
        worker.register()
        print(worker.run_once())
        return 0
    worker.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
