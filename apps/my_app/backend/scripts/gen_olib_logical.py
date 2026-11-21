#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 OLIB 逻辑拓扑 PDF 重新生成「逻辑拓扑」页签（?page=olibTopo，view=logical）。

**手工维护工具**，不在任何定时任务里跑：上游隔一段时间更新一版拓扑 PDF，人工执行一次。
物理图（?view=physical）与站点名单仍由 `gen_olib_topo.py` 从另一份两页版 PDF 的第 1 页生成，
两者互不覆盖 —— 2026-11-21 起逻辑图改用单独一页的《OLIB FO_Network_20260901.pdf》。

它做的事
--------
1. 把 PDF 第 1 页**矢量导出**为 `frontend/public/topology/logical.svg`
   （`text_as_path=True`，文字转轮廓，浏览器不需要装 FranklinGothic 字体）。
2. 同步 `topologyData.ts` 的 `TOPO_PAGE_SIZE.logical`（viewBox 尺寸 = PDF pt）。
3. 从页内文字对象还原**站点标签簇**（Visio 把每个词拆成独立文字对象、标签自动换行），
   再与 `RAW_SITES` 的站名匹配，重建 `LOGICAL_PINS`（千分比坐标，与叠加层同一口径）。

匹配口径（宁缺勿错）
--------------------
- 站名规范化后完全相同 → 直接匹配（大小写、空格、标点都不敏感）。
- 站点表里带括号后缀的（如 `Fendell(ISP)`、`Buchanan1（b）`）去掉后缀再比。
- 图上缩写/拼写差异走下面 `ALIASES` 白名单，**只登记人工确认过是同一站的写法**；
  对不上的标签（KSB / UL / AML Loop 3 等不在 667 条名单里的）一律跳过，不猜。
- 同一站在图上出现两次（黑色现有 / 红色在建）时取非红色那条（现有的位置）。

用法
----
    python backend/scripts/gen_olib_logical.py "D:\\\\muse\\\\OLIB FO_Network_20260901.pdf" --preview
    python backend/scripts/gen_olib_logical.py <pdf> --write      # 写 SVG + 改写 topologyData.ts

依赖：PyMuPDF（`pip install pymupdf`；本机已装）。**不要**加进 requirements.txt，它不是运行时依赖。
⚠ 写完必须人工过一眼 `--preview` 的「未匹配标签」清单：新图出现新写法时，确认是不是漏了别名。
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pymupdf

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
TOPO_DATA = FRONTEND / "src" / "pages" / "networkTopology" / "topologyData.ts"
LOGICAL_SVG = FRONTEND / "public" / "topology" / "logical.svg"

# 图例 / 标题 / 规划说明等非站点文字（按包含关系剔除）
NON_SITE_TEXT = (
    "New site to be build", "10GE", "100GE", "DWDM", "IPRAN", "Metro ring",
    "Existing", "built 2025", "OLIB FO Network", "DATA CENTER", "Obsolete",
    "Csquared Ring", "Copyright",
)
# 图例区（左下角）不参与站点匹配
LEGEND_BOX = (330.0, 520.0)   # x < 330 且 y < 520（pt）

