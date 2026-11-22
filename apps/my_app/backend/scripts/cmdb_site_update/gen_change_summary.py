# -*- coding: utf-8 -*-
"""生成 eflow_site_update.xlsx 的变更汇总（Markdown）。

所有数字来自 change_stats.stats() 实时统计：本文件不出现任何硬编码的站数、
日期、站号。基线或源表更新后直接重跑即可，不需要手改文案。
"""
import os

from change_stats import MODIFY, stats

st = stats()
ef, ms, src = st["eflow"], st["master"], st["source"]
A, M, D, same = ef["A"], ef["M"], ef["D"], ef["same"]
remap, new_sites, check = st["remap"], st["new_sites"], st["check"]
virtual_all = ef["virtual_all"]

NOTES = {
    "Region": "口径统一：旧“方位/县名”混合 → North / South 二分法",
    "Downstream Sites Count": "原 Victims 字段，取值口径对齐 CMDB",
    "Site Type": "按 SiteSubType / Site Information 推导（ERA / NORMAL）",
    "TRM Type": "传输介质命名规范化（如 Satellite Link → SES / Starlink）",
    "Latitude": "坐标更新，含经纬度疑似互换的纠正",
    "Longitude": "坐标更新，含经纬度疑似互换的纠正",
    "Site Maintainer": "FME Name 解析为 person 表 ObjectID",
    "Site Name": "站名拼写 / 命名更新",
    "Site Status": "空值按现有口径补 Operation",
    "Province": "省份取值与基线不同（拼写已归一，差异来自源表数据调整）",
    "Area": "同 Province",
}

L = []
w = L.append

w("# eflow_site_update.xlsx 变更汇总\n")
w(f"- 生成时间：{st['generated_at']}")
w(f"- 数据基准：{src['name']}（{src['mtime']}）")
w(f"- 对比基线：Site.xlsx（eflow 现网导出，{ef['baseline']} 条）")
w("- 文件位置：my_app/data/eflow/cmdb_site_update/modify/\n")

# ---------------------------------------------------------------- 一、概览
w("## 一、总体概览\n")
w("| 项目 | 数量 |")
w("|---|---|")
w(f"| eflow 基线条目 | {ef['baseline']}（含虚拟站 {len(virtual_all)} 个，物理站 "
  f"{ef['baseline'] - len(virtual_all)}） |")
w(f"| CMDB 站点数 | {src['unique']}（源表 {src['rows']} 行，按唯一 Site ID 去重） |")
w(f"| 变更行合计 | {A + M + D} |")
w(f"| 新增（A） | {A} |")
w(f"| 修改（M） | {M}（CMDB 站 {ef['cmdb_m']} + 虚拟站 {ef['virtual_m']}） |")
w(f"| 删除（D） | {D} |")
w(f"| 无变化 | {same} |\n")

if virtual_all:
    w(f"> 虚拟站 {len(virtual_all)} 个（{' / '.join(virtual_all)}）Site ID 非数字，作保护行处理，"
      "不参与 A/M/D 统计。")
if remap["pairs"]:
    w(f"> 删除 {D} 个中有 {len(remap['pairs'])} 个属于「同站换 ID」（新旧 ID 相差 1000），"
      f"eflow 侧表现为删 1 站 + 加 1 站；扣除后净删除 {len(remap['real_d'])} 个、"
      f"净新增 {A - len(remap['in_a'])} 个。")
else:
    w(f"> 本次未识别到「同站换 ID」记录，删除的 {D} 个均需按真实删除确认。")
w("")

# ---------------------------------------------------------------- 二、字段
w(f"## 二、字段级变更分布（M 行共 {M} 站）\n")
if M and st["fields"]:
    w("| 字段 | 变更站数 | 占比 | 说明 |")
    w("|---|---|---|---|")
    for h, n in st["fields"]:
        w(f"| {h} | {n} | {n / M * 100:.1f}% | {NOTES.get(h, '')} |")
    w("")
    w("按变更字段数量分布：" + "、".join(
        f"{k} 个字段 {v} 站" for k, v in sorted(st["dist"].items())) + "。")
