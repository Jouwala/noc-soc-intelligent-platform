#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CMDB Master Site Database 同步工具（A/M/D 标记版）
====================================================

用 my_app\\db 下最新一份「ZTE OLR MS Master Site Database」的 Master Site Database sheet，
生成两份可直接人工核对、再导入系统的 Excel：

  modify/eflow_site_update.xlsx   42 列 + 最前面「修改」列，全量显示
  modify/master_site_db .xlsx     22 列 + 最前面「修改」列，全量显示

「修改」列：
  A = 新增（CMDB 有、基线无）            整行浅绿
  M = 修改（共有站且映射字段有差异）      「修改」格 + 变化的单元格橙色，单元格批注里写着原值
  D = 删除（基线有、CMDB 已无）           整行浅红 + 删除线（数据取自基线）
  空 = 无变化                             不着色
  （eflow 里非数字 Site ID 的虚拟站 HQ_Site / ODC_Site 等：保护，不标 D，不着色）

另外生成「可直接导入」的干净副本（不含「修改」列、不着色）：
  modify/import/eflow_site_import.xlsx     只含 A + M 行（D 行请在系统里手工删除，见报告）
  modify/import/master_site_db.xlsx        CMDB 全量
差异报告：modify/cmdb_sync_report.xlsx（汇总 / 变更明细 / 待删除站点）

基线
----
  eflow  : backup/Site.xlsx               （eflow 当前 Site 表导出，每次跑之前请先更新）
  master : backup/master_site_db.xlsx     （系统里当前 master_site_db 的导出；没有则不标记 A/M/D）

默认只生成文件，不碰数据库。要写 PG / sqlite 需显式加 --pg upsert / --cmdb update。

用法
----
    py build_cmdb_site_update.py --dry-run          # 预演
    py build_cmdb_site_update.py                    # 生成文件（全量 + 标记）
    py build_cmdb_site_update.py --scope delta      # 两份文件都只保留 A/M/D 行
    py build_cmdb_site_update.py --pg upsert --cmdb update --prune   # 同时写库
