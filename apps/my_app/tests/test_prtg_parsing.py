# -*- coding: utf-8 -*-
"""PRTG 解析器测试（Issue #225）。

固件取自 2026-11-23 实测记录的真实观测值（传感器 2721）：
datetime_raw 46349.3753943056、lastvalue 速率 "66,123 kbit/s" /
raw 8265327.1757、体积 "484,135 KByte" / raw 495754324。
sensortree 固件是按实测结构（根 <ul class="sensortree">、嵌套 <li>）
构造的最小片段，用于验解析逻辑，不是 PRTG 原文转储。
不依赖真实 PRTG，不发网络请求。
"""
from datetime import datetime

from backend.services.prtg.parsing import (
    excel_serial_to_datetime,
    group_duplicate_keys,
    parse_historic_row_json,
    parse_historic_row_pairs,
    parse_lastvalue_pair,
    parse_sensortree_html,
    parse_us_datetime,
)


def test_excel_serial_matches_observed_row():
    # 46349.3753943056 ↔ 2026-11-23 09:00:34（historicdata 实测行）；
    # 序列日小数部分带毫秒（.068 秒），比到毫秒即可。
    got = excel_serial_to_datetime(46349.3753943056)
    assert abs((got - datetime(2026, 11, 23, 9, 0, 34)).total_seconds()) < 0.1


def test_us_datetime_format():
    assert parse_us_datetime("11/23/2026 9:00:34 AM") == datetime(2026, 11, 23, 9, 0, 34)


def test_duplicate_keys_are_grouped_not_overwritten():
    pairs = [("lastvalue", "66,123 kbit/s"), ("lastvalue_raw", 8265327.1757),
             ("lastvalue", "484,135 KByte"), ("lastvalue_raw", 495754324)]
    grouped = group_duplicate_keys(pairs)
    assert grouped["lastvalue"] == ["66,123 kbit/s", "484,135 KByte"]
    assert grouped["lastvalue_raw"] == [8265327.1757, 495754324]


def test_lastvalue_pair_rate_first_volume_second():
    pairs = [("lastvalue", "66,123 kbit/s"), ("lastvalue_raw", 8265327.1757),
             ("lastvalue", "484,135 KByte"), ("lastvalue_raw", 495754324)]
    parsed = parse_lastvalue_pair(pairs)
    assert parsed["rate"] == "66,123 kbit/s"
    assert parsed["rate_raw"] == 8265327.1757
    assert parsed["volume"] == "484,135 KByte"
    assert parsed["volume_raw"] == 495754324
    # 原始值自洽：byte/s × 8 / 1000 ≈ kbit/s；byte / 1024 ≈ KByte。
    assert abs(parsed["rate_raw"] * 8 / 1000 - 66123) < 1
    assert abs(parsed["volume_raw"] / 1024 - 484135) < 1


def test_lastvalue_pair_missing_volume_is_none_not_crash():
    parsed = parse_lastvalue_pair([("lastvalue", "66,123 kbit/s"), ("lastvalue_raw", 1.0)])
    assert parsed["volume"] is None
    assert parsed["volume_raw"] is None


def test_sensortree_html_fragment_parses_nested_list():
    html = (
        '<ul class="sensortree">'
        '<li><a href="/sensors.htm?id=1">Orange Liberia</a>'
        '<ul><li><a href="/sensors.htm?id=50">Core Switching</a>'
        '<ul><li><a href="/sensors.htm?id=2385">PTP_SW</a>'
        '<ul><li><a href="/sensors.htm?id=2721">2721 to CSQUARED</a></li></ul>'
        '</li></ul></li></ul></li></ul>'
    )
    nodes = parse_sensortree_html(html)
    assert [n["text"] for n in nodes] == [
        "Orange Liberia", "Core Switching", "PTP_SW", "2721 to CSQUARED",
    ]
    assert [n["depth"] for n in nodes] == [1, 2, 3, 4]
    assert nodes[-1]["objid"] == "2721"


def _historic_row_pairs():
    """构造一行 historicdata 键值对：datetime + 20 组 value/value_raw + coverage。"""
    pairs = [
        ("datetime", "11/23/2026 9:00:34 AM"),
        ("datetime_raw", 46349.3753943056),
    ]
    # 10 通道 ×（体积/计数 + 速率）：Total/ In/Out 取实测量级，其余取 0/波动值。
    values = [
        ("484,135 KByte", "66,123 kbit/s"),   # Total -1
        ("300,000 KByte", "40,000 kbit/s"),   # In 0
        ("184,135 KByte", "26,123 kbit/s"),   # Out 1
        ("100 #", "2 #/s"),                   # Unicast in 2
        ("90 #", "2 #/s"),                    # Unicast out 3
        ("0 #", "0 #/s"),                     # Errors in 10
        ("0 #", "0 #/s"),                     # Errors out 11
        ("0 #", "0 #/s"),                     # Discards in 12
        ("0 #", "0 #/s"),                     # Discards out 13
        ("5 #", "0 #/s"),                     # Unknown in 14
    ]
    raws = [
        (495754324, 8265327.1757),
        (307200000, 5000000.0),
        (188554324, 3265327.1757),
        (100, 2.0),
        (90, 2.0),
        (0, 0.0),
        (0, 0.0),
        (0, 0.0),
        (0, 0.0),
        (5, 0.0),
    ]
    for (v, r), (vr, rr) in zip(values, raws):
        pairs.append(("value", v))
        pairs.append(("value_raw", vr))
        pairs.append(("value", r))
        pairs.append(("value_raw", rr))
    pairs.append(("coverage", 100))
    return pairs


def test_historic_row_pairs_channel_mapping():
    row = parse_historic_row_pairs(_historic_row_pairs())
    assert row["datetime"] == datetime(2026, 11, 23, 9, 0, 34)
    assert abs((row["datetime_raw"] - datetime(2026, 11, 23, 9, 0, 34)).total_seconds()) < 0.1
    assert row["coverage"] == 100
    assert row["channels"][0]["value"] == "300,000 KByte"
    assert row["channels"][0]["rate_raw"] == 5000000.0
    assert row["channels"][10]["value_raw"] == 0  # Errors in：实测窗口全 0
    assert row["channels"][14]["value_raw"] == 5


def test_historic_row_wrong_pair_count_raises():
    pairs = [("datetime", "11/23/2026 9:00:34 AM"), ("value", "1"), ("value_raw", 1)]
    try:
        parse_historic_row_pairs(pairs)
    except ValueError as exc:
        assert "20" in str(exc)
    else:
        raise AssertionError("组数不对时应报错，不静默截断")


def test_historic_row_json_keeps_duplicate_keys():
    import json

    # 手工拼 JSON 文本保留重复键，验 object_pairs_hook 路径不断。
    text = json.dumps({"datetime": "11/23/2026 9:00:34 AM", "datetime_raw": 46349.3753943056})
    text = text[:-1] + "".join(
        f', "value": "v{i}", "value_raw": {i}, "value": "r{i}", "value_raw": {i}.5'
        for i in range(10)
    ) + ', "coverage": 100}'
    row = parse_historic_row_json(text)
    assert row["channels"][-1]["value"] == "v0"
    assert row["channels"][-1]["rate"] == "r0"
    assert row["channels"][14]["value_raw"] == 9
