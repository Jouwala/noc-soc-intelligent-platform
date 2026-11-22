# -*- coding: utf-8 -*-
"""International MOU 日报图表邮件的离线守卫（2026-11-22 新增任务时建立）。

这封邮件的核心承诺是「与 ?page=cn_kpi 的 International MOU 图表逐点同数」，
所以这里锁住三件事：

1. **SQL 来自图表组件**：`_widget_sql()` 必须取到 cn_kpi 的 c16，且仍以
   `substr("End Time"` 为日期键 —— 日期换算依赖这个口径，上游改键时服务要
   显式报错，而不是把整条曲线错标一天。
2. **日期换算与去当天**：End Time（区间右端点 = 次日 00:00）减一天还原成
   统计日；今天未过完的行必须剔除；带千分位的字符串必须转成数值。
3. **发信内容**：正文包含统计窗口、最近 7 天表和内嵌图片占位符，图片数量
   与行数进入返回值。

全程不联网、不发信、不写图：`query_remote` / `plot_series` / `_send_email`
都被替身接管。
"""
from pathlib import Path

import pandas as pd
import pytest

from backend.services import intl_mou_chart_service as svc
from backend.services.cn import cn_kpi_service as kpi


def _frame(n: int = 3) -> pd.DataFrame:
    days = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "Start Time": days,
        "Outgoing MOU": [100.0 + i for i in range(n)],
        "Incoming MOU": [200.0 + i for i in range(n)],
    })


def test_widget_sql_reuses_the_cn_kpi_chart():
    """必须复用 c16 的 SQL，且日期键仍是 End Time（减一天的前提）。"""
    sql = svc._widget_sql()
    assert 'FROM "International_MOU_OSS_EMS"' in sql
    assert 'substr("End Time"' in sql
    # 看板的占位符要已被替换成默认区间，不能让远端收到 {{LO}}
    assert "{{LO}}" not in sql and "{{HI}}" not in sql


def test_widget_sql_fails_loudly_when_component_disappears(monkeypatch):
    monkeypatch.setattr(kpi, "WIDGETS", [])
    with pytest.raises(RuntimeError, match="不存在"):
        svc._widget_sql()


def test_widget_sql_fails_loudly_when_date_key_changes(monkeypatch):
    fake = [{
        "id": "c16",
        "sql": 'SELECT substr("Start Time",1,10) AS t FROM "X" '
               "HAVING t >= {{LO}} AND t < {{HI}}",
    }]
    monkeypatch.setattr(kpi, "WIDGETS", fake)
    with pytest.raises(RuntimeError, match="End Time"):
        svc._widget_sql()


def test_fetch_shifts_end_time_back_to_stat_day(monkeypatch):
    payload = {
        "success": True,
        "columns": ["t", "Outgoing MOU", "Incoming MOU"],
        "rows": [
            ["2020-01-02", "1,234.50", 200.0],
            ["2020-01-03", 300, "400.25"],
        ],
    }
    monkeypatch.setattr(kpi, "query_remote", lambda *a, **k: payload)

    df = svc._fetch_mou_data()
    assert df is not None
    # End Time 2020-01-02 → 统计日 2020-01-01
    assert list(df["Start Time"].dt.strftime("%Y-%m-%d")) == ["2020-01-01", "2020-01-02"]
    assert df["Outgoing MOU"].tolist() == [1234.5, 300.0]
    assert df["Incoming MOU"].tolist() == [200.0, 400.25]


def test_fetch_drops_today_and_handles_empty(monkeypatch):
    today = pd.Timestamp.now().normalize()
    payload = {
        "success": True,
        "columns": ["t", "Outgoing MOU", "Incoming MOU"],
        "rows": [
            # End Time = 今天 → 统计日 = 昨天，仍属完整日；
            # End Time = 明天 → 统计日 = 今天，未过完，必须剔除。
            [today.strftime("%Y-%m-%d"), 10, 20],
            [(today + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), 99, 98],
        ],
    }
    monkeypatch.setattr(kpi, "query_remote", lambda *a, **k: payload)
    df = svc._fetch_mou_data()
    assert df is not None
    assert list(df["Start Time"].dt.strftime("%Y-%m-%d")) == [
        (today - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    ]

    monkeypatch.setattr(kpi, "query_remote",
                        lambda *a, **k: {"success": True, "columns": [], "rows": []})
    assert svc._fetch_mou_data() is None


def test_run_builds_body_without_sending(monkeypatch, tmp_path):
    df = _frame(9)
    sent = {}

    monkeypatch.setattr(svc, "OUTPUT_DIR", Path(tmp_path))
    monkeypatch.setattr(svc, "_fetch_mou_data", lambda: df)
    monkeypatch.setattr(svc, "plot_series",
                        lambda *a, **k: Path(tmp_path) / "fake.png")
    monkeypatch.setattr(svc, "cleanup_old_run_dirs", lambda *a, **k: None)

    from backend.services.email import email_recipient_store as store
    monkeypatch.setattr(store, "get_email_recipients",
                        lambda key, to=None, cc=None: ("a@example.com;b@example.com",
                                                       "c@example.com"))

    def fake_send(to, cc, body, images, data_dir):
        sent.update(to=to, cc=cc, body=body, images=images)
        return {"success": True}

    monkeypatch.setattr(svc, "_send_email", fake_send)

    out = svc.run_intl_mou_daily_email()
    assert out["success"] is True
    assert out["charts_generated"] == 2
    assert out["rows"] == len(df)
    assert sent["to"] == ["a@example.com", "b@example.com"]
    assert sent["cc"] == ["c@example.com"]
    assert "International MOU traffic" in sent["body"]
    # 最近 7 天表 + 内嵌图片插入点 + 签名顺序
    assert "<th>Outgoing MOU</th>" in sent["body"]
    assert sent["body"].index("<!-- inline images inserted here -->") < \
        sent["body"].index("Best regards")


def test_records_rejects_empty_data(monkeypatch):
    """没有数据时必须失败返回，不能发一封空邮件。"""
    monkeypatch.setattr(svc, "_fetch_mou_data", lambda: None)
    from backend.services.email import email_recipient_store as store
    monkeypatch.setattr(store, "get_email_recipients",
                        lambda key, to=None, cc=None: ("a@example.com", ""))
    out = svc.run_intl_mou_daily_email()
    assert out["success"] is False
    assert "无可用数据" in out["error"]
