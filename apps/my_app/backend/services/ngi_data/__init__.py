# -*- coding: utf-8 -*-
"""NGI 规划平台数据采集（工参 + 覆盖评估）。

模块划分：

| 文件 | 职责 |
| --- | --- |
| `ngi_client.py` | 登录（复用 `auth_manager`）、接口调用、覆盖导出的异步作业轮询与下载 |
| `ngi_param.py` | 工参 ZIP → 行字典；`SectorID` 缺失时的补号规则 |
| `ngi_coverage.py` | 覆盖网格 CSV（ZIP 内）→ 行字典 |
| `ngi_store.py` | PG `app` schema 建表、事务写入、同步日志 |
| `ngi_sync_service.py` | 任务入口 `run_ngi_param_coverage_sync()` |

数据源手册见 `docs/manual/04-task-system.md` 的「NGI 工参与覆盖数据同步」一节。
"""
