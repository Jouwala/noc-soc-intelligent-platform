# -*- coding: utf-8 -*-
"""生成邮件正文（纯文本）。

所有数字来自 change_stats.stats() 实时统计：不写死日期、站数、站号，
基线或源表更新后直接重跑即可。
"""
import os

from change_stats import MODIFY, stats

st = stats()
ef, ms, src = st["eflow"], st["master"], st["source"]
A, M, D = ef["A"], ef["M"], ef["D"]
remap, new_sites, check = st["remap"], st["new_sites"], st["check"]

L = []
w = L.append

w(f"【主题】eflow / master_site_db 站点数据同步变更汇总（{st['generated_at'][:10]}）")
w("")
w("各位好，")
w("")
w(f"已完成 {src['name']} 与 eflow 现网站点数据的一致性比对，")
w(f"共识别 {A + M + D} 条差异记录，明细见附件 eflow_site_update.xlsx。请审查后确认导入。")
w("")
w("一、总体情况")
w(f"  · 数据基准：{src['name']}（{src['mtime']}）")
w(f"  · eflow 基线条目：{ef['baseline']}（含虚拟站 {len(ef['virtual_all'])} 个，"
  f"物理站 {ef['baseline'] - len(ef['virtual_all'])}）")
w(f"  · CMDB 站点数：{src['unique']}（源表 {src['rows']} 行，按唯一 Site ID 去重）")
w(f"  · 新增（A）：{A}    修改（M）：{M}    删除（D）：{D}    无变化：{ef['same']}")
if ef["virtual_all"]:
    w(f"  · 另有虚拟站 {len(ef['virtual_all'])} 个（{' / '.join(ef['virtual_all'])}），"
      "为非数字 ID 的保护行，不参与 A/M/D 统计")
if remap["pairs"]:
    w(f"  · 删除 {D} 个中有 {len(remap['pairs'])} 个属于「同站换 ID」（新旧 ID 相差 1000），"
      f"扣除后净删除 {len(remap['real_d'])} 个、净新增 {A - len(remap['in_a'])} 个")
else:
    w(f"  · 未识别到同站换 ID，删除的 {D} 个均需按真实删除确认")
w("")

w(f"二、字段级变更分布（修改行共 {M} 站）")
if st["fields"]:
    for h, n in st["fields"]:
        w(f"  · {h}：{n} 站")
    if st["dist"]:
        w("")
        w("  按变更字段数量：" + "、".join(
            f"{k} 个字段 {v} 站" for k, v in sorted(st["dist"].items())) + "。")
else:
    w("  本次无修改行。")
w("")

w("三、需要重点复核")
idx = [0]


def item(text):
    idx[0] += 1
    w(f"  {idx[0]}. {text}")


if remap["pairs"]:
    pairs = "、".join(f"{p['old']}→{p['new']}（{p['name']}）" for p in remap["pairs"][:20])
    item(f"{len(remap['pairs'])} 组同站换 ID：{pairs}"
         + ("…" if len(remap["pairs"]) > 20 else "")
         + "。eflow 侧表现为删 1 站 + 加 1 站，若业务上应保留原 ID，请在导入前确认处理方式。")
if remap["real_d"]:
    names = "、".join(
        f"{x['name']}({x['id']}"
        + (f"，与 CMDB 现存站 {'/'.join(x['like'])} 同名，疑似 ID 打错位" if x["like"] else "")
        + ")" for x in remap["real_d"])
    item(f"{len(remap['real_d'])} 个站点在 CMDB 中已不存在：{names}。"
         "可能是真删除，也可能是 ID 打错一位或站名同时改动导致未识别为换 ID，请确认是否确已退网。")
if st["coord"]:
    det = "；".join(f"{x['name']}({x['id']}) {x['old']}→{x['new']}" for x in st["coord"][:8])
    item(f"{len(st['coord'])} 站坐标变更：{det}" + ("…" if len(st["coord"]) > 8 else "")
         + "。经纬度疑似互换的请在导入前抽样核对。")
