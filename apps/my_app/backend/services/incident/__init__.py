"""incident —— 事件总览域（事件概览服务 / 同步 / AI 归因入口）。

第二轮重构 §11-4 按域重组第 8 组（2026-11-14）：原先散在 ``services/``
根上的 3 个 ``incident_*`` 文件收进本包：``incident_overview_service``
（概览服务）、``incident_overview_sync``（概览同步）、
``incident_ai_service``（AI 事件分析）。

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.incident.<模块>`` 路径。
"""
