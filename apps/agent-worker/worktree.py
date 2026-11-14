"""Worktree 分配器 + 钩子校验（TASK-03）。

协议约定（agent-protocol.md 第六节）：一任务一分支 `agent/<任务>`，
一任务一 Worktree `.agent-worktrees/<任务>`。

钩子校验针对 AGENTS.md 第四节第 9 条记录的真实坑：core.hooksPath 是相对
路径、按当前工作树根解析，worktree 里若所在分支还没合入 .githooks，
钩子会静默不执行。validate_hooks() 把这件事变成显式检查结果而不是靠自觉。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class HookReport:
    ok: bool
    checks: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


class WorktreeAllocator:
    def __init__(self, repo_root: Path, base_dir: str = ".agent-worktrees") -> None:
        self.repo_root = Path(repo_root)
        self.base = self.repo_root / base_dir

    # -- 路径 -----------------------------------------------------------------
    def path_for(self, slug: str) -> Path:
        if not _SLUG_RE.match(slug):
            raise ValueError(f"非法任务 slug：{slug!r}（只允许字母数字与 ._-，且不以 . 开头）")
        return self.base / slug

    def branch_for(self, slug: str) -> str:
        return f"agent/{slug}"

    # -- 分配 / 释放 -------------------------------------------------------------
    def allocate(self, slug: str, branch: str | None = None) -> Path:
        """为任务分配 worktree：已存在直接复用；分支不存在则新建。幂等。"""
        path = self.path_for(slug)
        branch = branch or self.branch_for(slug)
        if path.exists() and (path / ".git").exists():
            return path
        self.base.mkdir(parents=True, exist_ok=True)
        _git(self.repo_root, "worktree", "prune")
        exists = _git(self.repo_root, "rev-parse", "--verify", f"refs/heads/{branch}")
        if exists.returncode == 0:
            result = _git(self.repo_root, "worktree", "add", str(path), branch)
        else:
            result = _git(self.repo_root, "worktree", "add", "-b", branch, str(path))
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add 失败：{result.stderr.strip()}")
        return path

    def release(self, slug: str, force: bool = False) -> bool:
        """释放 worktree（默认只移除干净的；force 时连同未提交改动一起丢弃）。"""
        path = self.path_for(slug)
        if not path.exists():
            return False
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        result = _git(self.repo_root, *args)
        if result.returncode != 0:
            raise RuntimeError(f"git worktree remove 失败：{result.stderr.strip()}")
        _git(self.repo_root, "worktree", "prune")
        return True

    # -- 钩子校验 ---------------------------------------------------------------
    def validate_hooks(self, worktree: Path) -> HookReport:
        """校验目标 worktree 的提交钩子是否真的生效，返回结构化报告（不抛异常）。"""
        report = HookReport(ok=True)
        hooks_dir = self.repo_root / ".githooks"

        if hooks_dir.is_dir():
            report.checks.append(".githooks/ 目录存在")
        else:
            report.ok = False
            report.problems.append("仓库根 .githooks/ 不存在，钩子无从谈起")

        pre_commit = hooks_dir / "pre-commit"
        if pre_commit.is_file():
            report.checks.append(".githooks/pre-commit 存在")
        else:
            report.ok = False
            report.problems.append(".githooks/pre-commit 缺失")

        cfg = _git(worktree, "config", "--get", "core.hooksPath")
        hooks_path = cfg.stdout.strip() if cfg.returncode == 0 else ""
        if hooks_path:
            resolved = (worktree / hooks_path).resolve()
            # 判据是「git 实际会去找钩子的地方确实有 pre-commit」：
            # worktree 的 checkout 里带 .githooks 时，解析到 worktree 自己那份也算生效。
            if resolved.is_dir() and (resolved / "pre-commit").is_file():
                report.checks.append(f"core.hooksPath={hooks_path} 解析到 {resolved}，钩子在位")
            else:
                report.ok = False
                report.problems.append(
                    f"core.hooksPath={hooks_path} 解析到 {resolved}，那里没有 pre-commit"
                    "（worktree 相对路径坑：钩子会静默不执行）"
                )
        else:
            report.ok = False
            report.problems.append(
                "core.hooksPath 未设置（先跑 node ops/deploy/setup-git-hooks.mjs）"
            )

        # 有 node 与校验脚本时，再跑一遍官方 --check 作为最终裁决
        setup = self.repo_root / "ops" / "deploy" / "setup-git-hooks.mjs"
        if shutil.which("node") and setup.is_file():
            proc = subprocess.run(
                ["node", str(setup), "--check"],
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                report.checks.append("setup-git-hooks.mjs --check 通过")
            else:
                report.ok = False
                report.problems.append(
                    "setup-git-hooks.mjs --check 未通过："
                    + (proc.stdout + proc.stderr).strip()[:300]
                )
        else:
            report.checks.append("无 node 或校验脚本，跳过 --check（已用上面的静态检查代替）")
        return report
