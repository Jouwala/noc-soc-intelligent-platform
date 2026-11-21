#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 Orange 网络拓扑 PDF 重新生成 OLIB 拓扑页的站点表（?page=olibTopo）。

**手工维护工具**，不在任何定时任务里跑：上游每年更新一两次拓扑 PDF，人工执行一次。

⚠ 本脚本只管**物理图**（第 1 页）与站点名单。逻辑图（`logical.svg`）与逻辑点位
   `LOGICAL_PINS` 自 2026-11-21 起改用单独一页的 PDF，由 `gen_olib_logical.py` 生成；
   两份脚本互不覆盖。

它做的事
--------
1. 读 PDF 第 1 页（物理拓扑）的全部文字对象，把「站点名」与「说明文字」分开：
   · 站点名 = 9pt 的正文行；同一基线上被拆开的片段（如 `18` + `th` + ` street`）合并；
   · 红色 `5G` 是**独立文字对象**，不在站点名里 —— 先摘出来，再按最近邻归属到站点；
   · 图例（Backbone fiber / ON going IPRAN link / DWDM / 5G SITE / P26A-D）、
     里程（400M / 1600M …）、国名（IVORY COAST）等说明文字按白名单剔除。
2. 坐标 = 站点名文本框中心 → 原图宽高的千分比（0~1000），与页面叠加口径一致。
3. `sector` 沿用历史分组：站名与旧表同名则继承旧 sector，新站取**最近的历史站点**的 sector。
4. 输出可直接替换进 `frontend/src/pages/networkTopology/topologyData.ts` 的 RAW_SITES 片段。

用法
----
    python backend/scripts/gen_olib_topo.py "D:\\muse\\Orange network topology_20260623.pdf"
    python backend/scripts/gen_olib_topo.py <pdf> --write    # 直接改写 topologyData.ts

依赖：PyMuPDF（`pip install pymupdf`；本机已装）。**不要**把它加进 requirements.txt，
它不是运行时依赖。

⚠ 抽完必须人工过一眼 `--list` 的输出：PDF 里偶尔有拼写残缺的标签（如 `Compound Su`），
   脚本不会猜，宁可原样保留，由人决定是否对齐旧名。
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import pymupdf

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
TOPO_DATA = FRONTEND / "src" / "pages" / "networkTopology" / "topologyData.ts"

# 图例 / 里程 / 国名等说明文字：不是站点，坐标里也不该出现
ANNOTATIONS = {
    "Backbone fiber 40G", "Backbone fiber 10G", "ON going IPRAN link", "DWDM", "5G SITE",
    "P26A", "P26B", "P26C", "P26D", "IVORY COAST",
    "400M", "800M", "1600M", "5700M", "HUAWEI 212M", "HUAWEI 223M",
}
# 原图上的地名标签（大写），归一成站点表里的写法
RENAMES = {"DANANE": "Danane", "Compound Su": "Compound Suakoko"}
# 断行片段：属于上一行站名的续行
CONTINUATIONS = {"Community", "Community）", "Comm", "Comm."}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_old_sites() -> list[dict]:
    src = TOPO_DATA.read_text(encoding="utf-8")
    rows = re.findall(
        r'^\s*\["((?:[^"\\]|\\.)*)",\s*([\d.]+),\s*([\d.]+),\s*(true|false),\s*(true|false),\s*"([^"]*)"\],?\s*$',
        src, re.M)
    return [dict(name=n.replace('\\"', '"'), x=float(x), y=float(y),
                 is5g=f5 == "true", isp=isp == "true", sector=sec)
            for n, x, y, f5, isp, sec in rows]


