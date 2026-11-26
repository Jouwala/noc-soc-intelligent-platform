# PRTG 日 / 周 / 月报样例（Issue #232）

> 2026-11-24 由 `backend/services/prtg/reports.py` 基于生产库生成；三档报表与
> `/api/prtg/overview`、`/api/prtg/sla` 使用同一套只读聚合，数字应逐项一致。
> 重新生成：
>
> ```bash
> cd apps/my_app
> python -m backend.services.prtg.reports --kind daily
> python -m backend.services.prtg.reports --kind weekly
> python -m backend.services.prtg.reports --kind monthly --month 2026-11
> ```

| 文件 | 内容 | 与看板一致的口径 |
| --- | --- | --- |
| `prtg-daily-20261124.md/.json` | 状态计数、异常链路、全网峰值/均值、容量待补全 | `overview.kpi` / `overview.trend` / `anomalies` |
| `prtg-weekly-20261124.md/.json` | 可用率排名（末 5）、区域质量、异常规则命中、容量 | `prtg_sla_daily` + `dashboard.links` |
| `prtg-monthly-20261124.md/.json` | SLA（可用率/低于目标/故障时长/Down）、异常命中、扩容建议 | `sla.month_report()`（#231） |

## 一致性抽查（2026-11-24 18:37 UTC）

- 日报：25 条、Up 17 / Unusual 8 / Down 0、平均可用率 99.1397%、异常 15、容量 0/25
  —— 与 `/api/prtg/overview?window=24h` 的 `kpi` 逐项相同。
- 月报：平均可用率 99.1397%、低于目标 4 条、当月停机 `null`（首日无基线）
  —— 与 `/api/prtg/sla?month=2026-11` 的 `summary` 相同。
- 报表正文带生成时间、数据年龄与来源；数字不在渲染层重算。

## WhatsApp 查询

机器人工具 `query_prtg_status` → my_app `GET /api/bot-data/prtg` → 返回
`generated_at`、`data_age_seconds`、`kpi`、`traffic`、`anomalies`、`capacity`、
`data_source`，由 `src/datadomains.js::renderPrtg` 原样渲染（带时间戳与来源），
不经 LLM 改写数字。

## 已知限制

- 周报/月报的可用率依赖 `prtg_sla_daily` 快照；首日只有累计回退值，标注
  `basis=all_time_fallback`。
- 容量利用率为空（0/25 已登记），扩容建议先要求补容量。
