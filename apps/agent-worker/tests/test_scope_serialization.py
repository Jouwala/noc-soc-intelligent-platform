"""文件范围锁：重叠串行化、不重叠并行、完成后释放。"""

from __future__ import annotations

import pytest

from conftest import gi


def _setup(transport, clock):
    a = gi.GitHubIssueAdapter(transport, "muse-2", clock=clock)
    b = gi.GitHubIssueAdapter(transport, "muse-3", clock=clock)
    transport.create_issue(51, "任务甲", "", labels=("agent:muse-2", "state:todo"))
    transport.create_issue(52, "任务乙", "", labels=("agent:muse-3", "state:todo"))
    transport.create_issue(53, "任务丙", "", labels=("agent:muse-3", "state:todo"))
    return a, b


def test_overlapping_scopes_serialized(transport, clock):
    a, b = _setup(transport, clock)
    a.claim(51)
    a.acquire_scope(51, ["apps/my_app/backend/data.py"])

    b.claim(52)
    # 目录前缀与甲的文件重叠 → 必须串行化
    with pytest.raises(gi.ScopeConflict) as excinfo:
        b.acquire_scope(52, ["apps/my_app/backend"])
    assert excinfo.value.holder_issue == 51

    # 不重叠的范围可以并行拿锁
    b.claim(53)
    b.acquire_scope(53, ["apps/portal/src/App.tsx"])

    # 甲完成后锁自动失效，乙可以拿到重叠范围
    a.begin_attempt(51, "att-a")
    a.complete(51, "att-a", "完成")
    b.acquire_scope(52, ["apps/my_app/backend"])


def test_scope_parsing_and_overlap():
    body = (
        "## 目标\n做事。\n## 改动文件\n- 新建 `apps/agent-worker/worker.py`\n"
        "- 改 `apps/my_app/backend/db.py`（说明）\n## 验收标准\n- 过。\n"
    )
    assert gi.parse_file_scope(body) == [
        "apps/agent-worker/worker.py",
        "apps/my_app/backend/db.py",
    ]
    assert gi.scopes_overlap(["a/b.py"], ["a"]) == ["a/b.py"]
    assert gi.scopes_overlap(["a/b.py"], ["a/c.py"]) == []
