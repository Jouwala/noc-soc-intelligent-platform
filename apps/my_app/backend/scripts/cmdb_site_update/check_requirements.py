# -*- coding: utf-8 -*-
"""按 `modify/其他要求.txt` 的 10 条规则校验脚本产出文件。

用法:
    python check_requirements.py                 # 校验默认两份产出，报告写 modify/校验报告_其他要求.txt
    python check_requirements.py --file x.xlsx   # 只校验指定文件
    python check_requirements.py --out r.txt     # 指定报告路径
    python check_requirements.py --no-out        # 只打印不落文件

退出码: 0 全部通过 / 1 有规则不通过 / 2 文件缺失
本脚本只读，不做任何修改与导入。
"""
import argparse
import os
import re
import sys
from collections import Counter

try:
    import openpyxl
except ImportError:                                    # pragma: no cover
    sys.exit("缺少 openpyxl，请用项目 venv 的 python 执行本脚本")

BASE = os.path.dirname(os.path.abspath(__file__))
# 脚本与产出分离：产出固定在 my_app/data/eflow/cmdb_site_update/modify（2026-11-22 起）。
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(BASE)))
MODIFY = os.path.join(APP_ROOT, "data", "eflow", "cmdb_site_update", "modify")

# ---- 规则常量（直接来自 其他要求.txt） ----
PROV_OK = {
    "Bomi", "Bong", "Gbarpolu", "Grand Bassa", "Grand Cape Mount", "Grand Gedeh",
    "Grand Kru", "Lofa", "Margibi", "Maryland", "Montserrado", "Nimba",
    "River Cess", "River Gee", "Sinoe",
}
VIRTUAL = {"HQ_Site", "ODC_Site", "VMAX-DC"}          # 规则 3/4 明确豁免
TYPE_OK = {"NORMAL", "COOL", "ERA"}                   # 规则 6
SUBTYPE_OK = {"REGULAR", "ERA", ""}                   # 规则 7/8（允许空）
RE_MAINT = re.compile(r"^FE_(ENG|FE|MG)_\d+$", re.I)  # 规则 5
VMAX_MAINT = "BO_RAN_102"                             # 规则 5 唯一豁免值


def s(v):
    """去 NBSP / 首尾空白；None → ''。"""
    if v is None:
        return ""
    return str(v).replace("\xa0", " ").strip()


def load(path):
    wb = openpyxl.load_workbook(path)
    ws = wb[wb.sheetnames[0]] if "data1" not in wb.sheetnames else wb["data1"]
    hdr = [c.value for c in ws[1]]
    H = {h: i for i, h in enumerate(hdr) if h}
    data = [[c.value for c in r]
            for r in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(hdr))]
    wb.close()
    return hdr, H, data