else:
    w("本次无修改行。")
w("")

w("### 2.1 字段迁移明细\n")
any_detail = False
for h, _n in st["fields"]:
    tr = sorted(st["transitions"].get(h) or [], key=lambda x: -x[1])
    if not tr:
        continue
    any_detail = True
    w(f"**{h}**\n")
    w("| 原值 → 新值 | 站数 |")
    w("|---|---|")
    for (a, b), c in (tr if len(tr) <= 12 else tr[:8]):
        w(f"| {a or '(空)'} → {b or '(空)'} | {c} |")
    if len(tr) > 12:
        w(f"\n> 另有 {len(tr) - 8} 种迁移组合未列出。")
    w("")
if not any_detail:
    w("本次无字段迁移。\n")

# ---------------------------------------------------------------- 三、增删
w("## 三、新增与删除\n")
w(f"### 3.1 新增站点（{A} 个" +
  (f"，其中 {len(remap['in_a'])} 个为同站换 ID" if remap["in_a"] else "") + "）\n")
if remap["new_a"]:
    w("| Site ID | Site Name | Region | Province | 备注 |")
    w("|---|---|---|---|---|")
    for x in sorted(remap["new_a"], key=lambda r: int(r["id"]) if r["id"].isdigit() else 0):
        mk = f"同站换 ID（原 {x['remap_from']}）" if x["remap_from"] else "真新增"
        w(f"| {x['id']} | {x['name']} | {x['region']} | {x['province']} | {mk} |")
else:
    w("本次无新增。")
w("")

w(f"### 3.2 删除站点（{D} 个" +
  (f"，其中 {len(remap['pairs'])} 个为同站换 ID" if remap["pairs"] else "") + "）\n")
if D:
    w("| Site ID | Site Name | Region | Province | 备注 |")
    w("|---|---|---|---|---|")
    for x in remap["real_d"]:
        note = "**真删除，需确认**"
        if x["like"]:
            note = f"与 CMDB 现存站 {'/'.join(x['like'])} 同名，疑似 ID 打错位"
        w(f"| {x['id']} | {x['name']} | {x['region']} | {x['province']} | {note} |")
    for x in remap["pairs"]:
        w(f"| {x['old']} | {x['name']} | {x['region']} | {x['province']} | "
          f"同站换 ID（新 ID {x['new']}） |")
else:
    w("本次无删除。")
w("")

# ---------------------------------------------------------------- 四、复核
w("## 四、需要重点复核的变更\n")
seq = [0]


def item(text):
    seq[0] += 1
    w(f"{seq[0]}. {text}")


if remap["pairs"]:
    pairs = "、".join(f"{p['old']}→{p['new']}（{p['name']}）" for p in remap["pairs"][:20])
    item(f"**{len(remap['pairs'])} 组同站换 ID**：{pairs}"
         + ("…" if len(remap["pairs"]) > 20 else "")
         + "。eflow 侧表现为删 1 站 + 加 1 站，若业务上应保留原 ID，需人工确认后再导入。")
if remap["real_d"]:
    names = "、".join(
        f"{x['name']}({x['id']}"
        + (f"，与 CMDB 现存站 {'/'.join(x['like'])} 同名，疑似 ID 打错位" if x["like"] else "")
        + ")" for x in remap["real_d"])
    item(f"**{len(remap['real_d'])} 个站点在 CMDB 中已不存在**：{names}。"
         "可能是真删除，也可能是 ID 打错一位或站名同时改动导致未识别为换 ID，请确认是否确已退网。")
if st["coord"]:
    det = "；".join(f"{x['name']}({x['id']}) {x['old']}→{x['new']}" for x in st["coord"][:8])
    item(f"**{len(st['coord'])} 站坐标变更**：{det}" + ("…" if len(st["coord"]) > 8 else "")
         + "。其中经纬度互换的在导入前需抽样核对。")
if st["maint"]:
    item(f"**{len(st['maint'])} 站 Site Maintainer 变更**（FME Name 解析为员工 ObjectID），"
         "请确认派工归属。明细见 cmdb_sync_report.xlsx「变更明细」页。")