# 逻辑图写法（规范化后）→ 站点表写法。只登记人工确认过的同一站。
ALIASES = {
    "epcmtokadeh": "EPCM",
    "kebbah": "Kebbah road",
    "lpmccomm": "LPMC Comm 2",
    "sethi": "Sethi Compound",
    "gbokonima": "Gbankonima",
    "barnesvilleestate": "Barnersville estate",
    "vaitown": "Via town",
    "kessellyblvd": "Keselly Boulevard",
    "kokoyard": "Kokoya Road",
    "yekepa5": "Yekepa 5 Area P",
    "policeacademy": "Police Academy Rd",
    "sanniquellie2": "Saniquellie 2",
    "bensonst": "Benson Street",
    "campjohnsonrd": "Camp Johnson Road",
    "schefflin": "Shefflin",
    "duportrd": "Duport Road",
    "twotire": "Two Tires",
    "bokaytown": "Luwein(Bokaytown)",
    "methodistcompound": "Methodist Cpd",
    "sophietown": "Sophe town",
    "odc": "ODC 2nd",
    "randallst": "Randall Street",
    "zoweinta": "Zowinta",
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def strip_paren(s: str) -> str:
    return re.sub(r"[\(（].*?[\)）]", "", s)


def read_sites() -> list[str]:
    src = TOPO_DATA.read_text(encoding="utf-8")
    raw = src.split("const RAW_SITES")[1].split("\n]")[0]
    return [n for n, *_ in re.findall(
        r'\["((?:[^"\\]|\\.)*)",\s*([\d.]+),\s*([\d.]+),\s*(true|false),\s*(true|false),\s*"([^"]*)"\]',
        raw)]


def read_spans(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for sp in line["spans"]:
                if not sp["text"].strip():
                    continue
                spans.append(dict(t=sp["text"], bbox=list(sp["bbox"]),
                                  base=sp["origin"][1], size=sp["size"], color=sp["color"]))
    return spans


def merge_superscripts(spans: list[dict]) -> list[dict]:
    """`10` + `th` / `72` + `nd` 这类上标是独立文字对象，先并回左侧正文。"""
    sups = [s for s in spans if s["size"] < 8.5]
    normal = [s for s in spans if s["size"] >= 8.5]
    for u in sups:
        best, best_d = None, 1e9
        for s in normal:
            gap = u["bbox"][0] - s["bbox"][2]
            if abs(s["base"] - u["base"]) <= 5 and -0.5 <= gap <= 2:
                d = abs(s["base"] - u["base"]) + abs(gap)
                if d < best_d:
                    best, best_d = s, d
        if best is not None:
            best["t"] += u["t"]
            best["bbox"] = [best["bbox"][0], min(best["bbox"][1], u["bbox"][1]),
                            max(best["bbox"][2], u["bbox"][2]), max(best["bbox"][3], u["bbox"][3])]
    return normal


def build_segments(spans: list[dict], min_size: float = 9.5, max_size: float = 16.0) -> list[dict]:
    """同一基线、水平紧挨的文字对象合成一行（一个标签的一行）。"""
    spans = [s for s in spans if min_size <= s["size"] <= max_size]
    spans.sort(key=lambda s: (round(s["base"], 1), s["bbox"][0]))
    rows: list[list] = []
    for s in spans:
        if rows and abs(rows[-1][0] - s["base"]) <= 1.5:
            rows[-1][1].append(s)
        else:
            rows.append([s["base"], [s]])
    segs = []
    for base, items in rows:
        items.sort(key=lambda s: s["bbox"][0])
        cur = [items[0]]
        for a, b in zip(items, items[1:]):
            if b["bbox"][0] - a["bbox"][2] <= 12.0:
                cur.append(b)
            else:
                segs.append(dict(base=base, items=cur))
                cur = [b]
        segs.append(dict(base=base, items=cur))
    for sg in segs:
        boxes = [i["bbox"] for i in sg["items"]]
        sg["x0"] = min(b[0] for b in boxes)
        sg["x1"] = max(b[2] for b in boxes)
        sg["y0"] = min(b[1] for b in boxes)
        sg["y1"] = max(b[3] for b in boxes)
        sg["cx"] = (sg["x0"] + sg["x1"]) / 2
        sg["text"] = re.sub(r"\s+", " ", "".join(i["t"] for i in sg["items"])).strip()
        sg["sz"] = round(sg["items"][0]["size"], 1)
        sg["color"] = sg["items"][0]["color"]
    return segs


def build_labels(segs: list[dict]) -> list[dict]:
    """把标签的多行合成一个簇：行距 5~18pt、水平中心相差 ≤40pt、字号与颜色相同。

    Visio 的标签是窄文本框自动换行，所以同一标签的相邻行居中、行距固定（12pt≈14.4）；
    行距 19pt 以上或颜色不同的是**相邻的另一个标签**（如 `New Georgia` 与 `St Michael`），
    不能并进来。
    """
    used = [False] * len(segs)
    out = []
    for i, sg in enumerate(segs):
        if used[i]:
            continue
        used[i] = True
        cur = [sg]
        base = sg["base"]
        while True:
            best, best_dy = None, 1e9
            for j, o in enumerate(segs):
                if used[j]:
                    continue
                dy = o["base"] - base
                if not (5.0 <= dy <= 18.0):
                    continue
                if o["sz"] != cur[-1]["sz"] or o["color"] != cur[-1]["color"]:
                    continue
                if abs(o["cx"] - cur[-1]["cx"]) > 40.0:
                    continue
                if dy < best_dy:
                    best, best_dy = j, dy
            if best is None:
                break
            used[best] = True
            cur.append(segs[best])
            base = segs[best]["base"]
        x0 = min(s["x0"] for s in cur)
        x1 = max(s["x1"] for s in cur)
        y0 = min(s["y0"] for s in cur)
        y1 = max(s["y1"] for s in cur)
        out.append(dict(
            text=re.sub(r"\s+", " ", " ".join(s["text"] for s in sorted(cur, key=lambda x: x["base"]))).strip(),
            bbox=(x0, y0, x1, y1), cx=(x0 + x1) / 2, cy=(y0 + y1) / 2,
            size=cur[0]["sz"], color=cur[0]["color"], nlines=len(cur)))
    return out


def match_site(text: str, by_norm: dict, by_base: dict) -> str | None:
    n = norm(text)
    if n in by_norm:
        return by_norm[n]
    if n in ALIASES:
        return ALIASES[n]
    if n in by_base:
        return by_base[n]
    return None


def build_pins(page, sites: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    W, H = page.rect.width, page.rect.height
    labels = build_labels(build_segments(merge_superscripts(read_spans(page))))

    def is_site_label(c: dict) -> bool:
        x0, y0 = c["bbox"][0], c["bbox"][1]
        if x0 < LEGEND_BOX[0] and y0 < LEGEND_BOX[1]:
            return False
        return not any(k in c["text"] for k in NON_SITE_TEXT)

    by_norm: dict[str, str] = {}
    by_base: dict[str, str] = {}
    for s in sites:
        by_norm.setdefault(norm(s), s)
        by_base.setdefault(norm(strip_paren(s)), s)

    matched: dict[str, list[dict]] = {}
    unmatched: list[dict] = []
    for c in labels:
        if not is_site_label(c):
            continue
        site = match_site(c["text"], by_norm, by_base)
        if site is None:
            unmatched.append(c)
        else:
            matched.setdefault(site, []).append(c)

    pins = []
    for site, cands in matched.items():
        # 同一站出现两次时优先非红色（现有站点），再优先字号大的
        def rank(c: dict):
            red = 1 if c["color"] in (0xb01513, 0xc00000) else 0
            return (red, -c["size"], c["cy"], c["cx"])
        c = min(cands, key=rank)
        pins.append(dict(name=site,
                         x=round(c["cx"] / W * 1000, 1),
                         y=round(c["cy"] / H * 1000, 1)))
    pins.sort(key=lambda p: p["name"].lower())
    return pins, unmatched, [c["text"] for c in labels if is_site_label(c)]


def render_pins_body(pins: list[dict]) -> str:
    return "\n".join(
        '  { name: %s, x: %s, y: %s },' % (
            json.dumps(p["name"], ensure_ascii=False), p["x"], p["y"])
        for p in pins)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--preview", action="store_true", help="只打印统计与点位，不写文件")
    ap.add_argument("--write", action="store_true", help="写 logical.svg 并改写 topologyData.ts")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    if doc.page_count < 1:
        print("PDF 没有页面", file=sys.stderr)
        return 2
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    sites = read_sites()
    pins, unmatched, labels = build_pins(page, sites)

    print(f"PDF：{pdf.name} 第 1 页 {W:.1f} × {H:.1f} pt")
    print(f"站点表 {len(sites)} 条；图上识别 {len(labels)} 个标签，匹配到 {len(pins)} 个站点")
    print(f"未匹配标签 {len(unmatched)} 个：" + "、".join(
        sorted(c["text"] for c in unmatched)))

    if args.preview:
        for p in pins:
            print(f'  {{ name: "{p["name"]}", x: {p["x"]}, y: {p["y"]} }},')

    if args.write:
        svg = page.get_svg_image(text_as_path=True)
        # 显式 LF：Windows 上 write_text 默认把 \n 转成 \r\n，会让整份 SVG 在 git 里变成全行改动
        LOGICAL_SVG.write_text(svg, encoding="utf-8", newline="\n")
        print(f"已写 {LOGICAL_SVG}（{len(svg) / 1024:.0f} KB）")

        src = TOPO_DATA.read_text(encoding="utf-8")
        new_src, n = re.subn(
            r"(export const LOGICAL_PINS: \{ name: string; x: number; y: number \}\[\] = \[\n)[\s\S]*?(?=\n\])",
            lambda m: m.group(1) + render_pins_body(pins), src, count=1)
        if n != 1:
            print("LOGICAL_PINS 定位失败，未写入", file=sys.stderr)
            return 2
        new_src, n = re.subn(
            r"(logical: \{ w: )[\d.]+(, h: )[\d.]+( \})",
            lambda m: f"{m.group(1)}{W:.1f}{m.group(2)}{H:.1f}{m.group(3)}",
            new_src, count=1)
        if n != 1:
            print("TOPO_PAGE_SIZE.logical 定位失败，未写入", file=sys.stderr)
            return 2
        TOPO_DATA.write_text(new_src, encoding="utf-8", newline="\n")
        print(f"已写入 {TOPO_DATA}（LOGICAL_PINS {len(pins)} 条，画布 {W:.1f}×{H:.1f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
