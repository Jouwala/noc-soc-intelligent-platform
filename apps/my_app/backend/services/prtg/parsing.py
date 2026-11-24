# -*- coding: utf-8 -*-
"""PRTG API 响应解析器（Issue #225）。纯函数，不发网络请求。

处理的实测坑（2026-11-23 对 PRTG 14.2.10.2256 实测，见
docs/workspace/prtg-channel-dictionary.md）：
  1. sensortree 返回的是 HTML 片段（根为 <ul class="sensortree">），不是 XML；
  2. lastvalue / historicdata 的 value 以重复键成对出现（速率 + 体积/计数），
     按普通 dict 解析会丢一半，必须按序处理；
  3. historicdata 的 datetime_raw 是 Excel 序列日；
  4. table.xml 默认只回 5 列且无 objid，按 id 定位须用 columns 参数。
"""
import json
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from backend.services.prtg.channels import HISTORIC_CHANNEL_ORDER, get_channel

# Excel 1900 日期系统：序列日 1 = 1900-01-01。1900-03-01 之后的日期用
# 1899-12-30 作 epoch 可直接换算（绕开 Excel 把 1900 当闰年的旧 bug）。
_EXCEL_EPOCH = datetime(1899, 12, 30)

# PRTG historicdata 的 datetime 显示格式（美式，服务器本地时间）。
US_DATETIME_FORMAT = "%m/%d/%Y %I:%M:%S %p"

def excel_serial_to_datetime(serial: float) -> datetime:
    """Excel 序列日 → datetime（naive，PRTG 服务器本地时间）。

    实测样本：46349.3753943056 ↔ 2026-11-23 09:00:34（historicdata 行）。
    调用方入库时须自行补时区，不得默认当 UTC。
    """
    return _EXCEL_EPOCH + timedelta(days=float(serial))


def parse_us_datetime(text: str) -> datetime:
    """解析 PRTG 的美式 datetime 显示串，如 "11/23/2026 9:00:34 AM"。"""
    return datetime.strptime(text.strip(), US_DATETIME_FORMAT)


def group_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, list[Any]]:
    """把 JSON 对象键值对序列按 key 分组，保留重复键的全部值与出现顺序。

    用法：json.loads(text, object_pairs_hook=...) 拿到 pairs 后调本函数。
    普通 json.loads 会把重复键覆盖成最后一个值，lastvalue 的速率/体积
    对就是这样丢一半的。
    """
    grouped: dict[str, list[Any]] = {}
    for key, value in pairs:
        grouped.setdefault(key, []).append(value)
    return grouped