"""
import argparse
import csv
import difflib
import glob
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# ---- 着色 ----
def _fill(rgb):
    return PatternFill(fill_type="solid", fgColor=rgb)


FILL_HEADER = _fill("FF2E75B6")
FONT_HEADER = Font(color="FFFFFFFF", bold=True)
FILL_ADD = _fill("FFC6EFCE")     # A 新增：浅绿
FILL_MOD = _fill("FFFFC000")     # M 变化格：橙
FILL_DEL = _fill("FFFFC7CE")     # D 删除：浅红
FONT_DEL = Font(color="FF9C0006", strike=True)
FILL_DIFF = _fill("FFFFC7CE")    # 报告：旧值
FILL_SAME = _fill("FFC6EFCE")    # 报告：新值 / 保护
FLAG_COL = "修改"

# 根目录按本脚本位置反推（<my_app>/data/eflow/cmdb_site_update/build_*.py），
# 不再写死 D:\01_RPA\Task\my_app —— 旧写法在 02 生产树下会读旧目录的源表、
# 把产出写进旧目录 modify，而摘要落到新树（split-brain）。2026-11-21 修。
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_SRC_DIR = os.path.join(APP_ROOT, "db")
DEFAULT_BASE_DIR = os.path.join(APP_ROOT, "data", "eflow", "cmdb_site_update")
DEFAULT_TEMPLATE = os.path.join(DEFAULT_BASE_DIR, "backup", "Site.xlsx")
DEFAULT_MASTER_BASE = os.path.join(DEFAULT_BASE_DIR, "backup", "master_site_db.xlsx")
DEFAULT_OUT_DIR = os.path.join(DEFAULT_BASE_DIR, "modify")
DEFAULT_ENV = os.path.join(APP_ROOT, "backend", ".env")
DEFAULT_PERSON_DB = os.path.join(APP_ROOT, "data", "eflow", "eflow.sqlite3")
DEFAULT_CMDB_DB = os.path.join(APP_ROOT, "data", "eflow", "eflow.sqlite3")
PG_TABLE = "app.master_site_db"

LAT_MIN, LAT_MAX = 4.0, 9.0
LON_MIN, LON_MAX = -11.8, -7.0

VICTIMS_ALIASES = ("Downstream Sites Count", "Victims")

CMDB_TO_EFLOW = {
    "Site ID": ["Site ID"],
    "Site Name": ["Site Name"],
    "Region": ["Region"],
    "Province/County": ["Province", "Area"],
    "Latitude": ["Latitude"],
    "Longitude": ["Longitude"],
    "FME Name": ["Site Maintainer"],
    "TRM Type": ["TRM Type"],
    "Victims": ["__VICTIMS__"],
    "Network Type": ["Network Type"],
    "Vip Site": ["VipSite"],
    "Hub Site": ["HubSite"],
    "Site Caregory": ["Site Type"],
}

CMDB_TO_PG = {
    "Site ID": ("site_id", "int"),
    "Site Name": ("site_name", "text"),
    "GateWay": ("gateway", "text"),
    "Network Type": ("network_type", "text"),
    "Region": ("region", "text"),
    "Province/County": ("province_county", "text"),
    "FME Name": ("fme_name", "text"),
    "FME Contact 1": ("fme_contact_1", "text"),
    "FME Contact 2": ("fme_contact_2", "text"),
    "New SuperVisor": ("new_supervisor", "text"),
    "Latitude": ("latitude", "float"),
    "Longitude": ("longitude", "float"),
    "TRM Type": ("trm_type", "text"),
    "Victims": ("victims", "int"),
    "Victims List": ("victims_list", "text"),
    "Vip Site": ("vip_site", "text"),
    "Hub Site": ("hub_site", "text"),
    "Site Caregory": ("site_caregory", "text"),
    "MS or Project": ("ms_or_project", "text"),
    "On-air Time": ("on_air_time", "text"),
    "Comment for the change": ("comment_for_the_change", "text"),
}


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def norm_h(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


EXCEL_ERRORS = {
    "#N/A", "#N/A N/A", "#REF!", "#VALUE!", "#DIV/0!",
    "#NAME?", "#NULL!", "#NUM!", "#GETTING_DATA",
}


_INVISIBLE = {
    "\u00a0": " ",   # NBSP（本批源表 New SuperVisor 整列都是这个）
    "\u2007": " ",   # figure space
    "\u202f": " ",   # narrow nbsp
    "\u200b": "",    # zero width space
    "\ufeff": "",    # BOM
    "\u3000": " ",   # 全角空格
}


def s(v):
    """值转文本：去 Excel 错误值、归一化不可见字符、压缩连续空格。
    源表大量字段用 U+00A0 当空格，不归一化会让"只差一个隐藏字符"被判成变更。"""
    if v is None:
        return ""
    t = str(v)
    for k, rep in _INVISIBLE.items():
        if k in t:
            t = t.replace(k, rep)
    t = re.sub(r"[ \t]+", " ", t).strip()
    if t.upper() in EXCEL_ERRORS:
        return ""
    return t


def norm_id(v):
    """Site ID 归一化：1001.0 / ' 1001 ' -> '1001'；非数字（HQ_Site）原样返回"""
    t = s(v)
    if re.fullmatch(r"\d+(\.0+)?", t):
        return str(int(float(t)))
    return t


def id_sort_key(sid):
    return (0, int(sid), "") if sid.isdigit() else (1, 0, sid)


def to_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def yn(v):
    t = s(v).lower()
    if t in ("y", "yes", "true", "1"):
        return "Yes"
    if t in ("n", "no", "false", "0"):
        return "No"
    return s(v)


# SiteSubType / Site Information → Site Type 的对应关系（Site Type 为空时兜底用）
SUBTYPE_TO_TYPE = {"ERA": "ERA", "REGULAR": "NORMAL"}


def pick_site_type(row, fallback="NORMAL"):
    """Site Type 为空时取值：优先沿用本行 SiteSubType / Site Information（ERA / REGULAR），
    都对不上才用 fallback。避免把 ERA 站标成 NORMAL。"""
    for c in ("SiteSubType", "Site Information"):
        t = s(row.get(c)).upper()
        if t in SUBTYPE_TO_TYPE:
            return SUBTYPE_TO_TYPE[t]
    return s(fallback) or "NORMAL"


# ----------------------------------------------------------------------------
# 省份（County）拼写归一化
#   源表里有 Monsteraddo / Monsterado、Rivercess / River cess 等错拼，
#   直接照抄会把 eflow 的 County 写成错的，还会把基线原本正确的值判成"变更"。
# ----------------------------------------------------------------------------
PROVINCE_CANON = (
    "Bomi", "Bong", "Gbarpolu", "Grand Bassa", "Grand Cape Mount", "Grand Gedeh",
    "Grand Kru", "Lofa", "Margibi", "Maryland", "Montserrado", "Nimba",
    "River Cess", "River Gee", "Sinoe",
)

PROVINCE_ALIAS = {
    "monsteraddo": "Montserrado",
    "monsterado": "Montserrado",
    "monsterrado": "Montserrado",
    "monteserrado": "Montserrado",
    "montserado": "Montserrado",
    "monteserrrado": "Montserrado",
    "montserrattdo": "Montserrado",
    "rivercess": "River Cess",
    "river cess": "River Cess",
    "rivercress": "River Cess",
    "rivercess county": "River Cess",
    "grandcapemount": "Grand Cape Mount",
    "grand capemount": "Grand Cape Mount",
    "gbarpolo": "Gbarpolu",
    "gbarpole": "Gbarpolu",
    "nimba county": "Nimba",
    "bong county": "Bong",
    "margibi county": "Margibi",
    "lofa county": "Lofa",
    "sinoe county": "Sinoe",
    "maryland county": "Maryland",
    "bomi county": "Bomi",
}


def _prov_key(v):
    return re.sub(r"[^a-z]", "", s(v).lower())


_PROV_INDEX = {_prov_key(x): x for x in PROVINCE_CANON}


def norm_province(v):
    """省份拼写归一化。
    命中标准拼写（忽略大小写/空格）或别名表 -> 标准值；与标准值高度相似 -> 标准值；
    否则原样返回（不猜）。返回 (归一后值, 是否改动)。"""
    raw = s(v)
    if not raw:
        return raw, False
    k = _prov_key(raw)
    if k in _PROV_INDEX:
        return _PROV_INDEX[k], _PROV_INDEX[k] != raw
    low = raw.lower().strip()
    if low in PROVINCE_ALIAS:
        return PROVINCE_ALIAS[low], True
    if k in PROVINCE_ALIAS:
        return PROVINCE_ALIAS[k], True
    hit = difflib.get_close_matches(k, list(_PROV_INDEX), n=1, cutoff=0.86)
    if hit:
        return _PROV_INDEX[hit[0]], True
    return raw, False


def same_val(col, a, b):
    """坐标按数值比较，忽略 7.0208000000000004 vs 7.0208 这类精度噪音"""
    a, b = s(a), s(b)
    if a == b:
        return True
    if col in ("Latitude", "Longitude"):
        fa, fb = to_float(a), to_float(b)
        if fa is not None and fb is not None:
            return abs(fa - fb) < 1e-9
    return False


def split_ids(v):
    return [x.strip() for x in re.split(r"[;,]", s(v)) if x.strip()]


def file_locked(path):
    if not os.path.exists(path):
        return False
    try:
        with open(path, "a+b"):
            pass
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def cleanup_fallbacks(out_dir, name, keep=None):
    stem, ext = os.path.splitext(name)
    pat = re.compile(rf"^{re.escape(stem)}_\d{{8}}_\d{{6}}{re.escape(ext)}$")
    removed = []
    if not os.path.isdir(out_dir):
        return removed
    for f in os.listdir(out_dir):
        if pat.match(f) and f != keep:
            try:
                os.remove(os.path.join(out_dir, f))
                removed.append(f)
            except OSError:
                pass
    return removed


class OutputLocked(Exception):
    def __init__(self, path, fallback=None):
        self.path = path
        self.fallback = fallback
        super().__init__(path)


def save_workbook_safe(wb, path, allow_fallback=True):
    """先写临时文件再原子替换。

    目标文件被 WPS / 右侧预览面板打开是常态，不是异常，所以默认不再因占用而
    整批失败：
      1) 短重试（约 3 秒），覆盖「刚关闭预览、句柄延迟释放」；
      2) 仍占用则另存带时间戳的副本 —— 绝不覆盖被打开的文件，避免用户看到
         半新半旧的内容，也不去强杀别人的编辑器进程；
      3) 只有显式 --strict-lock 才抛 OutputLocked。
    返回 (实际写出的路径, 是否降级)。
    """
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    tmp = os.path.join(d, f".~{stem}.tmp{ext}")
    wb.save(tmp)
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return path, False
        except PermissionError:
            if attempt < 5:
                time.sleep(0.5)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    alt = os.path.join(d, f"{stem}_{ts}{ext}")
    if allow_fallback:
        try:
            os.replace(tmp, alt)
            return alt, True
        except OSError:
            pass
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise OutputLocked(path, alt)


# ----------------------------------------------------------------------------
# FME Name（人名）-> person.ObjectID
# ----------------------------------------------------------------------------
def norm_name(v):
    t = s(v).replace(",", " ")
    t = re.sub(r"[.\u00a0]", " ", t)
    return re.sub(r"\s+", " ", t).strip().upper()


def load_person_index(path):
    idx = {}
    if not path or not os.path.isfile(path):
        return idx
    import sqlite3
    try:
        cn = sqlite3.connect(path)
    except Exception:  # noqa: BLE001
        return idx
    try:
        cur = cn.cursor()
        cur.execute("SELECT ObjectID, Name FROM person")
        for oid, nm in cur.fetchall():
            k = norm_name(nm)
            oid = s(oid)
            if k and oid:
                idx.setdefault(k, oid)
    except Exception:  # noqa: BLE001
        return {}
    finally:
        cn.close()
    return idx


def resolve_fme(value, person_idx):
    """CMDB FME Name（多人 'A / B'）-> (ObjectID 串 ';' 连接, 未命中姓名列表)"""
    raw = s(value)
    if not raw or not person_idx:
        return "", ([raw] if raw else [])
    parts = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
    if not parts:
        return "", []
    oids, unmatched = [], []
    for part in parts:
        k = norm_name(part)
        oid = person_idx.get(k)
        if oid is None and len(k.split()) >= 2:
            toks = k.split()
            cands = set()
            for pk, poid in person_idx.items():
                pt = pk.split()
                if len(pt) >= 2 and pt[0] == toks[0] and pt[-1] == toks[-1]:
                    cands.add(poid)
            if len(cands) == 1:
                oid = cands.pop()
        if oid:
            if oid not in oids:
                oids.append(oid)
        else:
            unmatched.append(part)
    if unmatched:
        return "", unmatched
    return ";".join(oids), []


def merge_maintainer(old_val, new_oid):
    """
    合并负责人。返回新值；与旧值等价时原样返回旧值。
      - 首位相同 -> 保留旧值（含 MGS 后位）
      - 首位不同 -> 换首位，旧值中的 MGS 后位（含 'MG' 的 ID）保留
    """
    old_ids, new_ids = split_ids(old_val), split_ids(new_oid)
    if not new_ids:
        return s(old_val)
    if old_ids and old_ids[0].upper() == new_ids[0].upper():
        return s(old_val)
    tail = [x for x in old_ids[1:] if "MG" in x.upper() and x not in new_ids]
    return ";".join(new_ids + tail)


def fix_coord(lat_raw, lon_raw):
    """返回 (lat_str, lon_str, swapped)；写反时自动交换"""
    lat, lon = to_float(lat_raw), to_float(lon_raw)
    if lat is None and lon is None:
        return s(lat_raw), s(lon_raw), False
    if lat is not None and lon is not None:
        swapped = False
        if lat <= 0 or lon >= 0:
            lat, lon = lon, lat
            swapped = True
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX) and swapped:
            lat, lon = lon, lat
            swapped = False
        return str(lat), str(lon), swapped
    return (str(lat) if lat is not None else s(lat_raw),
            str(lon) if lon is not None else s(lon_raw), False)


# ----------------------------------------------------------------------------
# 源文件
# ----------------------------------------------------------------------------
def find_source(src_dir, explicit=None):
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit(f"[FATAL] 指定的源文件不存在: {explicit}")
        return explicit
    cands = []
    for p in glob.glob(os.path.join(src_dir, "*.xlsx")):
        base = os.path.basename(p)
        if base.startswith("~$"):
            continue
        if "zte olr ms master site database" in re.sub(r"\s+", " ", base.lower()):
            cands.append(p)
    if not cands:
        raise SystemExit(f"[FATAL] {src_dir} 下没有文件名含 'ZTE OLR MS Master Site Database' 的 .xlsx")
    cands.sort(key=os.path.getmtime, reverse=True)
    print("候选源文件（最新在前）:")
    for p in cands[:5]:
        print("   ", datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M"),
              os.path.basename(p))
    return cands[0]


def pick_sheet(wb):
    exact = [n for n in wb.sheetnames if norm_h(n).lower() == "master site database"]
    if exact:
        return exact[0]
    loose = [n for n in wb.sheetnames if "master site database" in norm_h(n).lower()]
    if loose:
        return loose[0]
    raise SystemExit(f"[FATAL] 找不到 'Master Site Database' sheet，现有: {wb.sheetnames}")


def read_cmdb(path, sheet=None):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sn = sheet or pick_sheet(wb)
        ws = wb[sn]
        header_row, headers = None, None
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=5, values_only=True), start=1):
            if any(norm_h(c).lower() == "site id" for c in row if c is not None):
                header_row = i
                headers = [norm_h(c) for c in row]
                break
        if header_row is None:
            raise SystemExit("[FATAL] 前 5 行内没找到 'Site ID' 表头")
        rows = [list(r) for r in ws.iter_rows(min_row=header_row + 1, values_only=True)]
        return headers, rows, sn, {"header_row": header_row}
    finally:
        wb.close()


def build_cmdb_rows(headers, raw_rows):
    """按归一化 Site ID 去重（保留首次出现），并把归一化后的 ID 写回行字典"""
    idx = {}
    for i, h in enumerate(headers):
        if h and h not in idx:          # 重复表头只取第一次出现
            idx[h] = i
    for n in ("Site ID", "Site Name"):
        if n not in idx:
            raise SystemExit(f"[FATAL] 源表缺少必需列: {n}；实际表头: {headers}")
    out, seen, dups, blank = [], set(), [], 0
    prov_fix = []
    for r in raw_rows:
        sid = norm_id(r[idx["Site ID"]]) if idx["Site ID"] < len(r) else ""
        if not sid:
            blank += 1
            continue
        if sid in seen:
            dups.append(sid)
            continue
        seen.add(sid)
        row = {h: (r[i] if i < len(r) else None) for h, i in idx.items()}
        row["Site ID"] = sid
        for k in ("Province/County", "Province", "County", "Area"):
            if k in row:
                before = s(row[k])
                after, changed = norm_province(before)
                if changed:
                    prov_fix.append((sid, before, after))
                    row[k] = after
        out.append(row)
    return out, {"raw": len(raw_rows), "kept": len(out), "dup_ids": dups,
                 "blank_rows": blank, "prov_fix": prov_fix}


# ----------------------------------------------------------------------------
# eflow 基线 / 目标行
# ----------------------------------------------------------------------------
def load_eflow_template(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    headers = [norm_h(c) for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    headers = [h for h in headers if h]
    existing = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        sid = norm_id(row[0])
        if sid and sid not in existing:
            existing[sid] = {headers[i]: (row[i] if i < len(row) else None) for i in range(len(headers))}
    sheet_name = wb.sheetnames[0]
    wb.close()
    return headers, existing, sheet_name


def resolve_victims_col(ef_headers):
    for a in VICTIMS_ALIASES:
        if a in ef_headers:
            return a
    return None


def build_eflow_target(cmdb_row, base_row, ef_headers, victims_col, fill="keep",
                       skip_cols=(), person_idx=None):
    """以基线行为底，按 CMDB 覆盖映射列；返回 (target, swapped, unmatched_fme)"""
    skip_cols = set(skip_cols or ())
    keep_mode = (fill == "keep") and (base_row is not None)
    unmatched = []
    if base_row is None:
        target = {h: "" for h in ef_headers}
    else:
        target = {h: s(base_row.get(h)) for h in ef_headers}

    def put(col, val):
        if col in target and s(val) != "":
            target[col] = s(val)

    for cmdb_col, ef_cols in CMDB_TO_EFLOW.items():
        val = cmdb_row.get(cmdb_col)
        for c in ef_cols:
            col = victims_col if c == "__VICTIMS__" else c
            if not col or col not in ef_headers or col in skip_cols:
                continue
            if c == "Site Maintainer":
                if s(val) == "":
                    continue
                oid, miss = resolve_fme(val, person_idx)
                if oid:
                    put(col, merge_maintainer(target.get(col), oid))
                else:
                    unmatched.extend(miss)
                    if base_row is None:      # 新增站没有原值，退而写人名；已有站保留原员工号
                        put(col, val)
                continue
            if keep_mode and (col == "Region" or col == victims_col):
                continue
            if c in ("VipSite", "HubSite"):
                put(col, yn(val))
            elif c in ("Latitude", "Longitude"):
                continue
            else:
                put(col, val)

    lat_s, lon_s, swapped = fix_coord(cmdb_row.get("Latitude"), cmdb_row.get("Longitude"))
    if "Latitude" in target and lat_s != "":
        target["Latitude"] = lat_s
    if "Longitude" in target and lon_s != "":
        target["Longitude"] = lon_s
    return target, swapped, unmatched


def find_inherit_base(sid, name, ef_existing):
    """新站（基线无此 ID）找「同一物理站」的旧基线行，用来继承 eflow 专有字段
    （Site Maintainer / Site Status / Site Priority / Depot 等 CMDB 里没有的列）。

    1) 旧 ID = 新 ID - 1000 且站名一致 —— 源表整批重编号，eflow 侧表现为「删旧增新」，
       但物理站没变，属性必须带过来，否则新行维护人/状态全空。
    2) 站名一致的唯一基线行 —— 源表 ID 打错（如 4309 Beverly Hill vs 基线 4308）。

    返回 (基线行, 依据说明)；找不到返回 (None, "")。
    """
    nm = s(name).lower()
    if sid.isdigit() and int(sid) - 1000 > 0:
        old = str(int(sid) - 1000)
        b = ef_existing.get(old)
        if b is not None and s(b.get("Site Name")).lower() == nm:
            return b, f"重编号 {old}"
    if nm:
        hits = [(k, v) for k, v in ef_existing.items()
                if s(v.get("Site Name")).lower() == nm]
        if len(hits) == 1:
            return hits[0][1], f"同名 {hits[0][0]}"
    return None, ""


# CMDB 里没有、但 eflow 每条真实站都有值的列 —— 新站按现有口径补默认值
NEW_SITE_DEFAULTS = {
    "Status": "Active",
    "Site Status": "Operation",
    "Organization": "ZTE",
    "RAN Site": "Yes",
    "Transmision Site": "Yes",
}


def apply_new_site_defaults(target, ef_headers, priority="P4"):
    """只补空值，不覆盖任何已有值。返回补过的列名列表。"""
    filled = []
    for col, val in NEW_SITE_DEFAULTS.items():
        if col in ef_headers and not s(target.get(col)):
            target[col] = val
            filled.append(col)
    # 基线口径：SiteSubType / Site Information 与 Site Type 同源（ERA 站 -> ERA，其余 REGULAR）
    if "SiteSubType" in ef_headers and not s(target.get("SiteSubType")):
        target["SiteSubType"] = "ERA" if s(target.get("Site Type")).upper() == "ERA" else "REGULAR"
        filled.append("SiteSubType")
    if "Site Information" in ef_headers and not s(target.get("Site Information")):
        target["Site Information"] = s(target.get("SiteSubType")) or "REGULAR"
        filled.append("Site Information")
    for col in ("Site Priority", "Site Class"):
        if col in ef_headers and not s(target.get(col)):
            target[col] = priority
            filled.append(col)
    return filled


def classify_eflow(cmdb_rows, ef_existing, ef_headers, victims_col, fill, skip_cols, person_idx,
                   new_defaults=True, new_priority="P4", fill_blank_status=True,
                   virtual_maintainer="FE_ENG_101", site_type_default="NORMAL"):
    """返回 items=[(sid, flag, row, {变化列: 旧值})]、统计字典"""
    items, detail = [], []
    swapped_sites, fme_unmatched = [], set()
    inherited, defaulted, status_filled, no_maintainer = [], [], [], []
    virtual_filled = []
    notes = {}
    cmdb_ids = set()
    for r in cmdb_rows:
        sid = r["Site ID"]
        cmdb_ids.add(sid)
        base = ef_existing.get(sid)
        inherit_row, inherit_why = (None, "")
        if base is None:
            inherit_row, inherit_why = find_inherit_base(sid, r.get("Site Name"), ef_existing)
        target, swapped, miss = build_eflow_target(
            r, base if base is not None else inherit_row,
            ef_headers, victims_col, fill, skip_cols, person_idx)
        if swapped:
            swapped_sites.append(sid)
        fme_unmatched.update(miss)
        if base is None:
            if inherit_row is not None:
                inherited.append((sid, s(r.get("Site Name")), inherit_why))
                notes.setdefault(sid, {})["Site Maintainer"] = (
                    f"新增站：维护人等 eflow 专有字段继承自旧站点（{inherit_why}）\n"
                    f"源表 FME Name = {s(r.get('FME Name')) or '空 / #N/A'}，请核对维护人")
            if new_defaults:
                got = apply_new_site_defaults(target, ef_headers, new_priority)
                if got:
                    defaulted.append((sid, s(r.get("Site Name")), got))
                    for col in got:
                        notes.setdefault(sid, {})[col] = (
                            f"新站默认值（CMDB 无此字段），请确认：{s(target.get(col))}")
            if not s(target.get("Site Maintainer")):
                no_maintainer.append((sid, s(r.get("Site Name"))))
            items.append((sid, "A", target, {}))
            continue
        # 存量站：Site Status 空的补 Operation（基线 808/830 都是 Operation）
        if (fill_blank_status and "Site Status" in ef_headers
                and "Site Status" not in skip_cols and not s(target.get("Site Status"))):
            target["Site Status"] = "Operation"
            status_filled.append(sid)
            notes.setdefault(sid, {})["Site Status"] = "基线为空，按 eflow 现有口径补为 Operation，请确认"
        changes = {}
        for c in ef_headers:
            if c in skip_cols:
                continue
            a, b = s(base.get(c)), s(target.get(c))
            if not same_val(c, a, b):
                changes[c] = a
                detail.append((sid, s(r.get("Site Name")), c, a, b))
        items.append((sid, "M" if changes else "", target, changes))

    removed, protected = [], []
    for sid, base in ef_existing.items():
        if sid in cmdb_ids:
            continue
        row = {h: s(base.get(h)) for h in ef_headers}
        if sid.isdigit():
            # 删除记录：值仅作留档，不影响删除动作。Site Type 为空的按统一口径补，
            # 以免整份文件在 其他要求.txt 第 6 条上出现空值
            if (site_type_default and "Site Type" in ef_headers
                    and "Site Type" not in skip_cols and not s(row.get("Site Type"))):
                st = pick_site_type(row, site_type_default)
                notes.setdefault(sid, {})["Site Type"] = (
                    f"待删除站，基线 Site Type 为空，按 SiteSubType 记为 {st}"
                    "（仅留档，不影响删除动作）")
                row["Site Type"] = st
            items.append((sid, "D", row, {}))
            removed.append((sid, s(base.get("Site Name")), s(row.get("Site Type"))))
        else:
            # 虚拟站：保护不删。按 其他要求.txt 补齐必填列（维护人 / Site Status / Site Type）
            changes = {}
            if (virtual_maintainer and "Site Maintainer" in ef_headers
                    and "Site Maintainer" not in skip_cols
                    and not s(row.get("Site Maintainer"))):
                changes["Site Maintainer"] = ""
                row["Site Maintainer"] = virtual_maintainer
                notes.setdefault(sid, {})["Site Maintainer"] = (
                    f"虚拟站（非物理站），维护人按参数统一指定为 {virtual_maintainer}，请确认")
            if (fill_blank_status and "Site Status" in ef_headers
                    and "Site Status" not in skip_cols and not s(row.get("Site Status"))):
                changes["Site Status"] = ""
                row["Site Status"] = "Operation"
                notes.setdefault(sid, {})["Site Status"] = (
                    "虚拟站，基线 Site Status 为空，按现有口径补 Operation，请确认")
            if (site_type_default and "Site Type" in ef_headers
                    and "Site Type" not in skip_cols and not s(row.get("Site Type"))):
                st = pick_site_type(row, site_type_default)
                changes["Site Type"] = ""
                row["Site Type"] = st
                notes.setdefault(sid, {})["Site Type"] = (
                    f"虚拟站，基线 Site Type 为空，按统一口径记为 {st}，请确认")
            items.append((sid, "M" if changes else "", row, changes))   # 保护，不标 D
            if changes:
                virtual_filled.append((sid, sorted(changes)))
            protected.append((sid, s(base.get("Site Name"))))
    items.sort(key=lambda x: id_sort_key(x[0]))
    removed.sort(key=lambda x: int(x[0]))
    protected.sort()
    detail.sort(key=lambda x: (id_sort_key(x[0]), x[2]))
    return items, {"detail": detail, "removed": removed, "protected": protected,
                   "swapped": swapped_sites, "fme_unmatched": fme_unmatched,
                   "inherited": inherited, "defaulted": defaulted,
                   "status_filled": status_filled, "no_maintainer": no_maintainer,
                   "virtual_filled": virtual_filled,
                   "notes": notes}


# ----------------------------------------------------------------------------
# master_site_db 基线 / 差异
# ----------------------------------------------------------------------------
PG_TO_CMDB = {pg: cm for cm, (pg, _t) in CMDB_TO_PG.items()}


def _master_from_iter(hdr, body):
    """把表头统一成 CMDB 表头后逐行收进 dict（兼容 CMDB 原表头与 PG 列名）"""
    cols = []
    for h in hdr:
        h = norm_h(h)
        if h in CMDB_TO_PG:                 # 已是 CMDB 表头
            cols.append(h)
        elif h.lower() in PG_TO_CMDB:       # PG 列名 site_id / site_name ...
            cols.append(PG_TO_CMDB[h.lower()])
        else:
            cols.append(h)
    if "Site ID" not in cols:
        return None
    sid_i = cols.index("Site ID")
    flag_i = cols.index(FLAG_COL) if FLAG_COL in cols else None
    out = {}
    for row in body:
        if not row or sid_i >= len(row) or s(row[sid_i]) == "":
            continue
        if flag_i is not None and flag_i < len(row) and s(row[flag_i]) == "D":
            continue
        sid = norm_id(row[sid_i])
        if sid not in out:
            out[sid] = {h: s(row[i]) for i, h in enumerate(cols) if h and i < len(row)}
    return out


def load_master_base(path):
    """
    读取 master_site_db 基线，跳过上一份输出里标 D 的行。失败返回 None。

    支持三种来源，表头自动识别：
      * 本工具上一版输出的 master_site_db .xlsx（含「修改」列）
      * CMDB 表头的导出（Site ID / Site Name ...）
      * 从 PG 直接导出的 csv / xlsx（site_id / site_name ...）
    """
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv", ".txt"):
        delim = "\t" if ext == ".tsv" else ","
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f, delimiter=delim))
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        return _master_from_iter(rows[0], rows[1:])
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        hdr = next(it, [])
        return _master_from_iter(hdr, it)
    finally:
        wb.close()


def classify_master(cmdb_rows, headers, base, region_mode="keep", empty_keep=True):
    """
    base         : master_site_db 基线；None 时全部不标记
    region_mode  : 'keep'=Region 保留基线原值（CMDB 是 North/South 大区，库/eflow 用县名）；
                   'cmdb'=按 CMDB 覆盖
    empty_keep   : CMDB 该列为空而基线有值时，保留基线原值（避免把备注、victims 洗成空）
    """
    keep = [h for h in headers if h]
    items = []
    cmdb_ids = set()
    protected = Counter()
    for r in cmdb_rows:
        sid = r["Site ID"]
        cmdb_ids.add(sid)
        row = {h: s(r.get(h)) for h in keep}
        if base is None:
            items.append((sid, "", row, {}))
            continue
        b = base.get(sid)
        if b is None:
            items.append((sid, "A", row, {}))
            continue
        for h in keep:
            if h not in b:
                continue
            if region_mode == "keep" and h == "Region" and b[h] != row[h]:
                row[h] = b[h]
                protected["Region"] += 1
            elif empty_keep and row[h] == "" and b[h] != "":
                row[h] = b[h]
                protected[h] += 1
        changes = {h: b[h] for h in keep if h in b and not same_val(h, b[h], row[h])}
        items.append((sid, "M" if changes else "", row, changes))
    if base is not None:
        for sid, b in base.items():
            if sid not in cmdb_ids:
                items.append((sid, "D", {h: b.get(h, "") for h in keep}, {}))
    items.sort(key=lambda x: id_sort_key(x[0]))
    return keep, items, protected


# ----------------------------------------------------------------------------
# 写 Excel
# ----------------------------------------------------------------------------
LEGEND = [
    ("A", "新增：CMDB 有、基线无", "整行浅绿"),
    ("M", "修改：共有站且字段有差异", "「修改」格 + 变化格橙色，鼠标悬停批注为原值"),
    ("D", "删除：基线有、CMDB 已无（数据取自基线）", "整行浅红 + 删除线"),
    ("空", "无变化", "不着色"),
]


def write_flagged_xlsx(path, headers, items, sheet_name, allow_fallback=True,
                       with_flag=True, colorize=True, base_note="", notes=None):
    """items: [(sid, flag, row_dict, {变化列: 旧值})]；返回 (实际路径, 是否降级, 统计)
    notes: {sid: {列名: 批注文字}}，用于给新增行的「继承值 / 默认值」加说明"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    hdr = ([FLAG_COL] if with_flag else []) + list(headers)
    ws.append(hdr)
    if colorize:
        for c in ws[1]:
            c.fill = FILL_HEADER
            c.font = FONT_HEADER
            c.alignment = Alignment(horizontal="center", vertical="center")
    off = 1 if with_flag else 0
    stats = Counter()
    notes = notes or {}
    for _sid, flag, row, changes in items:
        ws.append(([flag] if with_flag else []) + [row.get(h, "") for h in headers])
        stats[flag or "same"] += 1
        row_notes = notes.get(_sid) or {}
        if row_notes:
            for j, h in enumerate(headers, start=1 + off):
                if h in row_notes:
                    ws.cell(row=ws.max_row, column=j).comment = Comment(row_notes[h], "sync")
        if not (colorize and flag):
            continue
        ri = ws.max_row
        if flag == "A":
            for j in range(1, len(hdr) + 1):
                ws.cell(row=ri, column=j).fill = FILL_ADD
        elif flag == "D":
            for j in range(1, len(hdr) + 1):
                cell = ws.cell(row=ri, column=j)
                cell.fill = FILL_DEL
                cell.font = FONT_DEL
        elif flag == "M":
            if with_flag:
                ws.cell(row=ri, column=1).fill = FILL_MOD
            for j, h in enumerate(headers, start=1 + off):
                if h in changes:
                    cell = ws.cell(row=ri, column=j)
                    cell.fill = FILL_MOD
                    txt = f"原值: {changes[h] or '(空)'}"
                    prev = cell.comment.text if cell.comment else ""
                    cell.comment = Comment(f"{prev}\n{txt}" if prev else txt, "sync")
        if with_flag:
            fc = ws.cell(row=ri, column=1)
            fc.font = Font(bold=True, strike=False,
                           color=FONT_DEL.color if flag == "D" else None)
            fc.alignment = Alignment(horizontal="center")
    if colorize:
        ws.freeze_panes = "C2" if with_flag else "B2"
        ws.auto_filter.ref = ws.dimensions
        for j, h in enumerate(hdr, start=1):
            ws.column_dimensions[get_column_letter(j)].width = min(max(10, len(str(h)) + 2), 26)
        if with_flag:
            ws.column_dimensions["A"].width = 8
            lg = wb.create_sheet("说明")
            lg.append(["标记", "含义", "颜色", "行数"])
            for c in lg[1]:
                c.fill = FILL_HEADER
                c.font = FONT_HEADER
            cnt = {"A": stats["A"], "M": stats["M"], "D": stats["D"], "空": stats["same"]}
            for k, meaning, color in LEGEND:
                lg.append([k, meaning, color, cnt[k]])
            if base_note:
                lg.append([])
                lg.append(["基线", base_note])
            for col, w in zip("ABCD", (8, 40, 44, 10)):
                lg.column_dimensions[col].width = w
    try:
        actual, degraded = save_workbook_safe(wb, path, allow_fallback)
    finally:
        wb.close()
    return actual, degraded, stats


