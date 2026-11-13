"""avail —— 可用率域（县级 / MS / FLM 可用率 + 核对宽表重建）。

第二轮重构 §11-4 按域重组试点组（2026-11-13）：原先散在 ``services/``
根上的 6 个 ``avail_*`` 文件收进本包：

- ``avail_county_data`` / ``avail_county_aggregate`` / ``avail_county_service``：
  县级可用率三层（取数 / 聚合 / 对外编排）
- ``avail_ms_service`` / ``avail_flm_service``：MS / FLM 可用率矩阵
- ``avail_recon_sync``：可用率核对宽表重建（定时任务 ``avail_recon_sync`` 的
  入口 ``run_avail_recon_sync``；任务 id 不变）

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.avail.<模块>`` 路径。
"""