def read_spans(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for sp in line["spans"]:
                if not sp["text"].strip():
                    continue
                x0, y0, x1, y1 = sp["bbox"]
                spans.append(dict(t=sp["text"], x0=x0, y0=y0, x1=x1, y1=y1,
                                  base=sp["origin"][1], color=sp["color"], size=sp["size"]))
    return spans


def build_labels(spans: list[dict], old_names: set[str]) -> tuple[list[dict], list[dict]]:
    """把文字对象收成站点名候选；返回 (labels, 5G 标记)。"""
    spans = [s for s in spans if s["t"].strip() not in ANNOTATIONS]
    markers = [s for s in spans if s["t"].strip() == "5G" and s["color"] == 0xFF0000]
    spans = [s for s in spans if not (s["t"].strip() == "5G" and s["color"] == 0xFF0000)]

    # 上标（`18` 的 `th`）并入左侧正文
    super_ = [s for s in spans if s["size"] <= 6.5]
    normal = [s for s in spans if s["size"] > 6.5]
    for u in super_:
        best, best_d = None, 1e9
        for s in normal:
            if abs(s["base"] - u["base"]) <= 5 and abs(u["x0"] - s["x1"]) <= 2:
                d = abs(s["base"] - u["base"]) + abs(u["x0"] - s["x1"])
                if d < best_d:
                    best, best_d = s, d
        if best is not None:
            best["t"] += u["t"]
            best["x1"] = max(best["x1"], u["x1"])

    # 同一基线上紧挨着的文字片段（间距 0~2pt）属于同一个站名；
    # 间距稍大但右片段以小写字母开头（如 `Kai` + `s town`）也是续写
    normal.sort(key=lambda s: (round(s["base"], 1), s["x0"]))
    groups: list[list[dict]] = []
    for s in normal:
        gap = s["x0"] - groups[-1][-1]["x1"] if groups else -1
        same_line = bool(groups) and abs(groups[-1][-1]["base"] - s["base"]) < 1.2
        if same_line and (0 <= gap <= 2.0 or (0 <= gap <= 10.0 and s["t"][:1].islower())):
            groups[-1].append(s)
        else:
            groups.append([s])
    labels = [dict(t="".join(x["t"] for x in g).strip(),
                   x0=min(x["x0"] for x in g), x1=max(x["x1"] for x in g),
                   y0=min(x["y0"] for x in g), y1=max(x["y1"] for x in g)) for g in groups]

    # 断行成两行的站名：下一行是 Community / 括号收尾 / 以上一行结尾是左括号
    labels.sort(key=lambda l: (l["y0"], l["x0"]))
    merged, used = [], set()
    for i, l in enumerate(labels):
        if i in used:
            continue
        cur = dict(l)
        for j in range(i + 1, len(labels)):
            if j in used:
                continue
            o = labels[j]
            if o["y0"] - cur["y1"] > 4.0:
                break
            if o["y0"] - cur["y1"] < -1:
                continue
            overlap = min(cur["x1"], o["x1"]) - max(cur["x0"], o["x0"])
            if overlap < 0.6 * min(cur["x1"] - cur["x0"], o["x1"] - o["x0"]):
                continue
            joinable = (o["t"] in CONTINUATIONS
                        or o["t"].startswith(("Community", "）", ")"))
                        or cur["t"].endswith(("(", "（"))
                        or o["t"].startswith("Center St.")
                        # 小写开头的短续行 / 拼起来正好是旧表里的站名
                        or (o["t"][:1].islower() and len(o["t"]) <= 14)
                        or norm(cur["t"] + o["t"]) in old_names)
            if joinable:
                cur["t"] = re.sub(r"\s+", " ", cur["t"] + " " + o["t"]).strip()
                cur["x0"], cur["x1"] = min(cur["x0"], o["x0"]), max(cur["x1"], o["x1"])
                cur["y1"] = max(cur["y1"], o["y1"])
                used.add(j)
        used.add(i)
        merged.append(cur)
    # 只剩上标的碎片（`th` / `nd`）和孤立括号：不是站名
    merged = [l for l in merged if re.sub(r"[^A-Za-z0-9]", "", l["t"]) not in ("", "th", "nd", "st", "rd")]
    return merged, markers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--write", action="store_true", help="直接改写 topologyData.ts 的 RAW_SITES")
    ap.add_argument("--list", action="store_true", help="打印全部站点")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    old = load_old_sites()
    old_names = {norm(o["name"]) for o in old}
    labels, markers = build_labels(read_spans(page), old_names)

    def percent(l):
        l["x"] = round((l["x0"] + l["x1"]) / 2 / W * 1000, 1)
        l["y"] = round((l["y0"] + l["y1"]) / 2 / H * 1000, 1)
        l["isp"] = bool(re.search(r"\bISP\b", l["t"], re.I))
        l["is5g"] = False
        l["t"] = re.sub(r"\s+", " ", l["t"]).strip()
        l["t"] = RENAMES.get(l["t"], l["t"])
        return l

    labels = [percent(l) for l in labels]

    # 红色 5G 标记按最近邻归属；图例里那个 5G 样本（左下角）不算
    legend_markers = [m for m in markers if m["x0"] < 330 and m["y0"] > 4000]
    for m in markers:
        if m in legend_markers:
            continue
        best, best_d = None, 1e9
        for l in labels:
            dx = max(0, max(l["x0"] - m["x1"], m["x0"] - l["x1"]))
            dy = max(0, max(l["y0"] - m["y1"], m["y0"] - l["y1"]))
            d = math.hypot(dx, dy)
            if d < best_d:
                best, best_d = l, d
        if best is not None and best_d <= 20:
            best["is5g"] = True

    # sector：同名继承旧值，新站取最近历史站点（按新坐标）的分组
    old_by_name = {}
    for o in old:
        old_by_name.setdefault(norm(o["name"]), o)
    anchors = []
    for l in labels:
        o = old_by_name.get(norm(l["t"]))
        if o:
            l["sector"] = o["sector"]
            anchors.append(l)
    for l in labels:
        if "sector" in l:
            continue
        best, best_d = None, 1e9
        for a in anchors:
            d = (a["x"] - l["x"]) ** 2 + (a["y"] - l["y"]) ** 2
            if d < best_d:
                best, best_d = a, d
        l["sector"] = best["sector"] if best else "其他"

    labels.sort(key=lambda l: l["t"].lower())
    isp = sum(1 for l in labels if l["isp"])
    five = sum(1 for l in labels if l["is5g"])
    old_names = {norm(o["name"]) for o in old}
    new_names = {norm(l["t"]) for l in labels}
    old_display = {norm(o["name"]): o["name"] for o in old}
    new_display = {norm(l["t"]): l["t"] for l in labels}

    print(f"PDF 页 1：{W:.1f} × {H:.1f} pt")
    print(f"站点 {len(labels)}（旧表 {len(old)}）；5G {five}；ISP {isp}")
    print(f"旧表有、新图没有：{len(old_names - new_names)} 条")
    print(f"新图有、旧表没有：{len(new_names - old_names)} 条")
    print("  旧表独有：" + "、".join(sorted(old_display[n] for n in old_names - new_names)))
    print("  新图独有：" + "、".join(sorted(new_display[n] for n in new_names - old_names)))

    if args.list:
        for l in labels:
            flag = ("5G" if l["is5g"] else "  ").ljust(3)
            print(f"{l['x']:7.1f} {l['y']:7.1f} {flag} {l['t']:<38} {l['sector']}")

    if args.write:
        body = "\n".join(
            '  [%s, %s, %s, %s, %s, %s],' % (
                json.dumps(l["t"], ensure_ascii=False),
                f"{l['x']:.1f}", f"{l['y']:.1f}",
                "true" if l["is5g"] else "false",
                "true" if l["isp"] else "false",
                json.dumps(l["sector"], ensure_ascii=False))
            for l in labels)
        src = TOPO_DATA.read_text(encoding="utf-8")
        new_src, n = re.subn(
            r"(?<=const RAW_SITES: \[string, number, number, boolean, boolean, string\]\[\] = \[\n)[\s\S]*?(?=\n\])",
            body, src, count=1)
        if n != 1:
            print("RAW_SITES 定位失败，未写入", file=sys.stderr)
            return 2
        new_src = re.sub(
            r"(physical: \{ w: )[\d.]+(, h: )[\d.]+( \})",
            lambda m: f"{m.group(1)}{W:.1f}{m.group(2)}{H:.1f}{m.group(3)}",
            new_src, count=1)
        TOPO_DATA.write_text(new_src, encoding="utf-8", newline="\n")
        print(f"已写入 {TOPO_DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
