"""gis —— GIS 地图域（微波拓扑叠加 / GIS 根因分析）。

第二轮重构 §11-4 按域重组第 16 组（2026-11-14）：原先散在 ``services/``
根上的 2 个 ``gis_*`` 文件收进本包：``gis_mw_overlay``（微波链路叠加）、
``gis_rca_service``（GIS 根因分析）。

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.gis.<模块>`` 路径。
"""
