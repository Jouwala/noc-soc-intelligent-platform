# -*- coding: utf-8 -*-
"""PRTG 采集服务包（Issue #225：PRTG API 验证与通道语义字典）。

通道语义字典（channels）、响应解析器（parsing）为纯函数/常量；
客户端骨架（client）带超时与退避重试，凭证只从环境变量读。
真实 PRTG 联调在 MSNOC1 上执行（VM 访问不到 PRTG 内网），
见 docs/workspace/prtg-channel-dictionary.md（MSNOC1 直连复核 2026-11-24 已完成）。
"""
from backend.services.prtg import channels, client, parsing

__all__ = ["channels", "client", "parsing"]
