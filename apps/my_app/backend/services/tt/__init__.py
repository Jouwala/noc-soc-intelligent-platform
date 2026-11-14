"""tt —— TT 告警域（告警触发 / 渲染 / 邮件与 WhatsApp 推送 / MTTR）。

第二轮重构 §11-4 按域重组第 26 组（2026-11-14）：原先散在 ``services/``
根上的 13 个 TT 域文件收进本包：

- ``tt_alarm_config`` / ``tt_alarm_store`` / ``tt_alarm_sources``：配置、存储与来源
- ``tt_alarm_job`` / ``tt_alarm_trigger``：告警作业与触发
- ``tt_alarm_render`` / ``tt_alarm_message`` / ``tt_alarm_mail_body``：渲染与报文
- ``tt_alarm_mailer`` / ``tt_alarm_whatsapp_service``：邮件与 WhatsApp 双通道推送
- ``tt_exclude``：排除清单
- ``tt_mttr_report`` / ``mttr_tt_sync``：MTTR 报表与宽表重建

旁置文档 ``tt_mttr_report.md`` 留在 ``services/`` 根上，路径已同步。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.tt.<模块>`` 路径。
"""