def write_delta_report(path, summary, detail_rows, removed_rows, protected_rows, allow_fallback=True,
                       inherit_rows=None, default_rows=None):
    wb = openpyxl.Workbook()

    def head(ws, labels):
        ws.append(labels)
        for c in ws[1]:
            c.fill = FILL_HEADER
            c.font = FONT_HEADER
            c.alignment = Alignment(horizontal="center", vertical="center")

    ws = wb.active
    ws.title = "汇总"
    head(ws, ["项目", "值"])
    for k, v in summary:
        ws.append([k, v])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 70

    ws2 = wb.create_sheet("变更明细")
    head(ws2, ["Site ID", "Site Name", "变更字段", "eflow 原值", "CMDB 新值"])
    for r in detail_rows:
        ws2.append(list(r))
        i = ws2.max_row
        ws2.cell(row=i, column=4).fill = FILL_DIFF
        ws2.cell(row=i, column=5).fill = FILL_SAME
    for c, w in zip("ABCDE", (12, 34, 26, 40, 40)):
        ws2.column_dimensions[c].width = w
    ws2.freeze_panes = "A2"

    ws3 = wb.create_sheet("待删除站点")
    head(ws3, ["Site ID", "Site Name", "Site Type", "说明"])
    for sid, nm, st in removed_rows:
        ws3.append([sid, nm, st, "CMDB 中已不存在，按 CMDB 为准应删除（D）"])
        for j in range(1, 5):
            ws3.cell(row=ws3.max_row, column=j).fill = FILL_DEL
    for sid, nm in protected_rows:
        ws3.append([sid, nm, "", "非数字 ID 虚拟站，保护跳过，不删除"])
        for j in range(1, 5):
            ws3.cell(row=ws3.max_row, column=j).fill = FILL_SAME
    for c, w in zip("ABCD", (14, 34, 14, 50)):
        ws3.column_dimensions[c].width = w
    ws3.freeze_panes = "A2"

    if inherit_rows or default_rows:
        ws4 = wb.create_sheet("新站补全说明")
        head(ws4, ["Site ID", "Site Name", "处理方式", "说明"])
        for sid, nm, why in (inherit_rows or []):
            ws4.append([sid, nm, "继承旧站点属性",
                        f"维护人/Site Status/优先级等 eflow 专有字段取自 {why}；"
                        f"源表该行 FME Name 为 #N/A，无法直接解析，请核对维护人"])
            ws4.cell(row=ws4.max_row, column=3).fill = FILL_SAME
        for sid, nm, cols in (default_rows or []):
            ws4.append([sid, nm, "补默认值", "、".join(cols) + "（CMDB 无此字段，按现有口径补，请确认）"])
            ws4.cell(row=ws4.max_row, column=3).fill = FILL_MOD
        for c, w in zip("ABCD", (12, 34, 18, 70)):
            ws4.column_dimensions[c].width = w
        ws4.freeze_panes = "A2"

    try:
        return save_workbook_safe(wb, path, allow_fallback)
    finally:
        wb.close()


