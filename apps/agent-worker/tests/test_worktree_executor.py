"""Worktree 分配/钩子校验 + 执行器范围守卫（真实临时 git 仓库）。"""

from __future__ import annotations

import subprocess

from conftest import ex, wt


def _branch_of(path) -> str:
    out = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_allocate_validate_release(git_repo):
    allocator = wt.WorktreeAllocator(git_repo)
    path = allocator.allocate("task-99")
    assert path.exists()
    assert _branch_of(path) == "agent/task-99"
    assert allocator.allocate("task-99") == path  # 幂等

    report = allocator.validate_hooks(path)
    assert report.ok, report.problems
    assert any("pre-commit" in c for c in report.checks)

    assert allocator.release("task-99", force=True) is True
    assert not path.exists()


def test_hooks_validation_flags_missing_path(git_repo):
    allocator = wt.WorktreeAllocator(git_repo)
    path = allocator.allocate("task-98")
    subprocess.run(
        ["git", "-C", str(path), "config", "--unset", "core.hooksPath"],
        capture_output=True,
    )
    # worktree 共用主仓库 config，unset 后校验应明确报错而不是静默通过
    report = allocator.validate_hooks(path)
    assert not report.ok
    assert any("hooksPath" in p for p in report.problems)
    allocator.release("task-98", force=True)


def test_executor_scope_guard_and_commit(git_repo):
    allocator = wt.WorktreeAllocator(git_repo)
    path = allocator.allocate("task-97")
    executor = ex.Executor()

    def job(wt_path):
        (wt_path / "result.txt").write_text("ok\n", encoding="utf-8")
        (wt_path / "stray.txt").write_text("不应提交\n", encoding="utf-8")
        return 0

    result = executor.run(job, path, scope=["result.txt"])
    assert result.exit_code == 0
    assert result.out_of_scope == ["stray.txt"]
    assert not result.ok  # 有超范围改动 → 不算成功

    sha = executor.commit_in_scope(path, ["result.txt"], "test: in-scope only")
    assert sha
    assert executor.changed_files(path) == ["stray.txt"]  # 只有超范围文件还留在工作区
    allocator.release("task-97", force=True)
