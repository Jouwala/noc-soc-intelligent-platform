# -*- coding: utf-8 -*-
"""CMDB 站点同步的变更口径统计（全部动态，供汇总 md 与邮件正文共用）。

历史教训：汇总脚本曾把「eflow 基线 830」「CMDB 站点数 = A + M」「源表 876 行 / 874 站」
「13 站经纬度」「202 站 Site Type」等写死，基线一更新就全错。这里统一从产出文件与
源表现算，生成脚本里不再出现任何硬编码站数、日期、站号。

数据来源：
  modify/eflow_site_update.xlsx    A/M/D、字段级变更、重编号、虚拟站
  modify/master_site_db .xlsx      master 侧 A/M/D
  modify/cmdb_sync_report.xlsx     「新站补全说明」页：继承 / 默认值条数
  modify/import/*.xlsx             导入副本行数
  <my_app>/db 最新 ZTE OLR 源表     数据行数、唯一站数、重复 ID、FME #N/A
"""
from __future__ import annotations

import glob
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

import openpyxl

BASE = os.path.dirname(os.path.abspath(__file__))
# 脚本在 backend/scripts/cmdb_site_update（2026-11-22 从 data/ 移出、纳入版本库），
# 产出仍在 my_app/data/eflow/cmdb_site_update/modify —— 路径按 my_app 根反推。
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(BASE)))
MODIFY = os.path.join(APP_ROOT, "data", "eflow", "cmdb_site_update", "modify")
SRC_DIR = os.path.join(APP_ROOT, "db")
ORANGE = "FFFFC000"


def s(v):
    return "" if v is None else str(v).replace("\xa0", " ").strip()


def _old(comment):
    """从批注里取「原值: xxx」后的旧值。"""
    t = comment or ""
    if "原值:" in t:
        t = t.rsplit("原值:", 1)[1]
    return t.strip()


def load(path, sheet=None):
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
    hdr = [c.value for c in ws[1]]
    hi = {h: i for i, h in enumerate(hdr)}
    rows = []
    for r in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(hdr)):
        vals = [c.value for c in r]
        fills = [str(c.fill.fgColor.rgb) if (c.fill and c.fill.fill_type == "solid") else None
                 for c in r]
        coms = [c.comment.text if c.comment else None for c in r]
        rows.append((vals, fills, coms))
    wb.close()
    return hdr, hi, rows


def source_info():
    """最新 ZTE OLR 源表：行数、唯一站、重复 ID、FME Name=#N/A 数量。"""
    out = {"path": None, "name": None, "mtime": None,
           "rows": 0, "unique": 0, "dups": [], "fme_na": 0}
    cands = [p for p in glob.glob(os.path.join(SRC_DIR, "ZTE OLR MS Master Site Database*.xlsx"))
             if not os.path.basename(p).startswith("~$")]
    if not cands:
        return out
    path = max(cands, key=os.path.getmtime)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = next((n for n in wb.sheetnames if "Master Site Database" in n), wb.sheetnames[0])
        head, body = None, []
        for row in wb[sheet].iter_rows(min_col=1, max_col=24, values_only=True):
            if head is None:
                if row and any(s(c) == "Site ID" for c in row):
                    head = [s(c) for c in row]
                continue
            body.append(row)
        hi = {h: i for i, h in enumerate(head or [])}
        i_id = hi.get("Site ID", 0)
        i_nm = hi.get("Site Name", 1)
        i_fme = hi.get("FME Name")
        names = defaultdict(list)
        for r in body:
            sid = s(r[i_id])
            if sid:
                names[sid].append(s(r[i_nm]))
        cnt = Counter(s(r[i_id]) for r in body if s(r[i_id]))
        out.update({
            "path": path,
            "name": os.path.basename(path),
            "mtime": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
            "rows": len(body),
            "unique": len(cnt),
            "dups": [{"id": k, "names": names[k]} for k, v in cnt.items() if v > 1],
            "fme_na": sum(1 for r in body if i_fme is not None and s(r[i_fme]) == "#N/A"),
        })
    finally:
        wb.close()
    return out


def _data_rows(path):
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        n = max(0, wb[wb.sheetnames[0]].max_row - 1)
        wb.close()
        return n
    except Exception:
        return 0


def _new_site_counts():
    got = {"inherited": 0, "defaulted": 0}
    path = os.path.join(MODIFY, "cmdb_sync_report.xlsx")
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        if "新站补全说明" in wb.sheetnames:
            for row in wb["新站补全说明"].iter_rows(min_row=2, values_only=True):
                kind = s(row[2]) if len(row) > 2 else ""
                if "继承" in kind:
                    got["inherited"] += 1
                elif "默认" in kind or "补全" in kind:
                    got["defaulted"] += 1
        wb.close()
    except Exception:
        pass
    return got


def _check_result():
    got = {"passed": None, "total": None}
    path = os.path.join(MODIFY, "校验报告_其他要求.txt")
    try:
        txt = open(path, encoding="utf-8").read()
        hits = re.findall(r"(\d+)\s*/\s*(\d+)\s*通过", txt)
        if hits:
            got = {"passed": int(hits[-1][0]), "total": int(hits[-1][1])}
    except OSError:
        pass
    return got


