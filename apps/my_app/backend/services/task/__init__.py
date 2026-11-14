"""task —— 任务系统域（任务执行 / 配置注册 / 健康告警 / 作业僵尸判定）。

第二轮重构 §11-4 按域重组第 23 组（2026-11-14）：原先散在 ``services/``
根上的 7 个任务系统文件收进本包：

- ``task_runner``：任务执行入口
- ``task_configs_registry`` / ``task_config_files``：任务配置注册与配置文
- ``task_health_alert``：任务健康告警
- ``task_introspect`` / ``task_stats``：任务自省与统计
- ``job_staleness``：作业文件「僵尸 running」判定的共享工具

注意：任务的**唯一登记处**仍是 ``backend/task_registry.py`` 的
``TASK_SPECS``（不在本包内）；本包是被登记的执行业务层。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.task.<模块>`` 路径。
``task_runner`` 对 ``weixin_notifier`` 的相对导入已同步改绝对路径。
"""