# ----------------------------------------------------------------------------
# PG（可选，--pg upsert）
# ----------------------------------------------------------------------------
def read_pg_url(env_path):
    if not os.path.isfile(env_path):
        return None
    with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def pg_connect(url):
    import psycopg2
    from urllib.parse import urlparse
    u = urlparse(url)
    return psycopg2.connect(
        host=u.hostname, port=u.port or 5432, dbname=(u.path or "/").lstrip("/"),
        user=u.username, password=u.password, connect_timeout=15,
    )


def pg_backup(conn, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"master_site_db_backup_{ts}.csv")
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {PG_TABLE}")
    cols = [d[0] for d in cur.description]
    n = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for row in cur:
            w.writerow(row)
            n += 1
    cur.close()
    return path, n


def pg_upsert(conn, cmdb_rows, src_file, src_sheet, dry_run=False, prune=False):
    """先 UPDATE 再 INSERT；只写 PG 表里真实存在的列（审计列两套命名自动择存在者）"""
    schema, table = PG_TABLE.split(".")
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s", (schema, table))
    pg_cols = {r[0] for r in cur.fetchall()}

    # 映射里的每一列都必须在目标表里存在。少一列就中止，**不允许静默丢列**：
    # 2026-11-22 事故中 CMDB_TO_PG 还把 Supervisor 映射成不存在的 supervisor（表里叫
    # new_supervisor），pg_upsert 把不存在的列直接丢掉，写库照样打印「update/insert」
    # 成功，Supervisor 一列却保持旧值 —— 页面提示成功、数据没变，比报错更糟。
    missing = sorted({pg for _cm, (pg, _t) in CMDB_TO_PG.items() if pg != "site_id"} - pg_cols)
    if missing:
        cur.close()
        print(f"[FATAL] 目标表 {PG_TABLE} 缺少这些映射列: {missing}")
        print("        拒绝写库：缺列会让对应字段被静默跳过（映射或表结构漂移）。")
        print("        请先修 CMDB_TO_PG 映射或表结构，确认后再跑。")
        raise SystemExit(3)

    ts = datetime.now().isoformat(timespec="seconds")
    audit = {}
    for name, val in (("source_file", os.path.basename(src_file)),
                      ("source_sheet", src_sheet), ("import_time", ts)):
        for cand in (name, "_" + name):
            if cand in pg_cols:
                audit[cand] = val

    records, skipped, dropped = [], [], set()
    for r in cmdb_rows:
        sid_raw = s(r.get("Site ID"))
        if not sid_raw.isdigit():
            skipped.append(sid_raw)
            continue
        rec = {"site_id": int(sid_raw)}
        for cmdb_col, (pg_col, typ) in CMDB_TO_PG.items():
            if pg_col == "site_id":
                continue
            v = r.get(cmdb_col)
            if typ == "int":
                try:
                    rec[pg_col] = int(float(s(v))) if s(v) != "" else None
                except ValueError:
                    rec[pg_col] = None
            elif typ == "float":
                rec[pg_col] = to_float(v)
            else:
                rec[pg_col] = s(v) if s(v) != "" else None
        lat_s, lon_s, _ = fix_coord(r.get("Latitude"), r.get("Longitude"))
        rec["latitude"] = to_float(lat_s)
        rec["longitude"] = to_float(lon_s)
        rec.update(audit)
        if pg_cols:
            for k in [k for k in rec if k not in pg_cols]:
                dropped.add(k)
                rec.pop(k)
        records.append(rec)

    cur.execute(f"SELECT site_id FROM {PG_TABLE}")
    existing = {int(x[0]) for x in cur.fetchall()}
    upd_cols = [c for c in records[0] if c != "site_id"] if records else []
    sql_upd = f"UPDATE {PG_TABLE} SET {', '.join(f'{c}=%s' for c in upd_cols)} WHERE site_id=%s"
    all_cols = ["site_id"] + upd_cols
    sql_ins = (f"INSERT INTO {PG_TABLE} ({', '.join(all_cols)}) "
               f"VALUES ({', '.join(['%s'] * len(all_cols))})")

    n_upd = n_ins = 0
    for rec in records:
        is_upd = rec["site_id"] in existing
        n_upd += is_upd
        n_ins += (not is_upd)
        if dry_run:
            continue
        vals = [rec[c] for c in upd_cols]
        if is_upd:
            cur.execute(sql_upd, vals + [rec["site_id"]])
        else:
            cur.execute(sql_ins, [rec["site_id"]] + vals)
    pruned = []
    if prune:
        keep_ids = {rec["site_id"] for rec in records}
        cur.execute(f"SELECT site_id, site_name FROM {PG_TABLE}")
        pruned = [(int(a), nm) for a, nm in cur.fetchall() if int(a) not in keep_ids]
        if pruned and not dry_run:
            cur.execute(f"DELETE FROM {PG_TABLE} WHERE site_id = ANY(%s)", ([p[0] for p in pruned],))
    if not dry_run:
        conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {PG_TABLE}")
    total = cur.fetchone()[0] if not dry_run else len(existing) + n_ins - len(pruned)
    cur.close()
    return {"updated": n_upd, "inserted": n_ins, "total_after": total, "before": len(existing),
            "skipped": skipped, "pruned": pruned, "dropped_cols": sorted(dropped)}


