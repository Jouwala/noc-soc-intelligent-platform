# -*- coding: utf-8 -*-
"""PRTG 日/周/月报与机器人数据域测试（Issue #232）。"""
from datetime import datetime

import pytest

from backend.services.prtg import reports


def test_daily_report_uses_dashboard_numbers(monkeypatch):
    overview = {
        "kpi": {"total": 25, "up": 17, "unusual": 8, "down": 0, "unknown": 0,
                "avg_uptime_pct": 99.14, "anomaly_links": 3},
        "data_age_seconds": 5,
    }
    monkeypatch.setattr(reports.dashboard, "overview", lambda w="24h": overview)
    monkeypatch.setattr(reports.dashboard, "anomalies", lambda w="24h", open_only=True: {
        "anomalies": [
            {"sensor_id": 1, "device_name": "d", "sensor_name": "s",
             "rule_code": "zero_traffic", "rule_label": "流量归零",
             "severity": "critical", "started_at": "2026-11-24T00:00:00Z"},
            {"sensor_id": 2, "device_name": "d2", "sensor_name": "s2",
             "rule_code": "baseline_low_pending_review", "rule_label": "基线偏低",
             "severity": "info", "started_at": "2026-11-24T00:00:00Z"},
        ]
    })
    monkeypatch.setattr(reports, "_peak_and_avg", lambda w="24h": (19800.0, 18500.0))
    monkeypatch.setattr(reports, "_capacity_summary", lambda: {
        "total": 25, "known": 0, "missing": 25, "attention": []
    })

    report = reports.daily_report(now=datetime(2026, 11, 24, 12, 0))
    assert report["kpi"] == overview["kpi"]  # 报表数字与看板同源
    assert report["traffic"] == {"peak_mbps": 19800.0, "avg_mbps": 18500.0}
    assert len(report["anomalies"]["items"]) == 1  # info 不进异常明细
    assert report["anomalies"]["items"][0]["rule_label"] == "流量归零"
    text = reports.render_markdown(report)
    assert "PRTG 日报" in text and "199" not in text.split("来源")[0]
    assert "来源：my_app /api/prtg" in text


def test_monthly_report_advice_mentions_missing_capacity(monkeypatch):
    monkeypatch.setattr(reports.sla, "month_report", lambda month=None, now=None: {
        "month": "2026-11", "target_pct": 99.5,
        "summary": {"avg_availability_pct": 99.1, "below_target": 4,
                    "total_downtime_hours_month": None},
        "links": [], "below_target": [], "capacity": [],
    })
    monkeypatch.setattr(reports, "_capacity_summary", lambda: {
        "total": 25, "known": 0, "missing": 25, "attention": []
    })
    monkeypatch.setattr(reports, "_anomaly_counts_since", lambda since: {"zero_traffic": 2})

    report = reports.monthly_report("2026-11", now=datetime(2026, 11, 24, 12, 0))
    assert report["kind"] == "monthly"
    assert report["anomalies"] == {"zero_traffic": 2}
    assert any("25 条链路未登记容量" in a for a in report["expansion_advice"])
    text = reports.render_markdown(report)
    assert "PRTG 月报" in text and "扩容建议" in text


def test_bot_data_prtg_endpoint_has_timestamp_and_source(monkeypatch):
    from backend.routers import bot_data

    monkeypatch.setattr(reports, "daily_report", lambda now=None: {
        "generated_at": "2026-11-24T12:00:00Z", "data_age_seconds": 3,
        "kpi": {"total": 25, "up": 17, "unusual": 8, "down": 0,
                "avg_uptime_pct": 99.14, "anomaly_links": 3},
        "traffic": {"peak_mbps": 19800.0, "avg_mbps": 18500.0},
        "anomalies": {"items": [{"severity": "warning", "rule_label": "超基线"}]},
        "capacity": {"total": 25, "known": 0, "missing": 25, "attention": []},
    })
    monkeypatch.setattr(reports, "monthly_report", lambda month=None, now=None: {
        "month": "2026-11", "sla": {"summary": {"avg_availability_pct": 99.1, "below_target": 4}},
    })

    payload = bot_data.get_prtg_status()
    assert payload["ok"] is True and payload["domain"] == "prtg"
    assert payload["generated_at"] and payload["data_source"]
    assert payload["kpi"]["total"] == 25


def _fake_sla_row(sensor_id, delta, coverage):
    from types import SimpleNamespace

    return SimpleNamespace(sensor_objid=sensor_id, downtime_delta_minutes=delta,
                           coverage_minutes=coverage)


def test_weekly_zero_availability_sorts_first(monkeypatch):
    links = [
        {"sensor_id": 1, "device_name": "d1", "sensor_name": "zero", "region": "R", "uptime_pct": 0.0},
        {"sensor_id": 2, "device_name": "d2", "sensor_name": "ok", "region": "R", "uptime_pct": 99.0},
    ]
    monkeypatch.setattr(reports.dashboard, "links", lambda w="24h": {"links": links})
    monkeypatch.setattr(reports, "_sla_rows", lambda start, end: [])
    monkeypatch.setattr(reports, "_anomaly_counts_since", lambda since: {})
    monkeypatch.setattr(reports, "_capacity_summary", lambda: {"total": 0, "known": 0, "missing": 0, "attention": []})

    report = reports.weekly_report()
    assert report["bottom5"][0]["sensor_id"] == 1  # 0% 不能因 `or 999` 被排到最后
    assert report["ranking"][0]["availability_pct"] == 0.0


def test_weekly_unknown_coverage_is_not_shown_as_100_percent(monkeypatch):
    links = [{"sensor_id": 2721, "device_name": "d", "sensor_name": "s", "region": "R", "uptime_pct": 99.9}]
    monkeypatch.setattr(reports.dashboard, "links", lambda w="24h": {"links": links})
    monkeypatch.setattr(reports, "_sla_rows", lambda start, end: [_fake_sla_row(2721, 60.0, None)])
    monkeypatch.setattr(reports, "_anomaly_counts_since", lambda since: {})
    monkeypatch.setattr(reports, "_capacity_summary", lambda: {"total": 0, "known": 0, "missing": 0, "attention": []})

    row = reports.weekly_report()["ranking"][0]
    assert row["availability_pct"] is None
    assert row["basis"] == "undefined_coverage"
    assert row["downtime_hours_week"] == pytest.approx(1.0)


def test_weekly_without_snapshot_is_labelled_fallback(monkeypatch):
    links = [{"sensor_id": 1, "device_name": "d", "sensor_name": "s", "region": "R", "uptime_pct": 98.5}]
    monkeypatch.setattr(reports.dashboard, "links", lambda w="24h": {"links": links})
    monkeypatch.setattr(reports, "_sla_rows", lambda start, end: [])
    monkeypatch.setattr(reports, "_anomaly_counts_since", lambda since: {})
    monkeypatch.setattr(reports, "_capacity_summary", lambda: {"total": 0, "known": 0, "missing": 0, "attention": []})

    row = reports.weekly_report()["ranking"][0]
    assert row["basis"] == "all_time_fallback"
    assert row["availability_pct"] == 98.5
