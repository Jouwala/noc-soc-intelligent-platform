"""ipran —— IPRAN 域（图表服务 / UME KPI 服务与查询配置）。

第二轮重构 §11-4 按域重组第 9 组（2026-11-14）：原先散在 ``services/``
根上的 3 个 ``ipran_*`` 文件收进本包：``ipran_chart_service``（图表）、
``ipran_ume_kpi_service``（UME KPI）、``ipran_configs_generated``
（由 Payload.txt 解析生成的 KPI 查询配置数据模块，与其消费方同包）。

既有的 ``ipran_topo/`` 子包是拓扑域，不在本组范围。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.ipran.<模块>`` 路径。
旁置文档 ``ipran_ume_kpi_service.md`` 留在 ``services/`` 根上，路径已同步。
"""
