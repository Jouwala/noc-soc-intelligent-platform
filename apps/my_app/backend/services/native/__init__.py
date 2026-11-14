"""native —— 原生看板域（FineBI 原生看板目录数据 / 看板镜像同步）。

第二轮重构 §11-4 按域重组第 17 组（2026-11-14）：原先散在 ``services/``
根上的 2 个 ``native_*`` 文件收进本包：``native_catalog``（原生看板
目录数据，由根上 ``_gen_native_catalog.py`` 从原始 JSON 生成，生成器
的输出路径已同步指向本包）、``native_board_mirror_sync``（看板镜像同步）。
校验脚本 ``_verify_native_catalog.py`` 留在 ``services/`` 根上，导入路径已同步。

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.native.<模块>`` 路径。
"""
