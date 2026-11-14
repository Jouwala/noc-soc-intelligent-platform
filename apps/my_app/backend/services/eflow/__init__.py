"""eflow —— eFlow 域核心（API 客户端 / 告警 / 流程 / 工单关闭 / UME 关联）。

第二轮重构 §11-4 按域重组第 25 组（2026-11-14）：原先散在 ``services/``
根上的 11 个 eFlow 域文件收进本包：

- ``eflow_api_client``：eFlow API 客户端（已接入 ext_client 底座）
- ``eflow_alarm_query`` / ``eflow_alarm_service``：告警查询与服务
- ``eflow_data_mapper`` / ``sync_from_eflow_api``：数据映射与 API 同步
- ``eflow_flow`` / ``eflow_flow_rules`` / ``eflow_workflow_service``：流程与规则
- ``eflow_wo_close``：工单关闭
- ``eflow_site_update_task`` / ``eflow_ume_link``：站点更新与 UME 关联

既有的 ``eflow_5g/``、``eflow_assign/``、``eflow_fiber/``、``eflow_rules/``
子包不在本组范围。旁置文档 ``eFlow_API集成方案.md``、``eflow_alarm_query.md``
与 ``requirements_eflow_alarm_query.txt`` 留在 ``services/`` 根上，路径已同步。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.eflow.<模块>`` 路径。
"""
