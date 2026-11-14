"""命令解析离线自测：Issue 正文「执行命令」一节的解析（shlex 修 bug）。

- 带引号的参数（如 python3 -c "..."）不得被引号内的空格切开；
- 引号不成对时退回 str.split 保底，不抛异常；
- 没有执行命令一节时返回 None。
"""

from __future__ import annotations

from conftest import wk


def _body(command: str) -> str:
    return "## 目标\n写结果文件。\n## 执行命令\n```\n" + command + "\n```\n"


def test_quoted_arg_kept_whole() -> None:
    assert wk.AgentWorker._command_from_issue(
        _body('python3 -c "print(1 + 2)"')
    ) == ["python3", "-c", "print(1 + 2)"]


def test_plain_command() -> None:
    assert wk.AgentWorker._command_from_issue(
        _body("python3 scripts/check.py --fast")
    ) == ["python3", "scripts/check.py", "--fast"]


def test_unbalanced_quote_falls_back_to_split() -> None:
    assert wk.AgentWorker._command_from_issue(
        _body('python3 -c "print(1)')
    ) == ["python3", "-c", '"print(1)']


def test_no_command_section_returns_none() -> None:
    assert wk.AgentWorker._command_from_issue("## 目标\n写结果文件。\n") is None
    assert wk.AgentWorker._command_from_issue("") is None
