# -*- coding: utf-8 -*-
"""PRTG 通道语义字典（Issue #225）。

来源：2026-11-23 对 PRTG 14.2.10.2256（Cellcom PRTG / GRAPHER-ISP）
只读账号的实测（浏览器侧，见 ~/workspace/prtg-api-test-2026-11-23.md
与 docs/workspace/prtg-channel-dictionary.md），不是照搬新版文档。

适用范围：SNMP Traffic 64bit 传感器（25 个传感器中的 24 个）。
Ping 传感器（AML TOKADEH1 的 1 个）的通道集不同，不在本字典内，待补。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelInfo:
    """单个 PRTG 通道的语义。"""

    channel_id: int
    name_en: str
    name_zh: str
    # 值形态：rate+volume（速率+体积成对）/ interval（区间计数+速率成对）/ none（无数值）
    value_kind: str
    unit_display: str
    unit_raw: str
    notes: str = ""


# 通道顺序与 getsensordetails 详情页列出的顺序一致，也是 historicdata
# JSON 里每行 20 组 value/value_raw 的排列顺序（2026-11-23 实测）。
HISTORIC_CHANNEL_ORDER = (-1, 0, 1, 2, 3, 10, 11, 12, 13, 14)

CHANNELS = {
    -1: ChannelInfo(
        channel_id=-1,
        name_en="Traffic Total",
        name_zh="流量合计",
        value_kind="rate+volume",
        unit_display="kbit/s / KByte",
        unit_raw="byte/s / byte",
        notes="实测 Total = In + Out（3 个样本行显示值与原始值均相等，仅 1 行体积差 1 KByte 为取整）。"
        "利用率分母口径：仍按 max(In/Cin, Out/Cout)，不要拿 Total/容量（会翻倍）。",
    ),
    0: ChannelInfo(
        channel_id=0,
        name_en="Traffic In",
        name_zh="入向流量",
        value_kind="rate+volume",
        unit_display="kbit/s / KByte",
        unit_raw="byte/s / byte",
        notes="利用率分子取 In/Cin（与 Out 取 max）。",
    ),
    1: ChannelInfo(
        channel_id=1,
        name_en="Traffic Out",
        name_zh="出向流量",
        value_kind="rate+volume",
        unit_display="kbit/s / KByte",
        unit_raw="byte/s / byte",
        notes="利用率分子取 Out/Cout（与 In 取 max）。",
    ),
    2: ChannelInfo(
        channel_id=2,
        name_en="Unicast In",
        name_zh="单播入向",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="区间值：120 行 historicdata 内逐行波动、非单调递增，证明是每采样间隔的"
        "区间计数（#）+ 速率（#/s），不是累计计数器，不需要再差分。",
    ),
    3: ChannelInfo(
        channel_id=3,
        name_en="Unicast Out",
        name_zh="单播出向",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="同 Unicast In：区间值，不差分。",
    ),
    10: ChannelInfo(
        channel_id=10,
        name_en="Errors In",
        name_zh="入向错误",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="区间值，不差分。实测窗口（2026-11-23 09:00-11:00）全 0，劣化预警规则"
        "需换有误差的时段再验证。",
    ),
    11: ChannelInfo(
        channel_id=11,
        name_en="Errors Out",
        name_zh="出向错误",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="同 Errors In：区间值，不差分；实测窗口全 0。",
    ),
    12: ChannelInfo(
        channel_id=12,
        name_en="Discards In",
        name_zh="入向丢弃",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="同 Errors In：区间值，不差分；实测窗口全 0。",
    ),
    13: ChannelInfo(
        channel_id=13,
        name_en="Discards Out",
        name_zh="出向丢弃",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="同 Errors In：区间值，不差分；实测窗口全 0。",
    ),
    14: ChannelInfo(
        channel_id=14,
        name_en="Unknown Protocols In",
        name_zh="未知协议入向",
        value_kind="interval",
        unit_display="# / #/s",
        unit_raw="count / count-per-second",
        notes="区间值，不差分。",
    ),
    -4: ChannelInfo(
        channel_id=-4,
        name_en="Downtime",
        name_zh="停机时间",
        value_kind="none",
        unit_display="-",
        unit_raw="-",
        notes="详情页列出但无数值；停机时长以传感器 Uptime/Downtime 百分比与 Last Down 为准。",
    ),
}

# table.xml（content=sensors / devices）默认只回 5 列，且不含 objid；
# 按 id 定位必须用 columns 参数显式指定（2026-11-23 实测）。
TABLE_DEFAULT_COLUMNS = ("group", "device", "sensor", "status", "status_raw")

# status_raw 枚举（实测：Up=3；devices 表另见 Paused=7）。
STATUS_RAW = {3: "Up", 7: "Paused"}


def get_channel(channel_id: int) -> ChannelInfo:
    """按通道 ID 取语义；未知 ID 直接报错，不静默猜测。"""
    try:
        return CHANNELS[channel_id]
    except KeyError:
        raise ValueError(f"未知 PRTG 通道 ID：{channel_id}（字典只覆盖 SNMP Traffic 64bit 的 11 个通道）") from None


def is_interval_channel(channel_id: int) -> bool:
    """是否区间值通道（Errors/Discards/Unicast 类）：是则直接用速率，不差分。"""
    return get_channel(channel_id).value_kind == "interval"


def verify_traffic_total(in_value: float, out_value: float, total_value: float, tolerance: float = 1.0) -> bool:
    """校验 Total = In + Out。tolerance 默认 1（单位与入参一致）。

    实测：显示值与原始值均相等，仅 1 行体积差 1 KByte（取整），故默认容差 1。
    """
    return abs((in_value + out_value) - total_value) <= tolerance
