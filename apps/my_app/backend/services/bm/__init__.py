"""bm —— BM 基准测速域（Meta / Speedtest 两套口径与服务）。

第二轮重构 §11-4 按域重组第 4 组（2026-11-14）：原先散在 ``services/``
根上的 4 个 ``bm_*`` 文件收进本包：

- ``bm_meta_caliber`` / ``bm_meta_service``：Meta 基准口径与服务
- ``bm_speedtest_caliber`` / ``bm_speedtest_service``：Speedtest 口径与服务

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.bm.<模块>`` 路径。
"""
