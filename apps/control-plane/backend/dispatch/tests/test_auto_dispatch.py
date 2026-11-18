"""auto-dispatch：并发只登记一次、重复投递幂等、超时对账、全量分页。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from dispatch.auto_dispatch import (
    dispatch_next,
    dispatch_scheduled,
    ensure_dispatch_comment,
    find_dispatch_comment,
    is_ready,
    run_from_env,
)
from dispatch.dispatch_registry import SOURCE_AUTO_DISPATCH, DispatchRegistry
from dispatch.github_client import GitHubAPIError

from dispatch.tests.fake_github import HEAD_SHA, MAIN_SHA, MERGE_SHA, FakeGitHub

ASSIGNED = "agent-msg: TASK_ASSIGNED"
DISPATCH_REF_PREFIX = "refs/heads/dispatch/pr-merged/"
CRON_REF_PREFIX = "refs/heads/dispatch/cron-sweep/"
NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def _dispatch(fake: FakeGitHub, *, pr_number: int = 31, timeout: float = 0.3):
    return dispatch_next(
        fake, fake.repo, pr_number, merge_sha=MERGE_SHA, reconcile_timeout=timeout
    )


# --------------------------------------------------------------------- 正常路径
def test_happy_path_dispatches_and_comments(fake: FakeGitHub, merged_pr) -> None:
    fake.seed_ready_issue(7)
    result = _dispatch(fake)
    assert result["status"] == "dispatched"
    assert result["issue"] == 7
    assert result["agent"] == "agent:muse-2"
    comments = fake.comments_for(7)
    assert len(comments) == 1
    assert ASSIGNED in comments[0]
    assert f"dispatch_id: {result['dispatch_id']}" in comments[0]
    assert "state:todo" in fake.labels_of(7)
    assert len(fake.refs_with_prefix(DISPATCH_REF_PREFIX)) == 1


def test_refs_anchor_is_merge_sha(fake: FakeGitHub, merged_pr) -> None:
    fake.seed_ready_issue(7)
    _dispatch(fake)
    refs = fake.refs_with_prefix(DISPATCH_REF_PREFIX)
    assert fake.refs[refs[0]] == MERGE_SHA


def test_no_ready_issue_only_logs(fake: FakeGitHub, merged_pr) -> None:
    result = _dispatch(fake)
    assert result["status"] == "no-ready"
    assert fake.refs_with_prefix(DISPATCH_REF_PREFIX) == []


def test_unmerged_pr_is_skipped(fake: FakeGitHub) -> None:
    fake.seed_pull(31, HEAD_SHA, merged=False)
    fake.seed_ready_issue(7)
    result = _dispatch(fake)
    assert result["status"] == "skip"
    assert fake.markers(7, ASSIGNED) == []


def test_pr_not_targeting_main_is_skipped(fake: FakeGitHub) -> None:
    fake.seed_pull(31, HEAD_SHA, merge_sha=MERGE_SHA, base="release")
    fake.seed_ready_issue(7)
    assert _dispatch(fake)["status"] == "skip"


def test_freed_builder_has_priority(fake: FakeGitHub) -> None:
    fake.seed_pull(31, HEAD_SHA, merge_sha=MERGE_SHA, labels=("agent:muse-3",))
    fake.seed_ready_issue(3, agent="agent:muse-2")
    fake.seed_ready_issue(5, agent="agent:muse-3")
    assert _dispatch(fake)["issue"] == 5


def test_pull_request_entries_are_not_candidates(fake: FakeGitHub, merged_pr) -> None:
    fake.seed_issue(3, labels=("state:todo", "agent:muse-2"), pull_request=True)
    fake.seed_ready_issue(7)
    assert _dispatch(fake)["issue"] == 7


# --------------------------------------------------------------------- 幂等 / 并发
def test_repeated_event_delivery_is_idempotent(fake: FakeGitHub, merged_pr) -> None:
    fake.seed_ready_issue(7)
    first = _dispatch(fake)
    second = _dispatch(fake)
    assert first["status"] == "dispatched"
    assert second["status"] == "reconciled"
    assert second["issues"] == [7]
    assert len(fake.markers(7, ASSIGNED)) == 1
    assert len(fake.refs_with_prefix(DISPATCH_REF_PREFIX)) == 1


def test_two_delivery_paths_share_one_dispatch_key(fake: FakeGitHub, merged_pr) -> None:
    """pull_request 与 workflow_dispatch 是同一逻辑事件的两条投递路径。"""
    fake.seed_ready_issue(7)
    env_event = {
        "REPO": fake.repo,
        "EVENT": "pull_request",
        "PR_NUMBER_EVENT": "31",
        "MERGE_SHA_EVENT": MERGE_SHA,
    }
    env_dispatch = {
        "REPO": fake.repo,
        "EVENT": "workflow_dispatch",
        "PR_NUMBER_INPUT": "31",
    }
    first = run_from_env(env_event, client=fake)
    second = run_from_env(env_dispatch, client=fake)
    assert first["status"] == "dispatched"
    assert second["status"] == "reconciled"
    assert len(fake.markers(7, ASSIGNED)) == 1
    assert len(fake.refs_with_prefix(DISPATCH_REF_PREFIX)) == 1


def test_two_concurrent_dispatch_runs_register_once(fake: FakeGitHub, merged_pr) -> None:
    fake.seed_ready_issue(7)
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            results.append(_dispatch(fake, timeout=0.5))
        except BaseException as exc:  # noqa: BLE001 - 线程里收集后断言
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(result["status"] for result in results) == ["dispatched", "reconciled"]
    assert len(fake.markers(7, ASSIGNED)) == 1
    assert len(fake.refs_with_prefix(DISPATCH_REF_PREFIX)) == 1


def test_status_writeback_failure_then_retry_aligns(fake: FakeGitHub, merged_pr) -> None:
    """评论已发、标签回写失败 → workflow 重试只对齐状态，不重复分派。"""
    fake.seed_ready_issue(7)
    fake.retry_limit = 0
    fake.inject(method="POST", path_contains="/labels", status=500, times=1)
    with pytest.raises(GitHubAPIError):
        _dispatch(fake, timeout=0.05)
    assert len(fake.markers(7, ASSIGNED)) == 1

    retried = _dispatch(fake, timeout=0.05)
    assert retried["status"] == "reconciled"
    assert retried["issues"] == [7]
    assert "state:blocked" not in fake.labels_of(7)
    assert len(fake.markers(7, ASSIGNED)) == 1


def test_comment_post_timeout_after_apply_does_not_duplicate(
    fake: FakeGitHub, merged_pr
) -> None:
    fake.seed_ready_issue(7)
    fake.inject(method="POST", path_contains="/comments", apply_first=True, times=1)
    result = _dispatch(fake)
    assert result["status"] == "dispatched"
    assert result["comment"] == "reconciled"
    assert len(fake.markers(7, ASSIGNED)) == 1


def test_registered_ref_without_comment_reconciles_once(fake: FakeGitHub, merged_pr) -> None:
    """登记后进程崩溃：ref 在、评论缺 → 重试补发一次评论，不重复。"""
    fake.seed_ready_issue(7)
    registry = DispatchRegistry(fake, fake.repo)
    registration = registry.register(
        "issue", 7, MERGE_SHA, SOURCE_AUTO_DISPATCH, anchor_sha=MERGE_SHA
    )
    assert registration.created is True
    first = _dispatch(fake, timeout=0.05)
    assert first["status"] == "reconciled"
    assert len(fake.markers(7, ASSIGNED)) == 1
    second = _dispatch(fake, timeout=0.05)
    assert second["status"] == "reconciled"
    assert len(fake.markers(7, ASSIGNED)) == 1


def test_comment_ledger_handles_reconcile_timeout(fake: FakeGitHub) -> None:
    fake.seed_issue(7)
    outcome = ensure_dispatch_comment(
        fake,
        7,
        "issue-7-deadbeef-0123456789",
        "<!-- agent-msg: TASK_ASSIGNED, dispatch_id: issue-7-deadbeef-0123456789 -->",
        wait_timeout=0.0,
    )
    assert outcome == "posted"
    assert find_dispatch_comment(fake, 7, "issue-7-deadbeef-0123456789") is True


# --------------------------------------------------------------------- 分页
def test_comments_on_second_page_block_readiness(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"))
    bodies = [f"进展 {i}" for i in range(149)]
    bodies.append("<!-- agent-claim: muse-2, 2026-11-13T19:30:00Z -->")
    fake.seed_comments(7, bodies)
    ok, reason = is_ready(fake, fake.get_issue(7))
    assert ok is False
    assert "认领" in reason


def test_assigned_marker_on_second_page_blocks_readiness(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"))
    bodies = [f"进展 {i}" for i in range(149)]
    bodies.append(f"<!-- {ASSIGNED}, task: #7 -->")
    fake.seed_comments(7, bodies)
    ok, reason = is_ready(fake, fake.get_issue(7))
    assert ok is False
    assert "分派" in reason


def test_issues_on_later_pages_are_discovered(fake: FakeGitHub, merged_pr) -> None:
    for number in range(1, 241):
        fake.seed_issue(number, labels=("state:todo",))  # 缺 agent 标签 → 不就绪
    fake.seed_ready_issue(245)
    result = _dispatch(fake)
    assert result["status"] == "dispatched"
    assert result["issue"] == 245


def test_comment_page_two_failure_is_explicit(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"))
    fake.seed_comments(7, [f"c{i}" for i in range(150)])
    fake.retry_limit = 0
    fake.inject(method="GET", path_contains="page=2", status=500, times=5)
    with pytest.raises(GitHubAPIError):
        is_ready(fake, fake.get_issue(7))


def test_dependency_query_failure_is_explicit(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"), body="depends on #99")
    fake.retry_limit = 0
    fake.inject(method="GET", path_contains="/issues/99", status=500, times=5)
    with pytest.raises(GitHubAPIError):
        is_ready(fake, fake.get_issue(7))


def test_open_dependency_blocks_readiness(fake: FakeGitHub) -> None:
    fake.seed_issue(3, state="open")
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"), body="depends on #3")
    ok, reason = is_ready(fake, fake.get_issue(7))
    assert ok is False
    assert "依赖" in reason


def test_closed_dependency_allows_readiness(fake: FakeGitHub) -> None:
    fake.seed_issue(3, state="closed")
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"), body="depends on #3")
    ok, _reason = is_ready(fake, fake.get_issue(7))
    assert ok is True


def test_main_sha_constant_used_by_fixture(fake: FakeGitHub) -> None:
    """锚点解析：事件没给 merge sha 时从 PR 取（这里校验测试常量确实对上）。"""
    assert fake.get_branch_sha("main") == MAIN_SHA


# --------------------------------------------------------------------- 白名单 / 依赖死锁
def test_registry_whitelist_makes_muse4_dispatchable(fake: FakeGitHub, merged_pr) -> None:
    """白名单来自注册表：新增的 Muse 4 不再因为代码里写死 muse-2/3 而派不出去。"""
    fake.seed_ready_issue(7, agent="agent:muse-4")
    result = _dispatch(fake)
    assert result["status"] == "dispatched"
    assert result["agent"] == "agent:muse-4"


def test_unknown_agent_label_is_not_ready(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:nobody"))
    ok, reason = is_ready(fake, fake.get_issue(7))
    assert ok is False
    assert "白名单" in reason


AUTO_FIX_BODY = (
    "<!-- auto-fix: for-issue-99 root-issue-99 round-1 sha-abc123 -->\n"
    "## 任务说明\n修复 #99 的 QA 失败项。\n\n"
    "## 依赖任务\n\n依赖：depends on #99\n"
)


def test_auto_fix_parent_dependency_is_ignored(fake: FakeGitHub) -> None:
    """修复任务对原 Issue 的 depends on 是自依赖：原 Issue 未关闭不再阻塞。"""
    fake.seed_issue(99, state="open")
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"), body=AUTO_FIX_BODY)
    ok, _reason = is_ready(fake, fake.get_issue(7))
    assert ok is True


def test_auto_fix_task_other_dependencies_still_block(fake: FakeGitHub) -> None:
    fake.seed_issue(99, state="open")
    fake.seed_issue(98, state="open")
    body = AUTO_FIX_BODY + "\ndepends on #98\n"
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"), body=body)
    ok, reason = is_ready(fake, fake.get_issue(7))
    assert ok is False
    assert "#98" in reason


# --------------------------------------------------------------------- 分派超时
def test_stale_assignment_allows_redispatch(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"))
    old = (NOW - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake.seed_comment(
        7,
        "<!-- agent-msg: TASK_ASSIGNED, task: #7, from: github-actions, "
        f"to: muse-2, at: {old} -->",
    )
    ok, _reason = is_ready(fake, fake.get_issue(7), now=NOW)
    assert ok is True


def test_fresh_assignment_still_blocks(fake: FakeGitHub) -> None:
    fake.seed_issue(7, labels=("state:todo", "agent:muse-2"))
    recent = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake.seed_comment(
        7,
        "<!-- agent-msg: TASK_ASSIGNED, task: #7, from: github-actions, "
        f"to: muse-2, at: {recent} -->",
    )
    ok, reason = is_ready(fake, fake.get_issue(7), now=NOW)
    assert ok is False
    assert "分派" in reason


# --------------------------------------------------------------------- 定时巡检（每小时 :17）
def test_schedule_dispatches_without_pr(fake: FakeGitHub) -> None:
    fake.seed_ready_issue(7)
    result = dispatch_scheduled(fake, fake.repo)
    assert result["status"] == "dispatched"
    assert result["event"] == "schedule"
    assert result["issue"] == 7
    assert len(fake.refs_with_prefix(CRON_REF_PREFIX)) == 1
    comments = fake.comments_for(7)
    assert len(comments) == 1
    assert "定时巡检" in comments[0]
    assert "TASK_ASSIGNED" in comments[0]


def test_schedule_without_ready_issue_only_logs(fake: FakeGitHub) -> None:
    result = dispatch_scheduled(fake, fake.repo)
    assert result["status"] == "no-ready"
    assert fake.refs_with_prefix(CRON_REF_PREFIX) == []


def test_schedule_second_round_does_not_dispatch_again(fake: FakeGitHub) -> None:
    fake.seed_ready_issue(7)
    assert dispatch_scheduled(fake, fake.repo)["status"] == "dispatched"
    # 任务已被分派（24h 内）→ 第二轮不再就绪，也不会发第二条评论。
    assert dispatch_scheduled(fake, fake.repo)["status"] == "no-ready"
    assert len(fake.markers(7, ASSIGNED)) == 1


def test_run_from_env_schedule_event(fake: FakeGitHub) -> None:
    fake.seed_ready_issue(7)
    result = run_from_env({"REPO": fake.repo, "EVENT": "schedule"}, client=fake)
    assert result["status"] == "dispatched"
    assert result["event"] == "schedule"


# --------------------------------------------------------------------- Issue 事件触发（P1-3 即时分派）
ISSUE_REF_PREFIX = "refs/heads/dispatch/issue-event/"


def test_issue_event_dispatches_without_pr(fake: FakeGitHub) -> None:
    """新 Issue 创建 / 依赖关闭时即时分派：来源记 issue-event，不等巡检。"""
    fake.seed_ready_issue(7)
    result = run_from_env({"REPO": fake.repo, "EVENT": "issues"}, client=fake)
    assert result["status"] == "dispatched"
    assert result["event"] == "issues"
    assert result["issue"] == 7
    assert len(fake.refs_with_prefix(ISSUE_REF_PREFIX)) == 1
    assert len(fake.refs_with_prefix(CRON_REF_PREFIX)) == 0
    comments = fake.comments_for(7)
    assert len(comments) == 1
    assert "TASK_ASSIGNED" in comments[0]
    # 发现 1（PR #127 审核）：Issue 路径的 intro 必须是即时分派文案，
    # 不能出现并不存在的 "PR #0"。
    assert "新 Issue 创建 / 依赖 Issue 关闭，触发即时分派" in comments[0]
    assert "PR #0" not in comments[0]


def test_issue_event_then_schedule_does_not_dispatch_twice(fake: FakeGitHub) -> None:
    """跨来源不重复分派：issues 先派，定时巡检后轮只对账不重派。"""
    fake.seed_ready_issue(7)
    first = run_from_env({"REPO": fake.repo, "EVENT": "issues"}, client=fake)
    assert first["status"] == "dispatched"
    second = run_from_env({"REPO": fake.repo, "EVENT": "schedule"}, client=fake)
    assert second["status"] == "no-ready"
    assert len(fake.refs_with_prefix(CRON_REF_PREFIX)) == 0
    assert len(fake.markers(7, ASSIGNED)) == 1


def test_schedule_then_issue_event_does_not_dispatch_twice(fake: FakeGitHub) -> None:
    """跨来源不重复分派（反向）：定时先派，Issue 事件后轮只对账不重派。"""
    fake.seed_ready_issue(7)
    first = run_from_env({"REPO": fake.repo, "EVENT": "schedule"}, client=fake)
    assert first["status"] == "dispatched"
    second = run_from_env({"REPO": fake.repo, "EVENT": "issues"}, client=fake)
    assert second["status"] == "no-ready"
    assert len(fake.refs_with_prefix(ISSUE_REF_PREFIX)) == 0
    assert len(fake.markers(7, ASSIGNED)) == 1


def test_issue_event_without_ready_issue_only_logs(fake: FakeGitHub) -> None:
    result = run_from_env({"REPO": fake.repo, "EVENT": "issues"}, client=fake)
    assert result["status"] == "no-ready"
    assert result["event"] == "issues"
    assert fake.refs_with_prefix(ISSUE_REF_PREFIX) == []


def test_issue_event_second_round_does_not_dispatch_again(fake: FakeGitHub) -> None:
    fake.seed_ready_issue(7)
    first = run_from_env({"REPO": fake.repo, "EVENT": "issues"}, client=fake)
    assert first["status"] == "dispatched"
    # 任务已被分派（24h 内）→ 第二轮不再就绪，也不会发第二条评论。
    second = run_from_env({"REPO": fake.repo, "EVENT": "issues"}, client=fake)
    assert second["status"] == "no-ready"
    assert len(fake.markers(7, ASSIGNED)) == 1
