"""remote —— 远端只读访问域（远端库读取 / FTP / 只读通道）。

第二轮重构 §11-4 按域重组第 11 组（2026-11-14）：原先散在 ``services/``
根上的 3 个 ``remote_*`` 文件收进本包：``remote_db_reader``（远端库
只读读取）、``remote_ftp``（远端 FTP）、``remote_readonly``（只读通道）。

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.remote.<模块>`` 路径。
"""
