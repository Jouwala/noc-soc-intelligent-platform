"""backfill —— 历史数据回补域（ran_kpi_daily 新/旧区域 Group 历史回补脚本）。

第二轮重构 §11-4 按域重组第 12 组（2026-11-14）：原先散在 ``services/``
根上的 2 个 ``backfill_*`` 文件收进本包：
``backfill_new_group_history``（新命名区域）、
``backfill_old_group_history``（旧命名 ZTE-OLR 系列）。
两者均为手工执行的一次性回补脚本（支持 ``--dry-run``）。

旁置说明 ``backfill_*_group_history.md`` 留在 ``services/`` 根上
（与前几组旁置文档处理一致），内容中的路径已同步。
本包只做文件归位，不改行为；``__init__`` 不做再导出。
"""
