# PRTG 月报（2026-11-24T18:47:21.360393Z）

> 来源：my_app /api/prtg（PRTG 采集服务，60 秒轮询）
> 月份：2026-11；目标可用率：99.5%

## 月度 SLA
- 平均可用率：99.1397%
- 低于目标：4 条
- 当月总停机：— 小时
- Down 次数：0

## 低于目标清单
| 设备 / 传感器 | 可用率 | 当月停机(h) | Down | 口径 |
| --- | ---: | ---: | ---: | --- |
| AML TOKADEH1 / AML _TOKADEH_PING | 88.0666% | — | 0 | all_time_fallback |
| AML TOKADEH1 / (002) AML_TOKADEH_ISP Link | 96.8282% | — | 0 | all_time_fallback |
| AML TOKADEH1 / AML_TOKADEH_IPRAN LINK | 96.8299% | — | 0 | all_time_fallback |
| AML TOKADEH1 / AML_TOKADEH_LAN | 97.3049% | — | 0 | all_time_fallback |

## 异常规则命中（本月）
- composite：26
- above_baseline_200：17
- baseline_low_pending_review：9
- errors_discards_growth：3
- below_baseline_30：1
- low_traffic_30min：1

## 扩容建议
- 25 条链路未登记容量（network_link_capacity 为空），无法计算利用率；请用 `python -m backend.services.prtg.sla --set-capacity SENSOR:MBPS:manual` 补齐后再评估扩容。
- 可用率低于目标 99.5% 的链路 4 条，优先核查 Last Down 与维护窗口。

---
数据来源：my_app /api/prtg（PRTG 采集服务，60 秒轮询）；生成时间：2026-11-24T18:47:21.360393Z