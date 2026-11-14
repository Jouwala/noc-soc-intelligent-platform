"""断线恢复验收（Issue #18）：断网 5 分钟——

- 租约未过期：重连后续租并回传，全程恰好一次 TASK_COMPLETED；
- 租约已过期且被重派：原 Worker 的迟到回传被拒，零重复执行。
"""

from __future__ import annotations

from datetime import timedelta

from conftest import gi, wk

BODY = "## 目标\n写结果文件。\n## 改动文件\n- `result.txt`\n"


class FlakyTransport:
    """包一层内存传输：fail_until 之前所有调用都抛 TransportError（模拟本机断网）。"""

    def __init__(self, inner, clock):
        self.inner = inner
        self.clock = clock
        self.fail_until = None

    def _gate(self):
        if self.fail_until is not None and self.clock() < self.fail_until:
            raise gi.TransportError("模拟断网")

    def list_issues(self, labels=(), state="open"):
        self._gate()
        return self.inner.list_issues(labels, state)

    def get_issue(self, number):
        self._gate()
        return self.inner.get_issue(number)

    def list_comments(self, number):
        self._gate()
        return self.inner.list_comments(number)

    def add_comment(self, number, body, author=""):
        self._gate()
        return self.inner.add_comment(number, body, author)

    def set_labels(self, number, labels):
        self._gate()
        return self.inner.set_labels(number, labels)


def _make_worker(adapter, git_repo, clock, sleep, runner):
    return wk.AgentWorker(
        adapter,
        wk.WorktreeAllocator(git_repo),
        wk.Executor(),
        wk.WorkerConfig(
            agent_id="muse-2",
            poll_interval_s=5,
            recovery_backoff_s=10,
            recovery_budget_s=900,
            scope_wait_timeout_s=120,
        ),
        clock=clock,
        sleep=sleep,
        runner=runner,
    )


def _completed_count(transport, number) -> int:
    return sum(
        1
        for c in transport.list_comments(number)
        if (gi.parse_header(c.body) or {}).get("type") == "TASK_COMPLETED"
    )


def test_disconnect_5min_lease_alive_resumes(transport, clock, git_repo):
    flaky = FlakyTransport(transport, clock)
    adapter = gi.GitHubIssueAdapter(flaky, "muse-2", clock=clock, lease_ttl=timedelta(hours=4))
    issue = transport.create_issue(
        61, "断网恢复（租约未过期）", BODY, labels=("agent:muse-2", "state:todo")
    )

    def runner(wt_path):
        (wt_path / "result.txt").write_text("done\n", encoding="utf-8")
        flaky.fail_until = clock.now + timedelta(minutes=5)  # 执行完回传时开始断网 5 分钟
        return 0

    worker = _make_worker(adapter, git_repo, clock, lambda s: clock.advance(s), runner)
    assert worker.handle_task(issue) == "completed"
    assert _completed_count(transport, 61) == 1
    assert "state:in-review" in transport.get_issue(61).labels
    # 恢复路径确实走了续租
    assert any("断线恢复后续租" in c.body for c in transport.list_comments(61))


def test_disconnect_lease_expired_reassigned_no_duplicate(transport, clock, git_repo):
    flaky = FlakyTransport(transport, clock)
    adapter_a = gi.GitHubIssueAdapter(
        flaky, "muse-2", clock=clock, lease_ttl=timedelta(seconds=120)
    )
    issue = transport.create_issue(
        62, "断网恢复（租约过期重派）", BODY, labels=("agent:muse-2", "state:todo")
    )
    start = clock.now
    state = {"reassigned": False}

    def runner(wt_path):
        (wt_path / "result.txt").write_text("A 的结果\n", encoding="utf-8")
        flaky.fail_until = clock.now + timedelta(seconds=150)  # A 断网 150 秒 > 租约 120 秒
        return 0

    def sleep_and_reassign(seconds):
        clock.advance(seconds)
        # 租约过期后：Muse 1 把标签摘回 todo 重派，另一实例（同身份）接手并完成
        if not state["reassigned"] and clock.now >= start + timedelta(seconds=130):
            state["reassigned"] = True
            transport.set_labels(62, ("agent:muse-2", "state:todo"))
            adapter_b = gi.GitHubIssueAdapter(
                transport, "muse-2", clock=clock, lease_ttl=timedelta(seconds=120)
            )
            adapter_b.claim(62)
            adapter_b.begin_attempt(62, "b-attempt")
            adapter_b.complete(62, "b-attempt", "重派后由新实例完成。")

    worker = _make_worker(adapter_a, git_repo, clock, sleep_and_reassign, runner)
    assert worker.handle_task(issue) == "aborted-reassigned"
    assert state["reassigned"] is True
    # 全程恰好一次完成回传（B 的），A 的迟到回传被 StaleAttempt 拒绝
    assert _completed_count(transport, 62) == 1
