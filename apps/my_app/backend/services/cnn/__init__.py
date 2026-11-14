"""cnn —— CNN 核心网 UME KPI 域（License 占用 / 5 分钟粒度同步 / Free Rating 图表邮件）。

第二轮重构 §11-4 按域重组第 13 组（2026-11-14）：原先散在 ``services/``
根上的 3 个 CNN 域文件收进本包（历史文件名 ``ccn_`` / ``cnn_`` 两种
前缀并存，指的是同一个 CNN 域，文件名不改）：

- ``ccn_ume_kpi_service``：CNN UME KPI 同步（任务 ``cnn_kpi_sync``）
- ``cnn_ume_kpi_5mins_service``：5 分钟粒度同步（``cnn_kpi_sync_5mins``）
- ``ccn_free_rating_chart_service``：Free Rating 图表邮件

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.cnn.<模块>`` 路径。
"""