def check_file(path, label):
    """返回 (规则结果列表, 行数)。规则结果: (序号, 描述, 异常列表)"""
    if not os.path.isfile(path):
        return None, 0
    hdr, H, data = load(path)
    res = []

    def col(name):
        return H.get(name)

    # 1 Site ID 重复 / 空
    bad = []
    if col("Site ID") is None:
        bad.append(("缺列", "Site ID"))
    else:
        ids = [s(v[H["Site ID"]]) for v in data]
        cnt = Counter(ids)
        bad += [("重复", k, f"{cnt[k]} 次") for k in cnt if cnt[k] > 1 and k]
        bad += [("空值", f"第{i + 2}行", "") for i, x in enumerate(ids) if not x]
    res.append((1, "Site ID 重复 / 空值", bad))

    # 2 Province 白名单
    bad = []
    if col("Province") is None:
        bad.append(("缺列", "Province"))
    else:
        bad = [(s(v[H["Site ID"]]), s(v[H["Province"]]))
               for v in data if s(v[H["Province"]]) not in PROV_OK]
    res.append((2, "Province 超出 15 个法定值", bad))

    # 3 Latitude 负值 / 空（虚拟站豁免）
    bad = []
    if col("Latitude") is None:
        bad.append(("缺列", "Latitude"))
    else:
        for v in data:
            sid = s(v[H["Site ID"]])
            if sid in VIRTUAL:
                continue
            t = s(v[H["Latitude"]])
            if not t:
                bad.append((sid, "空值"))
                continue
            try:
                if float(t) < 0:
                    bad.append((sid, f"负值 {t}"))
            except ValueError:
                bad.append((sid, f"非数值 {t!r}"))
    res.append((3, "Latitude 负值 / 空值（非虚拟站）", bad))

    # 4 Longitude 空（虚拟站豁免）
    bad = []
    if col("Longitude") is None:
        bad.append(("缺列", "Longitude"))
    else:
        for v in data:
            sid = s(v[H["Site ID"]])
            if sid in VIRTUAL:
                continue
            t = s(v[H["Longitude"]])
            if not t:
                bad.append((sid, "空值"))
            else:
                try:
                    float(t)
                except ValueError:
                    bad.append((sid, f"非数值 {t!r}"))
    res.append((4, "Longitude 空值（非虚拟站）", bad))

    # 5 Site Maintainer 空 / 非法（VMAX-DC 允许 BO_RAN_102）
    bad = []
    if col("Site Maintainer") is None:
        bad.append(("缺列", "Site Maintainer"))
    else:
        for v in data:
            sid = s(v[H["Site ID"]])
            maint = s(v[H["Site Maintainer"]])
            if not maint:
                bad.append((sid, "空值"))
                continue
            for tok in (x.strip() for x in re.split(r"[;,]", maint)):
                if not tok:
                    continue
                if sid == "VMAX-DC" and tok.upper() == VMAX_MAINT:
                    continue
                if not RE_MAINT.match(tok):
                    bad.append((sid, f"非法值 {tok!r}（整格 {maint!r}）"))
    res.append((5, "Site Maintainer 空值 / 非法值", bad))

    # 6 Site Type
    bad = []
    if col("Site Type") is None:
        bad.append(("缺列", "Site Type"))
    else:
        bad = [(s(v[H["Site ID"]]), repr(s(v[H["Site Type"]])))
               for v in data if s(v[H["Site Type"]]).upper() not in TYPE_OK]
    res.append((6, "Site Type 非 NORMAL / COOL / ERA 或空", bad))

    # 7 SiteSubType
    bad = []
    if col("SiteSubType") is None:
        bad.append(("缺列", "SiteSubType"))
    else:
        bad = [(s(v[H["Site ID"]]), repr(s(v[H["SiteSubType"]])))
               for v in data if s(v[H["SiteSubType"]]).upper() not in SUBTYPE_OK]
    res.append((7, "SiteSubType 非 REGULAR / ERA / 空", bad))

    # 8 Site Information
    bad = []
    if col("Site Information") is None:
        bad.append(("缺列", "Site Information"))
    else:
        bad = [(s(v[H["Site ID"]]), repr(s(v[H["Site Information"]])))
               for v in data if s(v[H["Site Information"]]).upper() not in SUBTYPE_OK]
    res.append((8, "Site Information 非 REGULAR / ERA / 空", bad))

    # 9 Site Status
    bad = []
    if col("Site Status") is None:
        bad.append(("缺列", "Site Status"))
    else:
        bad = [(s(v[H["Site ID"]]), repr(s(v[H["Site Status"]])))
               for v in data if s(v[H["Site Status"]]) != "Operation"]
    res.append((9, "Site Status 非 Operation", bad))

    # 10 Status
    bad = []
    if col("Status") is None:
        bad.append(("缺列", "Status"))
    else:
        bad = [(s(v[H["Site ID"]]), repr(s(v[H["Status"]])))
               for v in data if s(v[H["Status"]]) != "Active"]
    res.append((10, "Status 非 Active", bad))

    return res, len(data)


def render(results, label, nrows, shown=20):
    lines = []
    lines.append("=" * 74)
    lines.append(f"【{label}】数据行 {nrows}")
    lines.append("=" * 74)
    fail = 0
    for no, desc, bad in results:
        if no == 1:
            pass
        if bad:
            fail += 1
        lines.append("")
        lines.append(f"[{'PASS' if not bad else 'FAIL'}] {no}. {desc} —— 异常 {len(bad)} 处")
        for x in bad[:shown]:
            lines.append(f"      {x}")
        if len(bad) > shown:
            lines.append(f"      ... 另有 {len(bad) - shown} 处")
    lines.append("")
    lines.append(f">>> {label}: {10 - fail}/10 通过，{fail} 项不通过")
    return lines, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", default=None,
                    help="指定要校验的 xlsx（可重复）；默认校验 modify 下三份产出")
    ap.add_argument("--out", default=os.path.join(MODIFY, "校验报告_其他要求.txt"),
                    help="报告输出路径，默认 modify/校验报告_其他要求.txt")
    ap.add_argument("--no-out", action="store_true", help="只在终端打印，不写文件")
    args = ap.parse_args()

    if args.file:
        targets = [(p, os.path.basename(p)) for p in args.file]
    else:
        targets = [
            (os.path.join(MODIFY, "eflow_site_update.xlsx"), "eflow_site_update（审查全量）"),
            (os.path.join(MODIFY, "import", "eflow_site_import.xlsx"), "eflow_site_import（导入用）"),
        ]

    out, total_fail, missing = [], 0, []
    for path, label in targets:
        res, n = check_file(path, label)
        if res is None:
            missing.append(path)
            continue
        lines, fail = render(res, label, n)
        out += lines
        total_fail += fail

    text = "\n".join(out)
    print(text)
    if missing:
        print("\n[跳过] 文件不存在:")
        for m in missing:
            print("   ", m)
    if args.out and not args.no_out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n报告已保存: {args.out}")

    if missing and not out:
        return 2
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