if st["maint"]:
    item(f"{len(st['maint'])} 站 Site Maintainer 变更（FME Name 解析为员工 ObjectID），"
         "请确认派工归属，明细见 cmdb_sync_report.xlsx「变更明细」页。")
if st["swaps"]:
    desc = "；".join(f"{f} {len(v)} 组" for f, v in st["swaps"].items())
    ex = []
    for f, v in st["swaps"].items():
        ex.extend(f"{x['a']} ↔ {x['b']}（{f}）" for x in v[:2])
    item(f"检测到成对互换：{desc}，例如 {'、'.join(ex[:4])}。"
         "同一对站的两个取值互相交换，通常是源表两行数据错位或重复 ID 冲突，"
         "请先在 CMDB 侧核对再导入。")
st_era = dict(st["transitions"].get("Site Type") or []).get(("", "ERA"), 0)
if st_era:
    item(f"{st_era} 站 Site Type 由空值补为 ERA，属新增分类，请确认 eflow 字典中已存在 ERA。")
if new_sites["inherited"]:
    item(f"{new_sites['inherited']} 个新增站从同名旧站继承维护人 / Site Status / 优先级等字段"
         "（Excel 带「继承自旧站点」批注），请核对维护人，明细见 cmdb_sync_report.xlsx「新站补全说明」页。")
if new_sites["defaulted"]:
    item(f"{new_sites['defaulted']} 个全新站按默认口径补全（Status=Active、Site Status=Operation、"
         "Organization=ZTE、Priority/Class=P4、SubType/Information=REGULAR、RAN/Transmision Site=Yes），"
         "Excel 带「新站默认值」批注，请确认。")
if ef["virtual_m"]:
    item(f"{ef['virtual_m']} 个虚拟站由「无变化」升为修改：补维护人 / Site Status / Site Type 后"
         "才会进入导入副本，否则 eflow 端空缺无法补上。请确认维护人归属。")
if idx[0] == 0:
    w("  本次未发现需要特别复核的项。")
w("")

w("四、附件与产出文件")
w("  · eflow_site_update.xlsx         eflow 侧差异全量（绿=新增、橙=修改、红=删除；修改格附原值批注）")
w(f"  · master_site_db .xlsx           master_site_db 侧差异（A{ms['A']} / M{ms['M']} / D{ms['D']}）")
w(f"  · import/eflow_site_import.xlsx  供 eflow 导入的纯净版（{st['import_rows']['eflow']} 行）")
w(f"  · import/master_site_db.xlsx     供 PostgreSQL 导入的纯净版（{st['import_rows']['master']} 行）")
w("  · cmdb_sync_report.xlsx          同步明细报告（汇总 / 变更明细 / 待删除站点 / 新站补全说明）")
if check["passed"] is not None:
    w(f"  · 校验报告_其他要求.txt           按《其他要求》10 条规则自检：{check['passed']}/{check['total']} 通过")
w("")

w("五、源表数据质量")
w(f"  源表 {src['name']}：数据 {src['rows']} 行、唯一 Site ID {src['unique']} 个。")
if src["dups"]:
    for d in src["dups"]:
        w(f"  · 重复 ID {d['id']}：{' / '.join(d['names'])} —— 同一 ID 对应多个站名，"
          "当前按「首次出现优先」去重，需在 CMDB 侧确认是否拆分或更正。")
else:
    w("  · 未发现重复 Site ID。")
if src["fme_na"]:
    w(f"  · FME Name 有 {src['fme_na']} 站为 Excel 错误值 #N/A，master 侧 fme_name 只能留空，需人工补人。")
else:
    w("  · FME Name 无 #N/A。")
w("")

w("六、说明")
w("  本次仅生成文件供人工审查，未对 eflow 系统及 PostgreSQL 数据库做任何写入操作。")
w("  请审查确认后由人工执行导入。")
w("")
w("如有疑问请随时联系。")
w("")
w("谢谢。")

out = os.path.join(MODIFY, "邮件正文_变更汇总.txt")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("OK", out, len("\n".join(L)))
