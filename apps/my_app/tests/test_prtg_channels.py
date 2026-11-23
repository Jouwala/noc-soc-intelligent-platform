# -*- coding: utf-8 -*-
"""PRTG 通道语义字典测试（Issue #225）。

全部断言以 2026-11-23 实测记录为准（传感器 2721，PTP_SW → CSQUARED），
不依赖真实 PRTG，不发网络请求。
"""
import pytest

from backend.services.prtg.channels import (
    CHANNELS,
    HISTORIC_CHANNEL_ORDER,
    STATUS_RAW,
    TABLE_DEFAULT_COLUMNS,
    get_channel,
    is_interval_channel,
    verify_traffic_total,
)


def test_all_eleven_channels_present():
    assert sorted(CHANNELS) == [-4, -1, 0, 1, 2, 3, 10, 11, 12, 13, 14]


def test_historic_channel_order_matches_detail_page():
    assert HISTORIC_CHANNEL_ORDER == (-1, 0, 1, 2, 3, 10, 11, 12, 13, 14)


def test_total_channel_semantics():
    total = get_channel(-1)
    assert total.name_en == "Traffic Total"
    assert total.value_kind == "rate+volume"


def test_interval_channels_need_no_delta():
    # Errors/Discards/Unicast 是区间值：直接用速率，不差分。
    for channel_id in (2, 3, 10, 11, 12, 13, 14):
        assert is_interval_channel(channel_id) is True
    for channel_id in (-1, 0, 1):
        assert is_interval_channel(channel_id) is False


def test_downtime_channel_has_no_value():
    assert get_channel(-4).value_kind == "none"


def test_unknown_channel_id_raises_not_silently_guessed():
    with pytest.raises(ValueError):
        get_channel(99)


def test_traffic_total_equals_in_plus_out():
    # 2721 实测行：速率 66,123 kbit/s（raw 8265327.1757 byte/s）；
    # 此处用取整后的千比特数验算 Total = In + Out 的口径。
    assert verify_traffic_total(40000.0, 26123.0, 66123.0, tolerance=1.0) is True
    assert verify_traffic_total(40000.0, 26123.0, 70000.0, tolerance=1.0) is False


def test_traffic_total_volume_rounding_tolerance():
    # 实测仅 1 行体积差 1 KByte（取整）：容差 1 通过，容差 0 不通过。
    assert verify_traffic_total(484134.0, 1.0, 484136.0, tolerance=1.0) is True
    assert verify_traffic_total(484134.0, 1.0, 484136.0, tolerance=0.0) is False


def test_table_default_columns_have_no_objid():
    assert TABLE_DEFAULT_COLUMNS == ("group", "device", "sensor", "status", "status_raw")
    assert "objid" not in TABLE_DEFAULT_COLUMNS


def test_status_raw_up_is_3():
    assert STATUS_RAW[3] == "Up"
    assert STATUS_RAW[7] == "Paused"
