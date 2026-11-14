"""credential —— 凭据域（凭据存储与连通性测试）。

第二轮重构 §11-4 按域重组第 15 组（2026-11-14）：原先散在 ``services/``
根上的 2 个 ``credential_*`` 文件收进本包：``credential_store``
（凭据存储，全站凭据读取入口）、``credential_tester``（凭据连通性测试）。

旁置文档 ``credential_store.md`` 留在 ``services/`` 根上，路径已同步。
本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.credential.<模块>`` 路径。
"""
