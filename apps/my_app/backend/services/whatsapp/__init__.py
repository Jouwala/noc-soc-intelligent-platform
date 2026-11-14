"""whatsapp —— WhatsApp 发送与知识库域（发送通道 / 任务认领 / KB 同步 / 登录）。

第二轮重构 §11-4 按域重组第 2 组（2026-11-13）：原先散在 ``services/``
根上的 7 个 ``whatsapp_*`` 文件收进本包：

- ``whatsapp_service``：主发送通道（任务 ``whatsapp_send`` 的入口）
- ``whatsapp_ext_service`` / ``whatsapp_send_baileys_service``：扩展发送与 Baileys 通道
- ``whatsapp_dispatch`` / ``whatsapp_job_claim``：投递调度与任务认领
- ``whatsapp_kb_service``：知识库同步（任务 ``whatsapp_kb_sync_*``）
- ``whatsapp_login_service``：登录会话

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.whatsapp.<模块>`` 路径。
``wa_notify_service``（WhatsApp 通知发送）已于第 27 组补遗并入本包。
"""
