"""ume_alarm —— UME 告警域（告警拉取客户端 / 配置 / 存储 / WhatsApp 推送）。

第二轮重构 §11-4 按域重组第 19 组（2026-11-14）：原先散在 ``services/``
根上的 4 个 ``ume_alarm_*`` 文件收进本包：``ume_alarm_client``
（告警拉取，已接入 ext_client 底座）、``ume_alarm_config``（配置）、
``ume_alarm_store``（落库）、``ume_alarm_whatsapp_service``（告警推送）。

既有的 ``ume_in/`` 子包是 UME KPI 接入域，不在本组范围。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.ume_alarm.<模块>`` 路径。
"""
