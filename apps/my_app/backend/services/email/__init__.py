"""email —— 邮件域（发信通道 / 收件人 / 邮箱恢复 / 覆盖检查 / 邮件报表任务）。

第二轮重构 §11-4 按域重组第 20 组（2026-11-14）：原先散在 ``services/``
根上的 6 个邮件域文件收进本包：

- ``email_notifier``：统一发信通道
- ``email_recipient_store``：收件人存储
- ``email_recovery``：邮箱凭据恢复
- ``email_task_coverage``：邮件任务覆盖检查
- ``kpi_email_report``：KPI / SP / RAN 日报邮件任务
- ``liberia_email_report``：Liberia 站点宕报告邮件任务

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.email.<模块>`` 路径。
``task_runner`` 对 ``email_notifier`` 的相对导入已同步改绝对路径。
"""