def stats():
    h, hi, rows = load(os.path.join(MODIFY, "eflow_site_update.xlsx"))
    i_id = hi.get("Site ID", 1)
    i_nm = hi.get("Site Name", 2)

    A = [v for v, f, c in rows if s(v[0]) == "A"]
    D = [v for v, f, c in rows if s(v[0]) == "D"]
    M = [(v, f, c) for v, f, c in rows if s(v[0]) == "M"]
    same = sum(1 for v, f, c in rows if not s(v[0]))
    # 虚拟站（HQ_Site / ODC_Site / VMAX-DC）Site ID 非数字，只做保护行，不参与 A/M/D
    virtual_all = [s(v[i_id]) for v, f, c in rows if s(v[i_id]) and not s(v[i_id]).isdigit()]
    virt_m = sum(1 for v, f, c in M if not s(v[i_id]).isdigit())

    fc = Counter()
    on = defaultdict(Counter)
    for v, f, c in M:
        for i, name in enumerate(h):
            if i == 0 or f[i] != ORANGE:
                continue
            fc[name] += 1
            on[name][(_old(c[i]), s(v[i]))] += 1
    dist = Counter(sum(1 for i in range(1, len(h)) if f[i] == ORANGE) for v, f, c in M)

    # 原始站名留作展示，小写副本只用于同名匹配，避免报告里出现 "weinzu" 这种小写站名
    name_of = {s(v[i_id]): s(v[i_nm]) for v, f, c in rows if s(v[0]) in ("A", "M", "D")}
    name_lc = {k: v.lower() for k, v in name_of.items()}
    remap = {}
    for dv in D:
        did, dn = s(dv[i_id]), s(dv[i_nm]).lower()
        for sid2, nm2 in name_lc.items():
            # 同站换 ID：源表可能把 ID 改大也可能改小（11-06 版出现 4297→3297），
            # 所以用绝对差 1000，而不是固定的「新 = 旧 + 1000」。
            if (nm2 == dn and sid2 != did and sid2.isdigit() and did.isdigit()
                    and abs(int(sid2) - int(did)) == 1000):
                remap[did] = sid2
                break
    a_ids = {s(v[i_id]) for v in A}
    d_row = {s(v[i_id]): v for v in D}
    remap_pairs = [{"old": k, "new": v, "name": name_of.get(k, ""),
                    "region": s(d_row[k][hi.get("Region", 0)]) if k in d_row else "",
                    "province": s(d_row[k][hi.get("Province", 0)]) if k in d_row else ""}
                   for k, v in remap.items()]
    # 真删除行如果与某个新增站同名，多半是源表 ID 打错一位（如 4309 应为 4308），
    # 不是真的退网；把同名新增站一并标出来，交给人工确认。
    a_name_map = {}
    for v, f, c in rows:
        if s(v[0]) in ("A", "M"):  # CMDB 现存的行（新增 + 修改）
            a_name_map.setdefault(s(v[i_nm]).lower(), []).append(s(v[i_id]))
    real_d = [{"id": s(v[i_id]), "name": s(v[i_nm]),
               "region": s(v[hi.get("Region", 0)]), "province": s(v[hi.get("Province", 0)]),
               "like": a_name_map.get(s(v[i_nm]).lower(), [])}
              for v in D if s(v[i_id]) not in remap]
    new_a = [{"id": s(v[i_id]), "name": s(v[i_nm]),
              "region": s(v[hi.get("Region", 0)]), "province": s(v[hi.get("Province", 0)]),
              "remap_from": next((p["old"] for p in remap_pairs
                                  if p["new"] == s(v[i_id])), "")}
             for v in A]

    def detail(field):
        """某字段的逐站变更明细（旧值取自批注），用于报告里列出待核对清单。"""
        i = hi.get(field)
        if i is None:
            return []
        return [{"id": s(v[i_id]), "name": s(v[i_nm]),
                 "old": _old(c[i]), "new": s(v[i])}
                for v, f, c in M if f[i] == ORANGE]

    def swaps_of(field):
        """值成对互换：A→B 与 B→A 同时出现，通常意味着源表两行数据对调/错位。"""
        tr = on.get(field) or {}
        out = []
        for (a, b), n in tr.items():
            if a and b and a != b and (b, a) in tr and a < b:
                out.append({"a": a, "b": b, "n": n + tr[(b, a)]})
        return out

    try:
        _, _, rows_ms = load(os.path.join(MODIFY, "master_site_db .xlsx"))
        c2 = Counter(s(v[0]) for v, f, c in rows_ms)
        master = {"A": c2["A"], "M": c2["M"], "D": c2["D"],
                  "same": c2[""], "total": len(rows_ms)}
    except Exception:
        master = {"A": 0, "M": 0, "D": 0, "same": 0, "total": 0}

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": source_info(),
        "eflow": {
            "A": len(A), "M": len(M), "D": len(D), "same": same, "total": len(rows),
            # 基线条目 = 无变化 + 修改 + 删除（新增行不在基线里）
            "baseline": same + len(M) + len(D),
            "virtual_m": virt_m, "virtual_all": virtual_all,
            "cmdb_m": len(M) - virt_m,
        },
        "master": master,
        "remap": {"pairs": remap_pairs, "in_a": [p for p in remap_pairs if p["new"] in a_ids],
                  "real_d": real_d, "new_a": new_a},
        "fields": fc.most_common(),
        "transitions": {k: list(v.items()) for k, v in on.items()},
        "dist": dist,
        "new_sites": _new_site_counts(),
        "coord": detail("Latitude"),
        "maint": detail("Site Maintainer"),
        "swaps": {f: swaps_of(f) for f in
                  ("Site Name", "Latitude", "Longitude", "Province", "Area")
                  if swaps_of(f)},
        "import_rows": {
            "eflow": _data_rows(os.path.join(MODIFY, "import", "eflow_site_import.xlsx")),
            "master": _data_rows(os.path.join(MODIFY, "import", "master_site_db.xlsx")),
        },
        "check": _check_result(),
    }


if __name__ == "__main__":
    import json
    st = stats()
    st["transitions"] = {k: v for k, v in st["transitions"].items()}
    print(json.dumps(st, ensure_ascii=False, indent=2, default=str))