# ----------------------------------------------------------------------------
# 本地 CMDB sqlite（可选，--cmdb update）
# ----------------------------------------------------------------------------
CMDB_TO_SITE = {
    "Site Name": ["Site Name"],
    "Province/County": ["Province", "Area"],
    "TRM Type": ["TRM Type"],
    "Site Caregory": ["Site Type"],
    "Vip Site": ["vipsite"],
    "Hub Site": ["hubsite"],
}
SITE_KEEP_COLS = {c.lower() for c in (
    "Region", "Victims", "District", "Commune", "fdn", "GS Type", "mgs_type",
    "mgs_early", "delta", "SiteAddress", "Vendor", "sitesubtype",
    "site_information", "site_priority", "site_class", "ran_site",
    "transmision_site", "depot_leader_name", "wo_frontline_engineer",
    "wo_frontline_leader", "mgs_engineer", "fgs_engineer", "organization",
    "site_status", "status", "assigned_depot", "depot_contact", "service",
    "ne", "battery_information", "_sync_time", "_sync_source")}


def backup_sqlite_site(db_path, backup_dir):
    import sqlite3
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"cmdb_site_backup_{ts}.csv")
    cn = sqlite3.connect(db_path)
    try:
        cur = cn.cursor()
        cur.execute("SELECT * FROM site")
        cols = [d[0] for d in cur.description]
        n = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in cur:
                w.writerow(row)
                n += 1
    finally:
        cn.close()
    return path, n


