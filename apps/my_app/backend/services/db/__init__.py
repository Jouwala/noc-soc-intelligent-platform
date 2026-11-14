"""db —— 数据库域（数据新鲜度巡检 / PG schema 巡检 / 远程写入客户端）。

第二轮重构 §11-4 按域重组第 18 组（2026-11-14）：原先散在 ``services/``
根上的 3 个数据库域文件收进本包：``db_freshness_check``（数据新鲜度
巡检）、``pg_schema_freshness``（本机 PG schema 水位巡检）、
``db_writer_client``（SQLite 远程写入客户端，已接入 ext_client 底座）。

旁置文档 ``db_freshness_check.md`` / ``pg_schema_freshness.md`` 与
``requirements_pg_schema_freshness.txt`` 留在 ``services/`` 根上，路径已同步。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.db.<模块>`` 路径。
"""
