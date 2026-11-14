"""site —— 站点域（站点可用性 / 站点宕机 / 语音数据合并与探针）。

第二轮重构 §11-4 按域重组第 21 组（2026-11-14）：原先散在 ``services/``
根上的 5 个 ``site_*`` 文件收进本包：

- ``site_availability_service``：站点可用性
- ``site_outage_service`` / ``site_outage_session``：站点宕机检测与会话
- ``site_voice_data_merge`` / ``site_voice_data_probe``：语音数据合并与探针

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.site.<模块>`` 路径。
"""
