"""常驻 Worker 的自动任务书与 agent 命令模板（2026-11-24 流程提速批）。

覆盖三件纯逻辑：
- `render_task_prompt()`：Issue → coding agent 的任务书（含边界要求）；
- `build_agent_argv()`：模板占位符展开；
- `AgentWorker._eligible()`：只有显式 opt-in 标签的任务才会被常驻 Worker 认领。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from conftest import wk


def _issue(number=226, title="PRTG 映射表", body="## 目标\n补映射。\n", labels=("agent:muse-2",)):
    return SimpleNamespace(number=number, title=title, body=body, labels=labels)


def test_render_task_prompt_carries_issue_and_boundaries() -> None:
    prompt = wk.render_task_prompt(_issue())
    assert "# Issue #226" in prompt
    assert "PRTG 映射表" in prompt
    assert "agent:muse-2" in prompt
    assert "补映射。" in prompt
    # 边界要求必须在任务书里：不切分支、不推 main、不合并、缺凭据就停
    assert "不要合并 PR" in prompt
    assert "不要切分支" in prompt
    assert "VMAX" in prompt
    assert "AGENTS.md" in prompt


def test_render_task_prompt_handles_empty_body() -> None:
    prompt = wk.render_task_prompt(_issue(title="空任务", body="", labels=()))
    assert "（正文为空）" in prompt
    assert "（无）" in prompt


def test_build_agent_argv_expands_placeholders() -> None:
    argv = wk.build_agent_argv(
        ("claude", "-p", "读 {prompt_file} 完成 #{issue}", "--dir", "{worktree}"),
        226,
        Path("D:/wt/TMP/agent-task.md"),
        Path("D:/wt"),
        "agent/task-226",
    )
    assert argv[0] == "claude"
    assert argv[2].endswith("#226")
    assert str(Path("D:/wt/TMP/agent-task.md")) in argv[2]
    assert Path(argv[4]) == Path("D:/wt")


def test_build_agent_argv_leaves_unknown_placeholders() -> None:
    argv = wk.build_agent_argv(("tool", "{unknown}"), 1, Path("p"), Path("w"), "b")
    assert argv == ["tool", "{unknown}"]


def test_eligible_requires_all_opt_in_labels() -> None:
    worker = SimpleNamespace(config=SimpleNamespace(require_labels=("worker:auto",)))
    yes = _issue(number=1, labels=("agent:muse-2", "worker:auto"))
    no = _issue(number=2, labels=("agent:muse-2",))
    out = wk.AgentWorker._eligible(worker, [yes, no])
    assert [i.number for i in out] == [1]


def test_eligible_skips_risk_high_even_when_opted_in() -> None:
    """risk:high 无论如何不被常驻 Worker 认领（Muse 1 审核第 3 项阻塞）。"""
    worker = SimpleNamespace(
        config=SimpleNamespace(require_labels=("worker:auto",)), log=lambda *_: None
    )
    danger = _issue(number=9, labels=("agent:muse-2", "worker:auto", "risk:high"))
    safe = _issue(number=10, labels=("agent:muse-2", "worker:auto"))
    out = wk.AgentWorker._eligible(worker, [danger, safe])
    assert [i.number for i in out] == [10]


def test_eligible_without_requirement_keeps_everything() -> None:
    worker = SimpleNamespace(config=SimpleNamespace(require_labels=()))
    issues = [_issue(number=1), _issue(number=2)]
    assert wk.AgentWorker._eligible(worker, issues) == issues


def test_write_task_prompt_lives_under_tmp(tmp_path: Path) -> None:
    path = wk.write_task_prompt(tmp_path, _issue())
    assert path == tmp_path / "TMP" / "agent-task.md"
    assert "Issue #226" in path.read_text(encoding="utf-8")
