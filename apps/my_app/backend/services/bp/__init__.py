"""bp —— BP 报表域（Business Plan 口径 / 报表导入、生成与同步）。

第二轮重构 §11-4 按域重组第 3 组（2026-11-14）：原先散在 ``services/``
根上的 6 个 ``bp_*`` 文件收进本包：

- ``bp_caliber``：BP 口径定义
- ``bp_active_user``：活跃用户口径数据
- ``bp_report_import`` / ``bp_report_store``：报表导入与落库
- ``bp_report_service`` / ``bp_report_sync``：报表生成服务与同步任务

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.bp.<模块>`` 路径。
"""