if st["swaps"]:
    desc = "；".join(f"{f} {len(v)} 组" for f, v in st["swaps"].items())
    ex = []
    for f, v in st["swaps"].items():
        ex.extend(f"{x['a']} ↔ {x['b']}（{f}）" for x in v[:2])
    item(f"检测到**成对互换**：{desc}。例如 {'、'.join(ex[:4])}。"
         "同一对站的两个取值互相交换，通常是源表两行数据错位或重复 ID 冲突所致，"
         "请先在 CMDB 侧核对，再决定是否按本次结果覆盖 eflow。")
st_era = dict(st["transitions"].get("Site Type") or []).get(("", "ERA"), 0)
if st_era:
    item(f"**{st_era} 站 Site Type 由空值补为 ERA**：属新增分类，需确认 eflow 字典中已存在 ERA 类型。")
if new_sites["inherited"]:
    item(f"**{new_sites['inherited']} 个新增站继承了同名旧站的维护人 / Site Status / 优先级等字段**，"
         "Excel 中带「继承自旧站点」批注，明细见 cmdb_sync_report.xlsx「新站补全说明」页，请核对维护人。")
if new_sites["defaulted"]:
    item(f"**{new_sites['defaulted']} 个全新站按默认口径补全**（Status=Active、Site Status=Operation、"
         "Organization=ZTE、Priority/Class=P4、SubType/Information=REGULAR、RAN/Transmision Site=Yes），"
         "Excel 中带「新站默认值」批注，请确认。")
if ef["virtual_m"]:
    item(f"**{ef['virtual_m']} 个虚拟站由「无变化」升为修改**：补维护人 / Site Status / Site Type 后"
         "才会进入导入副本，否则 eflow 端空缺无法补上。请确认维护人归属。")
if seq[0] == 0:
    w("本次未发现需要特别复核的项。")
w("")

# ---------------------------------------------------------------- 五、产出
w("## 五、产出文件\n")
w("| 文件 | 用途 |")
w("|---|---|")
w("| modify/eflow_site_update.xlsx | eflow 侧差异全量（着色，A/M/D 标记 + 原值批注） |")
w(f"| modify/master_site_db .xlsx | master_site_db 侧差异（A{ms['A']} / M{ms['M']} / D{ms['D']}） |")
w(f"| modify/import/eflow_site_import.xlsx | 供 eflow 导入的纯净版（{st['import_rows']['eflow']} 行） |")
w(f"| modify/import/master_site_db.xlsx | 供 PG 导入的纯净版（{st['import_rows']['master']} 行） |")
w("| modify/cmdb_sync_report.xlsx | 同步明细报告 |")
if check["passed"] is not None:
    w(f"| modify/校验报告_其他要求.txt | 按《其他要求》10 条规则自检：{check['passed']}/{check['total']} 通过 |")
w("")
w("> 本次仅生成审查文件，未对 eflow 与 PostgreSQL 做任何写入；审查通过后由人工执行导入。\n")

# ---------------------------------------------------------------- 六、源表
w("## 六、源表数据质量\n")
w(f"- 源表 `{src['name']}`：数据 **{src['rows']} 行**，唯一 Site ID **{src['unique']} 个**。")
if src["dups"]:
    w("")
    w("| Site ID | 出现的站名 | 影响 |")
    w("|---|---|---|")
    for d in src["dups"]:
        w(f"| {d['id']} | {' / '.join(d['names'])} | 同一 ID 对应多个站名，"
          "当前按「首次出现优先」去重，需在 CMDB 侧确认是否应拆分或更正 |")
else:
    w("- 未发现重复 Site ID。")
w("")
if src["fme_na"]:
    w(f"- FME Name 有 **{src['fme_na']} 站**为 Excel 错误值 `#N/A`，master 侧 fme_name 只能留空，需人工补人。")
else:
    w("- FME Name 无 `#N/A`。")
w("")

out = os.path.join(MODIFY, "eflow_site_update_变更汇总.md")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("OK", out, len("\n".join(L)))
