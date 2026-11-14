"""ms —— MS 合同 SLA 域（SLA 看板 / 报表 / KPI 报告与同步）。

第二轮重构 §11-4 按域重组第 5 组（2026-11-14）：原先散在 ``services/``
根上的 5 个 ``ms_*`` 文件收进本包：

- ``ms_sla_service`` / ``ms_sla_sync``：SLA 数据服务与同步
- ``ms_sla_dashboard`` / ``ms_sla_report``：SLA 看板与 WO SLA 报表
- ``ms_kpi_report_service``：合同 KPI 报告

同名旁置文档 ``ms_sla_dashboard.md`` 与 ``requirements_ms_sla_dashboard.txt``
留在 ``services/`` 根上（旁置文档/依赖清单不随本组移动，内容中的路径已同步）。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.ms.<模块>`` 路径。
"""
