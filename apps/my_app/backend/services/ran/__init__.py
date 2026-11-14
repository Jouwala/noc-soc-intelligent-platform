"""ran —— RAN 无线侧域（小区可用率 / 小区 KPI / UME 主 KPI 与网络 KPI）。

第二轮重构 §11-4 按域重组第 22 组（2026-11-14）：原先散在 ``services/``
根上的 6 个 ``ran_*`` 文件收进本包：

- ``ran_cell_availability_sync``：小区可用率同步
- ``ran_cells_kpi_service`` / ``ran_cells_kpi_sync``：小区 KPI 服务与同步
- ``ran_ume_main_kpi`` / ``ran_ume_network_kpi``：UME 主 KPI 与网络 KPI
- ``ran_ume_service``：RAN UME 基础服务

既有的 ``ran_ume/`` 与 ``gp_ran/`` 子包不在本组范围。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.ran.<模块>`` 路径。
``ran_ume_service`` 的相对导入与根上 ``packet_loss_service`` 对本包的
相对导入已同步改绝对路径。
"""
