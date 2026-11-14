"""适配器全流程：注册 / 心跳 / 认领 / 租约 / 回传（离线，内存传输）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import gi


def _adapter(transport, clock, agent="muse-2", ttl=timedelta(hours=4), registry=None):
    return gi.GitHubIssueAdapter(
        transport, agent, clock=clock, lease_ttl=ttl, registry_issue=registry
    )


def _task(transport, number=42, agent="muse-2", body="## 改动文件\n- `a.py`\n"):
    return transport.create_issue(
        number, "测试任务", body, labels=(f"agent:{agent}", "state:todo", "phase:3")
    )


def test_full_lifecycle(transport, clock):
    transport.create_issue(1, "注册表", "", labels=())
    adapter = _adapter(transport, clock, registry=1)
    _task(transport)

    info = adapter.register(capabilities=("python",), vm="VM-TEST")
    assert info["agent"] == "muse-2"
    registry_comments = transport.list_comments(1)
    assert any("worker-registry: muse-2" in c.body for c in registry_comments)

    claimable = adapter.list_claimable()
    assert [i.number for i in claimable] == [42]

    lease = adapter.claim(42)
    assert lease.holder == "muse-2"
    labels = transport.get_issue(42).labels
    assert "state:claimed" in labels and "state:todo" not in labels

    headers = [gi.parse_header(c.body) for c in transport.list_comments(42)]
    assert headers[0]["type"] == "TASK_ACCEPTED"

    adapter.begin_attempt(42, "attempt-1")
    adapter.complete(42, "attempt-1", "执行完成，自检通过。")
    labels = transport.get_issue(42).labels
    assert "state:in-review" in labels and "state:claimed" not in labels
    assert adapter.lease_of(42) is None  # 完成即租约终止
    types = [gi.parse_header(c.body)["type"] for c in transport.list_comments(42)]
    assert types == ["TASK_ACCEPTED", "TASK_PROGRESS", "TASK_COMPLETED"]


def test_heartbeat_throttled(transport, clock):
    transport.create_issue(1, "注册表", "", labels=())
    adapter = _adapter(transport, clock, registry=1)
    adapter.register()
    assert adapter.heartbeat() is False  # 注册刚发过，心跳被节流
    clock.advance(601)
    assert adapter.heartbeat() is True
    bodies = [c.body for c in transport.list_comments(1)]
    assert any("worker-heartbeat: muse-2" in b for b in bodies)


def test_lease_conflict_then_expiry_reassign(transport, clock):
    ttl = timedelta(minutes=10)
    a = _adapter(transport, clock, agent="muse-2", ttl=ttl)
    b = _adapter(transport, clock, agent="muse-3", ttl=ttl)
    _task(transport, agent="muse-2")

    a.claim(42)
    with pytest.raises(gi.LeaseConflict):
        b.claim(42)
    clock.advance(601)  # 租约过期
    lease = b.claim(42)
    assert lease.holder == "muse-3"
    # 原持有者的回传必须被拒（防重复执行）
    with pytest.raises(gi.LeaseConflict):
        a.complete(42, "attempt-x", "迟到回传")


def test_stale_attempt_rejected(transport, clock):
    adapter = _adapter(transport, clock)
    _task(transport)
    adapter.claim(42)
    adapter.begin_attempt(42, "attempt-1")
    with pytest.raises(gi.StaleAttempt):
        adapter.complete(42, "attempt-OTHER", "错尝试回传")
