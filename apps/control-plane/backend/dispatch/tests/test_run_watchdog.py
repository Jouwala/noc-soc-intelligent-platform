"""run_watchdog.py（卡死 CI 巡检）的判定与执行测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dispatch.run_watchdog import (
    MAX_ATTEMPTS,
    QUEUED_STUCK_AFTER,
    STUCK_AFTER,
    classify_run,
    run_cycle,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _run(status: str, *, started_minutes_ago: float | None = 5.0,
         conclusion: str | None = None, attempt: int = 1) -> dict:
    started = (
        None
        if started_minutes_ago is None
        else (NOW - timedelta(minutes=started_minutes_ago)).isoformat()
    )
    return {
        "id": 1,
        "status": status,
        "conclusion": conclusion,
        "run_started_at": started,
        "created_at": started,
        "run_attempt": attempt,
    }


def test_running_within_threshold_is_ok() -> None:
    action, why = classify_run(_run("in_progress", started_minutes_ago=5), 8, NOW)
    assert action == "ok"
    assert "运行中" in why


def test_running_too_long_is_cancelled() -> None:
    minutes = STUCK_AFTER.total_seconds() // 60 + 5
    action, why = classify_run(_run("in_progress", started_minutes_ago=minutes), 0, NOW)
    assert action == "cancel"
    assert "卡死" in why


def test_queued_longer_than_running_threshold_is_not_cancelled() -> None:
    """排队 40 分钟可能是正常等 runner，不能按 30 分钟的卡死阈值误杀。"""
    minutes = STUCK_AFTER.total_seconds() // 60 + 10
    action, _ = classify_run(_run("queued", started_minutes_ago=minutes), 0, NOW)
    assert action == "ok"


def test_queued_too_long_is_cancelled() -> None:
    minutes = QUEUED_STUCK_AFTER.total_seconds() // 60 + 1
    action, why = classify_run(_run("queued", started_minutes_ago=minutes), 0, NOW)
    assert action == "cancel"
    assert "卡死" in why


def test_unparseable_attempt_does_not_crash() -> None:
    """run_attempt 缺失/脏数据按第 1 次处理，不抛异常（与 docstring 一致）。"""
    run = _run("completed", conclusion="failure")
    run["run_attempt"] = "not-a-number"
    action, _ = classify_run(run, 0, NOW)
    assert action == "rerun"


def test_missing_attempt_is_treated_as_first() -> None:
    run = _run("completed", conclusion="failure")
    run.pop("run_attempt", None)
    action, _ = classify_run(run, 0, NOW)
    assert action == "rerun"


def test_unparseable_time_never_acts() -> None:
    action, why = classify_run(_run("in_progress", started_minutes_ago=None), 0, NOW)
    assert action == "ok"
    assert "不可解析" in why


def test_normal_failure_is_left_for_fix() -> None:
    action, why = classify_run(_run("completed", conclusion="failure"), 8, NOW)
    assert action == "ok"
    assert "留给代码修复" in why


def test_zero_job_failure_is_rerun() -> None:
    action, why = classify_run(_run("completed", conclusion="failure"), 0, NOW)
    assert action == "rerun"
    assert "自动重跑" in why


def test_zero_job_failure_stops_after_max_attempts() -> None:
    action, why = classify_run(
        _run("completed", conclusion="failure", attempt=MAX_ATTEMPTS), 0, NOW
    )
    assert action == "ok"
    assert "不再自动重跑" in why


def test_success_is_ok() -> None:
    action, _ = classify_run(_run("completed", conclusion="success"), 8, NOW)
    assert action == "ok"


class _FakeAPI:
    def __init__(self, runs_by_status):
        self.runs_by_status = runs_by_status
        self.cancelled: list[int] = []
        self.rerun_ids: list[int] = []
        self.fail_jobs_for: set[int] = set()

    def list_runs(self, status):
        return self.runs_by_status.get(status, [])

    def job_count(self, run_id):
        if run_id in self.fail_jobs_for:
            raise OSError("api down")
        return 0 if run_id == 2 else 8

    def cancel(self, run_id):
        self.cancelled.append(run_id)

    def rerun(self, run_id):
        self.rerun_ids.append(run_id)


def test_run_cycle_acts_only_on_anomalies() -> None:
    stuck = {
        "id": 1,
        "status": "in_progress",
        "run_started_at": (NOW - timedelta(minutes=90)).isoformat(),
        "created_at": (NOW - timedelta(minutes=90)).isoformat(),
        "run_attempt": 1,
    }
    zero_job = {
        "id": 2,
        "status": "completed",
        "conclusion": "failure",
        "run_started_at": (NOW - timedelta(minutes=2)).isoformat(),
        "created_at": (NOW - timedelta(minutes=2)).isoformat(),
        "run_attempt": 1,
    }
    healthy = {
        "id": 3,
        "status": "completed",
        "conclusion": "success",
        "run_started_at": (NOW - timedelta(minutes=3)).isoformat(),
        "created_at": (NOW - timedelta(minutes=3)).isoformat(),
        "run_attempt": 1,
    }
    api = _FakeAPI({"in_progress": [stuck], "queued": [], "completed": [zero_job, healthy]})
    done = run_cycle(api, NOW, log=lambda *_: None)
    assert sorted(done) == sorted([(1, "cancel"), (2, "rerun")])
    assert api.cancelled == [1]
    assert api.rerun_ids == [2]


def test_run_cycle_keeps_going_when_one_run_errors() -> None:
    zero_job = {
        "id": 2,
        "status": "completed",
        "conclusion": "failure",
        "run_started_at": (NOW - timedelta(minutes=2)).isoformat(),
        "created_at": (NOW - timedelta(minutes=2)).isoformat(),
        "run_attempt": 1,
    }
    api = _FakeAPI({"completed": [zero_job]})
    api.fail_jobs_for = {2}
    done = run_cycle(api, NOW, log=lambda *_: None)
    assert done == []
    assert api.rerun_ids == []


def test_run_cycle_respects_action_budget() -> None:
    stuck = [
        {
            "id": i,
            "status": "in_progress",
            "run_started_at": (NOW - timedelta(minutes=90)).isoformat(),
            "created_at": (NOW - timedelta(minutes=90)).isoformat(),
            "run_attempt": 1,
        }
        for i in range(1, 6)
    ]
    api = _FakeAPI({"in_progress": stuck})
    done = run_cycle(api, NOW, log=lambda *_: None, max_actions=2)
    assert len(done) == 2
    assert len(api.cancelled) == 2
