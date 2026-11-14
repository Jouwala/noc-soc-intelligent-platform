"""sp —— SP 物料域（物料看板 / 明细 / 汇总 / 同步 / 镜像推送 / 报表）。

第二轮重构 §11-4 按域重组第 24 组（2026-11-14）：原先散在 ``services/``
根上的 10 个 SP 域文件收进本包：

- ``sp_common`` / ``sp_dashboard``：公共口径与看板
- ``sp_details_service`` / ``sp_material_summary_service``：明细与汇总
- ``sp_material_name`` / ``get_sp_table``：物料名称与取表
- ``sp_material_sync`` / ``sp_online_sync`` / ``sp_mirror_push``：同步与镜像推送
- ``sp_report_service``：SP 报表

既有的 ``sp_report_gen/`` 子包（报表生成脚本）不在本组范围；
旁置文档 ``sp_material_name.md`` 留在 ``services/`` 根上，路径已同步。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.sp.<模块>`` 路径。
``get_sp_table`` 的相对导入已同步改绝对路径。
"""