def parse_lastvalue_pair(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """解析传感器行里成对出现的 lastvalue / lastvalue_raw。

    顺序约定（2026-11-23 传感器 2721 实测）：第一组是速率
    （"66,123 kbit/s"，raw=8265327.1757 byte/s），第二组是体积
    （"484,135 KByte"，raw=495754324 byte）。
    返回：rate / rate_raw / volume / volume_raw，缺失组为 None。
    """
    grouped = group_duplicate_keys(pairs)
    values = grouped.get("lastvalue", [])
    raws = grouped.get("lastvalue_raw", [])
    return {
        "rate": values[0] if len(values) > 0 else None,
        "rate_raw": raws[0] if len(raws) > 0 else None,
        "volume": values[1] if len(values) > 1 else None,
        "volume_raw": raws[1] if len(raws) > 1 else None,
    }


class _SensorTreeParser(HTMLParser):
    """解析 sensortree HTML 片段。

    只抽结构：每层 <li> 的文本、嵌套深度、链接 href 与 objid（href 里
    id=/objid= 参数，有则取，无则为 None）。不推断节点是组/设备/传感器，
    类型判定由调用方按 treesize 与 sensors/devices 表交叉核对。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.nodes: list[dict[str, Any]] = []
        # 栈帧：每层 <li> 各自攒文本，嵌套 <li> 打开时不得丢掉外层已攒的文本。
        self._stack: list[dict[str, Any]] = []
        self._text_stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "ul":
            self.depth += 1
        elif tag == "li":
            node = {
                "depth": self.depth,
                "text": "",
                "href": None,
                "objid": None,
                "children": [],
            }
            self.nodes.append(node)
            self._stack.append(node)
            self._text_stack.append([])
        elif tag == "a" and self._stack:
            href = attrs_dict.get("href")
            self._stack[-1]["href"] = href
            if href:
                for part in href.replace("?", "&").split("&"):
                    if part.startswith(("id=", "objid=")):
                        self._stack[-1]["objid"] = part.split("=", 1)[1]

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul":
            self.depth -= 1
        elif tag == "li" and self._stack:
            node = self._stack.pop()
            node["text"] = "".join(self._text_stack.pop()).strip()

    def handle_data(self, data: str) -> None:
        if self._text_stack:
            self._text_stack[-1].append(data)


def parse_sensortree_html(html: str) -> list[dict[str, Any]]:
    """解析 sensortree 返回的 HTML 片段，返回扁平节点清单（带 depth）。

    实测：一次返回完整树，全树 74 个对象（6 组、42 设备、25 传感器、1 探针），
    最深 6 层。注意：这不是 XML 文档，不得用 XML 解析器硬套。
    """
    parser = _SensorTreeParser()
    parser.feed(html)
    parser.close()
    return parser.nodes


def parse_historic_row_pairs(
    pairs: list[tuple[str, Any]],
    channel_order: tuple[int, ...] = HISTORIC_CHANNEL_ORDER,
) -> dict[str, Any]:
    """解析 historicdata 的一行（键值对序列，保留重复键）。

    每行结构（2026-11-23，avg=0，传感器 2721）：datetime（美式显示串）+
    datetime_raw（Excel 序列日）+ 20 组 value/value_raw（10 通道 ×
    体积/计数 + 速率，按 HISTORIC_CHANNEL_ORDER 排列）+ coverage。
    返回：{"datetime", "datetime_raw", "channels": {id: {"value", "value_raw"}},
    "coverage"}；value/value_raw 缺失组为 None。未知通道 ID 直接报错。
    """
    grouped = group_duplicate_keys(pairs)
    datetimes = grouped.get("datetime", [])
    raws = grouped.get("datetime_raw", [])
    values = grouped.get("value", [])
    value_raws = grouped.get("value_raw", [])
    if len(values) != 2 * len(channel_order) or len(value_raws) != 2 * len(channel_order):
        raise ValueError(
            f"historicdata 行 value/value_raw 组数应为 {2 * len(channel_order)}，"
            f"实得 value={len(values)} value_raw={len(value_raws)}"
        )
    channels: dict[int, dict[str, Any]] = {}
    for i, channel_id in enumerate(channel_order):
        get_channel(channel_id)  # 未知 ID 直接报错，不静默跳过
        channels[channel_id] = {
            "value": values[2 * i],
            "value_raw": value_raws[2 * i],
            "rate": values[2 * i + 1],
            "rate_raw": value_raws[2 * i + 1],
        }
    return {
        "datetime": parse_us_datetime(datetimes[0]) if datetimes else None,
        "datetime_raw": excel_serial_to_datetime(raws[0]) if raws else None,
        "channels": channels,
        "coverage": grouped.get("coverage", [None])[0],
    }


def parse_historic_row_json(text: str) -> dict[str, Any]:
    """解析 historicdata 单行 JSON 文本（保留重复键，见 group_duplicate_keys）。"""
    pairs = json.loads(text, object_pairs_hook=lambda p: p)
    if isinstance(pairs, dict):
        pairs = list(pairs.items())
    return parse_historic_row_pairs(pairs)


# ---------------- 采集服务用（Issue #227） ----------------

# historicdata.csv 的列名后缀：每个通道四列（体积、体积 RAW、速率、速率 RAW）。
_CSV_SUFFIX_VOLUME = " (volume)"
_CSV_SUFFIX_VOLUME_RAW = " (volume)(RAW)"
_CSV_SUFFIX_SPEED = " (speed)"
_CSV_SUFFIX_SPEED_RAW = " (speed)(RAW)"


def _parse_raw_number(text: str) -> float | None:
    """CSV 的 RAW 列是纯数字（如 "4631565.1667"）；空、"—"、带千分位都兼容。"""
    if text is None:
        return None
    cleaned = str(text).strip().replace(",", "")
    if cleaned in ("", "-", "—", "n/a"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_historic_csv(
    text: str, channel_order: tuple[int, ...] = HISTORIC_CHANNEL_ORDER
) -> list[dict[str, Any]]:
    """解析 historicdata.csv（44/45 列，实测 2026-11-24 为 44 列）。

    列结构：Date Time、Date Time(RAW)，随后每个通道依次
    `<name> (volume)`、`<name> (volume)(RAW)`、`<name> (speed)`、`<name> (speed)(RAW)`，
    最后 Coverage、Coverage(RAW)。通道名按 channels.CHANNELS 的 name_en 大小写不敏感匹配。

    返回：[{"datetime": naive UTC datetime, "channels": {cid: {
        "value_num": 速率原始值, "value_display": 速率显示值,
        "volume_num": 体积原始值, "volume_display": 体积显示值}}}]
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    try:
        header = next(reader)
    except StopIteration:
        return []
    index = {str(name).strip().lower(): i for i, name in enumerate(header)}

    def col(channel_id: int, suffix: str) -> int | None:
        name = f"{get_channel(channel_id).name_en}{suffix}".lower()
        return index.get(name)

    plan: list[tuple[int, int | None, int | None, int | None, int | None]] = []
    for channel_id in channel_order:
        plan.append((
            channel_id,
            col(channel_id, _CSV_SUFFIX_VOLUME),
            col(channel_id, _CSV_SUFFIX_VOLUME_RAW),
            col(channel_id, _CSV_SUFFIX_SPEED),
            col(channel_id, _CSV_SUFFIX_SPEED_RAW),
        ))

    dt_raw_idx = index.get("date time(raw)")
    dt_idx = index.get("date time")
    rows: list[dict[str, Any]] = []
    for cells in reader:
        if not cells:
            continue
        sample_time = None
        if dt_raw_idx is not None and dt_raw_idx < len(cells):
            serial = _parse_raw_number(cells[dt_raw_idx])
            if serial is not None:
                sample_time = excel_serial_to_datetime(serial)
        if sample_time is None and dt_idx is not None and dt_idx < len(cells) and cells[dt_idx].strip():
            sample_time = parse_us_datetime(cells[dt_idx])
        if sample_time is None:
            continue
        channels: dict[int, dict[str, Any]] = {}
        for channel_id, vol_i, vol_raw_i, speed_i, speed_raw_i in plan:
            if vol_raw_i is None or speed_raw_i is None:
                continue  # 该通道列不在本次响应里（版本差异），跳过不猜
            channels[channel_id] = {
                "value_num": _parse_raw_number(cells[speed_raw_i]) if speed_raw_i < len(cells) else None,
                "value_display": cells[speed_i].strip() if speed_i is not None and speed_i < len(cells) else None,
                "volume_num": _parse_raw_number(cells[vol_raw_i]) if vol_raw_i < len(cells) else None,
                "volume_display": cells[vol_i].strip() if vol_i is not None and vol_i < len(cells) else None,
            }
        rows.append({"datetime": sample_time, "channels": channels})
    return rows


def parse_channels_xml(text: str) -> list[dict[str, Any]]:
    """解析 /api/table.xml?content=channels&id=<sensor> 的通道清单。

    实测（2026-11-24）：每个 `<item>` 内 lastvalue / lastvalue_raw 各重复 3 次
    （速率、体积、体积），JSON 用普通 dict 解析会丢成 1 个。XML 的
    子元素顺序天然保留重复项，所以采集端用 XML 取通道值。

    返回：[{"channel_objid", "name", "value_num", "value_display",
            "volume_num", "volume_display", "unit"}]，无值通道（Downtime）跳过。
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    items = root.findall(".//item")
    result: list[dict[str, Any]] = []
    for item in items:
        objid_text = item.findtext("objid")
        name = item.findtext("name") or ""
        if objid_text is None:
            continue
        values = [el.text or "" for el in item.findall("lastvalue")]
        raws = [el.text or "" for el in item.findall("lastvalue_raw")]
        if not values or not values[0].strip():
            continue  # Downtime 等无值通道
        value_display = values[0].strip()
        value_num = _parse_raw_number(raws[0]) if raws else None
        volume_display = values[1].strip() if len(values) > 1 else None
        volume_num = _parse_raw_number(raws[1]) if len(raws) > 1 else None
        # 单位从显示值末尾切出；切不出就留空（不猜）
        unit = None
        if " " in value_display:
            unit = value_display.split()[-1]
        result.append({
            "channel_objid": int(objid_text),
            "name": name,
            "value_num": value_num,
            "value_display": value_display,
            "volume_num": volume_num,
            "volume_display": volume_display,
            "unit": unit,
        })
    return result
