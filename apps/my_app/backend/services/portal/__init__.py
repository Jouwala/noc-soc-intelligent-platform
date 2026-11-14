"""portal —— 门户对接域（门户鉴权 / 门户登录 / 门户 QA 控制）。

第二轮重构 §11-4 按域重组第 10 组（2026-11-14）：原先散在 ``services/``
根上的 3 个 ``portal_*`` 文件收进本包：``portal_auth``（门户鉴权）、
``portal_login_service``（门户登录）、``portal_qa_ctl``（门户 QA 控制）。

本包只做文件归位与 import 调整，不改行为；``__init__`` 不做再导出，
调用方一律写全 ``backend.services.portal.<模块>`` 路径。
"""
