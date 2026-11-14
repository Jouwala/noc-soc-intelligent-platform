"""cn —— CN KPI 看板域（PS/CS 核心网 KPI 日粒度与 5 分钟粒度数据服务）。

第二轮重构 §11-4 按域重组第 14 组（2026-11-14）：原先散在 ``services/``
根上的 2 个 ``cn_kpi_*`` 文件收进本包：``cn_kpi_service``（CN KPI
看板数据服务）、``cn_kpi_5mins_service``（5 分钟粒度）。

既有的 ``cn_ems/`` 子包是 EMS 采集域，不在本组范围。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.cn.<模块>`` 路径。
"""
