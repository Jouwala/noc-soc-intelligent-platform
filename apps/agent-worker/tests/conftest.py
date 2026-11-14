"""离线自测的公共夹具：按文件路径加载被测模块 + 可控假时钟。"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _bootstrap  # noqa: E402

gi = _bootstrap.load_adapter()
wt = _bootstrap.load_sibling("worktree")
ex = _bootstrap.load_sibling("executor")
wk = _bootstrap.load_sibling("worker")

START = datetime(2026, 11, 14, 2, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def transport(clock):
    return gi.InMemoryGitHubTransport(clock=clock)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """一个带 .githooks 的临时 git 仓库（钩子已提交、hooksPath 已设置）。"""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    hooks = repo / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (repo / "README.md").write_text("test repo\n", encoding="utf-8")
    # 与真实仓库一致：TMP/ 是探针与执行日志的落点，被忽略不算改动
    (repo / ".gitignore").write_text("TMP/\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("config", "core.hooksPath", ".githooks")
    return repo