def update_cmdb_site(db_path, cmdb_rows, person_idx, dry_run=False, prune=False,
                     with_network_type=False):
    import sqlite3
    cn = sqlite3.connect(db_path, timeout=30)
    try:
        cur = cn.cursor()
        cur.execute("PRAGMA table_info(site)")
        site_cols = [r[1] for r in cur.fetchall()]
        cmap = {c.lower(): c for c in site_cols}          # sqlite 列名不分大小写
        cur.execute(f'SELECT "{cmap.get("site id", "Site ID")}", '
                    f'"{cmap.get("site maintainer", "Site Maintainer")}" FROM site')
        existing = {norm_id(r[0]): s(r[1]) for r in cur.fetchall()}
        missing_cols = set()

        def build_vals(r):
            out = {}
            for cmdb_col, cols in CMDB_TO_SITE.items():
                v = r.get(cmdb_col)
                for c in cols:
                    real = cmap.get(c.lower())
                    if real is None:
                        missing_cols.add(c)
                        continue
                    if real.lower() in SITE_KEEP_COLS:
                        continue
                    out[real] = yn(v) if c in ("vipsite", "hubsite") else (s(v) or None)
            lat_s, lon_s, _ = fix_coord(r.get("Latitude"), r.get("Longitude"))
            for nm, val in (("latitude", lat_s), ("longitude", lon_s)):
                if val != "" and nm in cmap:
                    out[cmap[nm]] = to_float(val)
            if with_network_type and "network type" in cmap:
                out[cmap["network type"]] = s(r.get("Network Type")) or None
            return out

        sid_col = cmap.get("site id", "Site ID")
        mt_col = cmap.get("site maintainer")
        records = []
        for r in cmdb_rows:
            sid = r["Site ID"]
            rec = build_vals(r)
            oid, _ = resolve_fme(r.get("FME Name"), person_idx)
            if oid and mt_col:
                merged = merge_maintainer(existing.get(sid, ""), oid)
                if merged != existing.get(sid, ""):
                    rec[mt_col] = merged
            records.append((sid, rec))

        n_upd = n_ins = 0
        for sid, rec in records:
            if sid in existing:
                if not rec:
                    continue
                n_upd += 1
                if not dry_run:
                    clause = ", ".join(f'"{c}"=?' for c in rec)
                    cur.execute(f'UPDATE site SET {clause} WHERE "{sid_col}"=?',
                                list(rec.values()) + [sid])
            else:
                n_ins += 1
                if not dry_run:
                    cols = [sid_col] + list(rec.keys())
                    cur.execute(f'INSERT INTO site ({", ".join(chr(34)+c+chr(34) for c in cols)}) '
                                f'VALUES ({", ".join(["?"] * len(cols))})',
                                [sid] + list(rec.values()))
        deleted = []
        if prune:
            keep_ids = {sid for sid, _ in records}
            deleted = [x for x in existing if x not in keep_ids and x.isdigit()]
            if deleted and not dry_run:
                cur.execute(f'DELETE FROM site WHERE "{sid_col}" IN ({",".join("?" * len(deleted))})',
                            deleted)
        if not dry_run:
            cn.commit()
        cur.execute("SELECT COUNT(*) FROM site")
        after = cur.fetchone()[0] if not dry_run else len(existing) + n_ins - len(deleted)
        return {"updated": n_upd, "inserted": n_ins, "deleted": deleted,
                "before": len(existing), "after": after, "missing_cols": sorted(missing_cols)}
    finally:
        cn.close()


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="CMDB Master Site Database -> eflow / master_site_db（A/M/D 标记）")
    ap.add_argument("--src-dir", default=DEFAULT_SRC_DIR)
    ap.add_argument("--src-file", default=None, help="显式指定源 xlsx（默认取最新）")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--template", default=DEFAULT_TEMPLATE, help="eflow 基线 Site.xlsx")
    ap.add_argument("--master-base", default=DEFAULT_MASTER_BASE,
                    help="master_site_db 基线（系统当前导出，支持 xlsx/csv 与 PG 列名）；缺失则不标 A/M/D")
    ap.add_argument("--master-region", choices=["keep", "cmdb"], default="cmdb",
                    help="cmdb=Region 按 CMDB 原值写（默认）；keep=保留基线原值")
    ap.add_argument("--master-keep-empty", action="store_true",
                    help="CMDB 为空时保留基线原值（默认空值也照写，如实反映 CMDB 现状）")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--scope", choices=["full", "delta"], default="full",
                    help="full=全量显示+标记（默认）；delta=只保留 A/M/D 行")
    ap.add_argument("--fill", choices=["cmdb", "keep"], default="cmdb",
                    help="cmdb=映射列一律取 CMDB 原值（默认）；keep=Region/victims 保留 eflow 原值")
    ap.add_argument("--with-network-type", action="store_true")
    ap.add_argument("--no-new-defaults", action="store_true",
                    help="新站不补默认值（默认会补 Status/Site Status/Organization/Site Priority 等）")
    ap.add_argument("--new-priority", default="P4",
                    help="新站 Site Priority / Site Class 默认值（默认 P4，待评估）")
    ap.add_argument("--no-fill-status", action="store_true",
                    help="存量站 Site Status 为空时不补 Operation（默认补）")
    ap.add_argument("--virtual-maintainer", default="FE_ENG_101",
                    help="给虚拟站（HQ_Site / ODC_Site 等）补维护人 ObjectID；默认 FE_ENG_101，传空串则不补")
    ap.add_argument("--site-type-default", default="NORMAL",
                    help="Site Type 为空时的兜底值（虚拟站与待删除站）；默认 NORMAL，传空串则不补")
    ap.add_argument("--pg", choices=["upsert", "none"], default="none", help="默认不写 PG")
    ap.add_argument("--cmdb", choices=["update", "none"], default="none", help="默认不写 sqlite")
    ap.add_argument("--cmdb-db", default=DEFAULT_CMDB_DB)
    ap.add_argument("--persondb", default=DEFAULT_PERSON_DB)
    ap.add_argument("--pg-url", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--allow-fallback", action="store_true",
                    help="兼容旧参数（默认已开启：被占用时自动降级另存）")
    ap.add_argument("--strict-lock", action="store_true",
                    help="严格模式：目标被占用则退出码 2、不降级（默认关闭）")
    ap.add_argument("--prune", action="store_true", help="写库时删除源表已无的 Site ID")
    args = ap.parse_args()
    # 默认永不因「文件被 WPS / 预览面板打开」而整批失败
    args.allow_fallback = not args.strict_lock

    print("=" * 78)
    print("CMDB Master Site Database -> 生成待审查文件（A/M/D 标记版）")
    print("只生成文件，不写数据库。确认无误后请人工导入。")
    print("=" * 78)

    src = find_source(args.src_dir, args.src_file)
    print(f"源文件 : {os.path.basename(src)}  "
          f"({datetime.fromtimestamp(os.path.getmtime(src)).strftime('%Y-%m-%d %H:%M')})")
    headers, raw_rows, sheet, meta = read_cmdb(src, args.sheet)
    print(f"源 sheet: {sheet!r}  表头行={meta['header_row']}  有效列={len([h for h in headers if h])}  "
          f"数据 {len(raw_rows)} 行")
    cmdb_rows, cst = build_cmdb_rows(headers, raw_rows)
    print(f"清洗后 : {cst['kept']} 个唯一站（重复ID {len(cst['dup_ids'])}: {cst['dup_ids'][:6]}，"
          f"空ID行 {cst['blank_rows']}）")
    if cst["kept"] == 0 or cst["kept"] < 0.5 * max(1, cst["raw"] - cst["blank_rows"]):
        print("[WARN] 有效站数异常偏少，请检查源文件公式是否有缓存值")

    if not os.path.isfile(args.template):
        raise SystemExit(f"[FATAL] eflow 基线不存在: {args.template}")
    ef_headers, ef_existing, ef_sheet = load_eflow_template(args.template)
    victims_col = resolve_victims_col(ef_headers)
    print(f"eflow基线: {os.path.basename(args.template)}  {len(ef_headers)} 列 / {len(ef_existing)} 站  "
          f"Victims列={victims_col!r}")

    person_idx = load_person_index(args.persondb)
    print(f"person库: {len(person_idx)} 人" if person_idx
          else f"person库: [WARN] 未读到 {args.persondb}，已有站的 Site Maintainer 保持原值，新增站写人名")

    skip_cols = set() if args.with_network_type else {"Network Type"}

    # eflow
    ef_items, ef_info = classify_eflow(cmdb_rows, ef_existing, ef_headers, victims_col,
                                       args.fill, skip_cols, person_idx,
                                       new_defaults=not args.no_new_defaults,
                                       new_priority=args.new_priority,
                                       fill_blank_status=not args.no_fill_status,
                                       virtual_maintainer=args.virtual_maintainer,
                                       site_type_default=args.site_type_default)
    ef_cnt = Counter(f for _, f, _, _ in ef_items)
    # master
    mbase = load_master_base(args.master_base)
    if mbase is None:
        print(f"master基线: [WARN] 找不到 {args.master_base}，master_site_db 不标 A/M/D"
              f"（把系统当前 master_site_db 导出放到该路径即可）")
        base_note = "无基线，未标记"
    else:
        base_note = f"{os.path.basename(args.master_base)} / {len(mbase)} 站"
    keep_cols, ms_items, ms_protected = classify_master(
        cmdb_rows, headers, mbase, args.master_region, args.master_keep_empty)
    ms_cnt = Counter(f for _, f, _, _ in ms_items)

    print("-" * 78)
    print(f"eflow   : 新增A {ef_cnt['A']}  修改M {ef_cnt['M']}  删除D {ef_cnt['D']}  "
          f"无变化 {ef_cnt['']}（含保护虚拟站 {len(ef_info['protected'])}）")
    print(f"master  : 新增A {ms_cnt['A']}  修改M {ms_cnt['M']}  删除D {ms_cnt['D']}  无变化 {ms_cnt['']}")
    if ms_protected:
        print("master保护: " + "  ".join(f"{c}={n}" for c, n in ms_protected.most_common(6))
              + "（保留基线原值，未按 CMDB 覆盖）")
    print(f"坐标自愈: {len(ef_info['swapped'])}  {sorted(ef_info['swapped'])[:8]}")
    if cst.get("prov_fix"):
        fix_cnt = Counter((a, b) for _, a, b in cst["prov_fix"])
        print(f"省份归一: {len(cst['prov_fix'])}  "
              + "  ".join(f"{a}→{b} ×{n}" for (a, b), n in fix_cnt.most_common(6))
              + f"  IDs={[x[0] for x in cst['prov_fix']][:10]}")
    if ef_info["inherited"]:
        print(f"新站继承: {len(ef_info['inherited'])}  "
              + "  ".join(f"{i}({w})" for i, _, w in ef_info["inherited"][:8]))
    if ef_info["defaulted"]:
        print(f"新站补全: {len(ef_info['defaulted'])} 站补默认值"
              f"（{', '.join(ef_info['defaulted'][0][2]) if ef_info['defaulted'] else ''}）")
    if ef_info["status_filled"]:
        print(f"状态补全: {len(ef_info['status_filled'])} 站 Site Status 空 -> Operation  "
              f"{sorted(ef_info['status_filled'], key=id_sort_key)[:10]}")
    if ef_info["no_maintainer"]:
        print(f"维护人为空: {len(ef_info['no_maintainer'])}  "
              + "  ".join(f"{i}:{n}" for i, n in ef_info["no_maintainer"][:10]))
    if ef_info.get("virtual_filled"):
        print(f"虚拟站补全: {len(ef_info['virtual_filled'])}  "
              + "  ".join(f"{i}({','.join(c)})" for i, c in ef_info["virtual_filled"]))
    fme_empty = [r["Site ID"] for r in cmdb_rows if not s(r.get("FME Name"))]
    if fme_empty:
        print(f"FME为空 : {len(fme_empty)}  {sorted(fme_empty)[:8]}")
    if ef_info["fme_unmatched"]:
        print(f"FME未匹配: {len(ef_info['fme_unmatched'])}  {sorted(ef_info['fme_unmatched'])[:6]}"
              f"（已有站保持原员工号，请在 person 表补人）")
    if ef_cnt["A"] + ef_cnt["D"] > 0.5 * max(1, len(ef_existing)):
        print("[WARN] 新增+删除超过基线一半，请确认 Site.xlsx 基线是否过旧 / Site ID 格式是否一致")
    if ef_info["detail"]:
        cnt = Counter(d[2] for d in ef_info["detail"])
        print("eflow 变化字段 TOP: " + "  ".join(f"{c}={n}" for c, n in cnt.most_common(8)))

    os.makedirs(args.out_dir, exist_ok=True)
    out_eflow = os.path.join(args.out_dir, "eflow_site_update.xlsx")
    out_msdb = os.path.join(args.out_dir, "master_site_db .xlsx")
    out_report = os.path.join(args.out_dir, "cmdb_sync_report.xlsx")
    imp_dir = os.path.join(args.out_dir, "import")
    out_imp_ef = os.path.join(imp_dir, "eflow_site_import.xlsx")
    out_imp_ms = os.path.join(imp_dir, "master_site_db.xlsx")

    if args.scope == "delta":
        ef_out = [i for i in ef_items if i[1]]
        ms_out = [i for i in ms_items if i[1]]
    else:
        ef_out, ms_out = ef_items, ms_items

    summary = [
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("源文件", os.path.basename(src)),
        ("源 sheet", sheet),
        ("CMDB 站数", len(cmdb_rows)),
        ("eflow 基线", f"{os.path.basename(args.template)} / {len(ef_existing)} 站"),
        ("master 基线", base_note),
        ("范围 / 填充", f"{args.scope} / {args.fill}"),
        ("eflow 新增 A", ef_cnt["A"]),
        ("eflow 修改 M", ef_cnt["M"]),
        ("eflow 删除 D", ef_cnt["D"]),
        ("保护跳过（虚拟站）", len(ef_info["protected"])),
        ("master 新增 / 修改 / 删除", f"{ms_cnt['A']} / {ms_cnt['M']} / {ms_cnt['D']}"),
        ("master 保留基线原值", "  ".join(f"{c}={n}" for c, n in ms_protected.most_common(8)) or "无"),
        ("master Region 口径", "保留基线（不覆盖）" if args.master_region == "keep" else "按 CMDB 覆盖"),
        ("不写入的列", ", ".join(sorted(skip_cols)) or "无"),
        ("坐标写反自愈", len(ef_info["swapped"])),
        ("省份拼写归一", f"{len(cst['prov_fix'])}: " +
         "  ".join(f"{i} {a}→{b}" for i, a, b in cst["prov_fix"]) if cst.get("prov_fix") else "无"),
        ("新站继承旧站点属性", f"{len(ef_info['inherited'])}: " +
         "  ".join(f"{i}({w})" for i, _, w in ef_info["inherited"]) if ef_info["inherited"] else "无"),
        ("新站默认值补全", f"{len(ef_info['defaulted'])} 站" if ef_info["defaulted"] else "无"),
        ("Site Status 补 Operation", f"{len(ef_info['status_filled'])} 站" if ef_info["status_filled"] else "无"),
        ("维护人仍为空", f"{len(ef_info['no_maintainer'])}: " +
         "  ".join(f"{i}" for i, _ in ef_info["no_maintainer"]) if ef_info["no_maintainer"] else "无"),
        ("FME 未匹配", f"{sorted(ef_info['fme_unmatched'])}" if ef_info["fme_unmatched"] else "无"),
        ("FME Name 为空的站", f"{len(fme_empty)}: {sorted(fme_empty)}" if fme_empty else "无"),
    ]

    if args.dry_run:
        print("-" * 78)
        print("[DRY-RUN] 不写文件、不写库。将写:")
        for p in (out_eflow, out_msdb, out_report, out_imp_ef, out_imp_ms):
            print("   ", p)
    else:
        locked = [p for p in (out_eflow, out_msdb) if file_locked(p)]
        if locked:
            print("-" * 78)
            print("[WARN] 目标文件正被 WPS / 右侧预览面板打开（占用是常态，不中断生成）：")
            for p in locked:
                print("   ", p)
            print("       本次自动另存带时间戳的副本，不覆盖被打开的文件；")
            print("       关闭右侧预览后重跑一次即可写回正式文件名。")
        try:
            use_color = not args.no_color
            a_ef, d_ef, st_ef = write_flagged_xlsx(
                out_eflow, ef_headers, ef_out, ef_sheet, args.allow_fallback,
                with_flag=True, colorize=use_color, base_note=f"{os.path.basename(args.template)}",
                notes=ef_info.get("notes"))
            a_ms, d_ms, st_ms = write_flagged_xlsx(
                out_msdb, keep_cols, ms_out, "data1", args.allow_fallback,
                with_flag=True, colorize=use_color, base_note=base_note)
            a_rp, d_rp = write_delta_report(out_report, summary, ef_info["detail"],
                                            ef_info["removed"], ef_info["protected"], args.allow_fallback,
                                            inherit_rows=ef_info.get("inherited"),
                                            default_rows=ef_info.get("defaulted"))
            imp_ef_items = [i for i in ef_items if i[1] in ("A", "M")]
            imp_ms_items = [i for i in ms_items if i[1] != "D"]
            a_ie, d_ie, _ = write_flagged_xlsx(out_imp_ef, ef_headers, imp_ef_items, ef_sheet,
                                               args.allow_fallback, with_flag=False, colorize=False)
            a_im, d_im, _ = write_flagged_xlsx(out_imp_ms, keep_cols, imp_ms_items, "data1",
                                               args.allow_fallback, with_flag=False, colorize=False)
        except OutputLocked as e:
            print(f"[LOCKED] 严格模式下无法写入: {e.path}（去掉 --strict-lock 即可自动降级另存）")
            return 2
        # 本次真实产出清单：一旦降级，页面与人工审查都必须以这里为准，
        # 不能再去读那个没被更新的旧文件。
        produced = [
            ("eflow_site_update", out_eflow, a_ef, bool(d_ef)),
            ("master_site_db", out_msdb, a_ms, bool(d_ms)),
            ("cmdb_sync_report", out_report, a_rp, bool(d_rp)),
            ("import_eflow", out_imp_ef, a_ie, bool(d_ie)),
            ("import_master", out_imp_ms, a_im, bool(d_im)),
        ]
        degraded = [x for x in produced if x[3]]
        manifest = {
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "degraded": bool(degraded),
            "files": {k: {"expected": e, "actual": a, "degraded": d} for k, e, a, d in produced},
            "note": "仅生成待审查文件，未写入任何数据库",
        }
        try:
            mf = os.path.join(args.out_dir, "last_run_files.json")
            mtmp = mf + ".tmp"
            with open(mtmp, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
            os.replace(mtmp, mf)
        except OSError as e:
            print(f"[WARN] 产出清单写入失败: {e}")
        print("-" * 78)
        print(f"写出    : {a_ef}  ({len(ef_out)} 行)" + ("  [降级另存]" if d_ef else ""))
        print(f"写出    : {a_ms}  ({len(ms_out)} 行)" + ("  [降级另存]" if d_ms else ""))
        print(f"写出    : {a_rp}")
        print(f"导入副本: {a_ie}  ({len(imp_ef_items)} 行 = A+M)")
        print(f"导入副本: {a_im}  ({len(imp_ms_items)} 行 = CMDB 全量)")
        if degraded:
            print("-" * 78)
            print(f"[DEGRADED] {len(degraded)} 个文件被占用，已另存副本；以下正式文件本次未更新：")
            for _k, e, a, _d in degraded:
                print(f"[DEGRADED] {os.path.basename(e)} -> {os.path.basename(a)}")
            print("[DEGRADED] 关闭右侧预览面板 / WPS 后重跑，即可覆盖正式文件。")
        for nm, keep in (("eflow_site_update.xlsx", a_ef if d_ef else None),
                         ("master_site_db .xlsx", a_ms if d_ms else None),
                         ("cmdb_sync_report.xlsx", a_rp if d_rp else None)):
            for gone in cleanup_fallbacks(args.out_dir, nm, keep=os.path.basename(keep) if keep else None):
                print(f"         已清理旧临时文件: {gone}")

    # 可选：写库
    if args.pg == "upsert":
        print("-" * 78)
        url = args.pg_url or read_pg_url(DEFAULT_ENV)
        if not url:
            print("PG      : [WARN] 没拿到 DATABASE_URL，跳过")
        else:
            conn = pg_connect(url)
            try:
                if not args.dry_run:
                    bp, bn = pg_backup(conn, os.path.join(DEFAULT_BASE_DIR, "backup"))
                    print(f"PG 备份 : {bp}  ({bn} 行)")
                res = pg_upsert(conn, cmdb_rows, src, sheet, args.dry_run, args.prune)
                print(f"PG 写入 : update {res['updated']}  insert {res['inserted']}  "
                      f"表内 {res['before']} -> {res['total_after']}  清理 {len(res['pruned'])}")
                if res["dropped_cols"]:
                    print(f"          [WARN] PG 表里没有这些列，已忽略: {res['dropped_cols']}")
                if res["skipped"]:
                    print(f"          [WARN] 非数字 Site ID 跳过: {res['skipped'][:6]}")
            finally:
                conn.close()
    if args.cmdb == "update":
        print("-" * 78)
        if not os.path.isfile(args.cmdb_db):
            print(f"CMDB库  : [WARN] 找不到 {args.cmdb_db}，跳过")
        else:
            if not args.dry_run:
                bp, bn = backup_sqlite_site(args.cmdb_db, os.path.join(DEFAULT_BASE_DIR, "backup"))
                print(f"CMDB备份: {bp}  ({bn} 行)")
            try:
                res = update_cmdb_site(args.cmdb_db, cmdb_rows, person_idx, args.dry_run,
                                       args.prune, args.with_network_type)
            except Exception as e:  # noqa: BLE001
                print(f"CMDB库  : [WARN] 更新失败 {type(e).__name__}: {e}")
            else:
                print(f"CMDB库  : update {res['updated']}  insert {res['inserted']}  "
                      f"删除 {len(res['deleted'])}  表内 {res['before']} -> {res['after']}")
                if res["missing_cols"]:
                    print(f"          [WARN] site 表里找不到这些列，已跳过: {res['missing_cols']}")

    print("=" * 78)
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
