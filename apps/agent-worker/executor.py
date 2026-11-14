"""任务执行器（TASK-03）。

在任务的 Worktree 里执行命令（或注入的 callable，供离线自测），
并做两件守卫：
- 范围守卫：执行后用 git status 核对实际改动文件，超出任务声明范围的
  改动单独标出（out_of_scope），提交时只提交范围内文件——这是
  「两个并行任务零冲突提交」的落地手段之一（另一半是适配器范围锁）。
- 超时：命令超时被杀，返回 exit_code=None 并在 summary 里注明。
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

Command = Union[Sequence[str], Callable[[Path], int]]


@dataclass
class ExecutionResult:
    exit_code: Optional[int]
    duration_s: float
    changed_files: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    log_path: Optional[Path] = None
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.out_of_scope


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def _in_scope(path: str, scope: Sequence[str]) -> bool:
    np = _norm(path)
    for entry in scope:
        ne = _norm(entry)
        if np == ne or np.startswith(ne + "/"):
            return True
    return False


class Executor:
    def __init__(self, timeout_s: float = 1800.0) -> None:
        self.timeout_s = timeout_s

    # -- git 辅助 ---------------------------------------------------------------
    @staticmethod
    def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def changed_files(self, worktree: Path) -> list[str]:
        proc = self._git(worktree, "status", "--porcelain")
        files: list[str] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            # porcelain 格式：XY <path>，重命名是 old -> new，取 new
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path.strip('"'))
        return files

    # -- 执行 ---------------------------------------------------------------------
    def run(
        self,
        command: Command,
        worktree: Path,
        scope: Sequence[str] = (),
        timeout_s: Optional[float] = None,
        log_dir: Optional[Path] = None,
    ) -> ExecutionResult:
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        started = time.monotonic()
        exit_code: Optional[int]
        output = ""
        try:
            if callable(command):
                exit_code = int(command(worktree))
            else:
                proc = subprocess.run(
                    list(command),
                    cwd=str(worktree),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                exit_code = proc.returncode
                output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            output = f"执行超时（>{timeout}s）被终止：{exc}"
        duration = time.monotonic() - started

        log_path: Optional[Path] = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "execution.log"
            log_path.write_text(output, encoding="utf-8", errors="replace")

        changed = self.changed_files(worktree)
        if log_dir is not None:
            # 执行器自己的日志不算任务改动（真实仓库里 TMP/ 本就被 gitignore，
            # 这里再显式排除，保证未配 .gitignore 的仓库行为一致）
            try:
                log_prefix = _norm(str(log_dir.relative_to(worktree)))
                changed = [
                    f for f in changed if not (_norm(f) == log_prefix or _norm(f).startswith(log_prefix + "/"))
                ]
            except ValueError:
                pass
        out_of_scope = [f for f in changed if scope and not _in_scope(f, scope)]
        if exit_code is None:
            summary = f"执行超时（{duration:.1f}s）"
        elif exit_code == 0:
            summary = f"执行成功（{duration:.1f}s，改动 {len(changed)} 个文件）"
        else:
            summary = f"执行失败 exit={exit_code}（{duration:.1f}s）"
        if out_of_scope:
            summary += f"；超范围改动 {len(out_of_scope)} 个：{', '.join(out_of_scope)}"
        return ExecutionResult(
            exit_code=exit_code,
            duration_s=duration,
            changed_files=changed,
            out_of_scope=out_of_scope,
            log_path=log_path,
            summary=summary,
        )

    # -- 提交（只提交范围内文件） ---------------------------------------------------
    def commit_in_scope(
        self, worktree: Path, scope: Sequence[str], message: str
    ) -> Optional[str]:
        """把范围内改动提交，返回提交 SHA；无改动返回 None。超范围文件不进提交。"""
        changed = [f for f in self.changed_files(worktree) if _in_scope(f, scope)]
        if not changed:
            return None
        add = self._git(worktree, "add", "--", *changed)
        if add.returncode != 0:
            raise RuntimeError(f"git add 失败：{add.stderr.strip()}")
        commit = self._git(worktree, "commit", "-m", message)
        if commit.returncode != 0:
            raise RuntimeError(f"git commit 失败：{commit.stderr.strip()}")
        head = self._git(worktree, "rev-parse", "HEAD")
        return head.stdout.strip() or None
