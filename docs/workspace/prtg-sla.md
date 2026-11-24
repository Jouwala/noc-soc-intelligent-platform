# PRTG SLA 与容量利用率（Issue #231）

> 基准日期：2026-11-24　|　执行位置：MSNOC1　|　实现：`backend/services/prtg/sla.py`
> 页面：`/myapp/?page=prtg_links` 的「SLA 与容量」页签；接口 `GET /api/prtg/sla`、`GET /api/prtg/capacity`。

## 1. 口径结论（书面）

| 问题 | 结论 | 依据 |
| --- | --- | --- |
| 可用率分母是什么 | PRTG 的**监控覆盖时间增量**（相邻日快照 `updowntotal` 之差），不是自然日历时间 | `getsensordetails.xml` 返回 `uptimetime` / `downtimetime` / `updowntotal`，2721 实测 `99.8775%` + `28 h 9 m` + `958 d`，与 PRTG 页面一致；Paused 期间 `updowntotal` 不增长，分母只按实际覆盖算 |
| 维护窗口/Paused 是否计入停机 | **不计入**：Paused 期间不累计 `downtimetime`，覆盖率（updowntotal）也不增长 | 用 PRTG 累计值直接出报表，天然排除维护窗口；若两次快照间 `updowntotal` 增量明显小于自然时长，报表用实际覆盖分钟做分母并标注 |
| 月故障时长怎么算 | 每天快照累计 `downtimetime`，月内相邻快照差分求和 | PRTG 不提供逐日停机历史；首日无前序快照时该日不计（`basis=all_time_fallback`） |
| Down 次数怎么算 | 月内直接按 `prtg_sensor_events` 统计 | 日快照只统计到快照时刻，月报不依赖它，避免每天丢失快照后的晚间事件 |
| 累计可用率能否直接当本月 | 不能。累计值是自传感器创建以来的；当月无快照时仅作回退展示，并在「口径」列标「累计回退」 | `prtg_sla_daily` 的 `downtime_delta_minutes` 为空即回退 |
| 容量从哪来 | PRTG **不提供**接口速率（实测 `columns=speed` 返回 `"Not found"`），只能人工登记 `network_link_capacity`（`manual`）或未来接 SNMP `ifSpeed` | `sla.set_capacity()` / CLI `--set-capacity` |
| 无容量怎么显示 | 利用率返回 `null`，页面显示「待补全」；**不按 0 或猜测值算百分比** | `_capacity_rows()` |
| 利用率公式 | `max(In/Cin, Out/Cout)`，取 24h/7d 峰值与 30d P95；阈值 70 关注 / 80 Warning / 90 Critical | 与 #226/#228 口径一致，不用 Total/容量（会翻倍） |

## 2. 数据与实现

- 迁移 `0011_prtg_sla`（+ `0012_prtg_sla_coverage` 补累计覆盖列）：
  - `prtg_sensors` 补 `uptime_minutes` / `downtime_minutes` / `updown_total_minutes` / `updown_since`；
  - 新表 `prtg_sla_daily`（唯一键 `sensor_objid + day`）：当天快照、累计停机、累计覆盖（`updown_total_minutes_cum`）、当日差分、Down 次数、覆盖分钟、当天可用率。
- 采集线程每天 `snapshot_day()` 一次（状态键 `sla`），同一天重跑覆盖更新（幂等）。
- `month_report()` 输出：
  - `summary`：链路数、低于目标数、平均可用率、当月总故障时长、容量登记数、快照天数；
  - `links`：按可用率升序，含当月/全时可用率、当月故障时长、Down 次数、Last Down、口径；
  - `below_target`、`region_ranking`（按映射表 region 平均）；
  - `capacity`：25 条链路全部列出，无容量的 `capacity_known=false`、利用率 `null`。
- 容量登记 CLI：

```bash
cd apps/my_app
python -m backend.services.prtg.sla --set-capacity 2721:10000:manual
python -m backend.services.prtg.sla --month 2026-11
python -m backend.services.prtg.sla --snapshot
```

## 3. 生产首次快照（2026-11-24 17:47 UTC）

- 25 条链路，快照 25 行（首日 `basis=all_time_fallback`，尚无月差分）。
- 平均可用率 **99.1397%**，低于 99.5% 目标 **4 条**（Ping/AML 与两条低可用率链路），与 PRTG 页面口径一致。
- 当月总故障时长：`null`（首日无基线，不猜）；从 11-25 起快照差分后出数。
- 容量：`0 / 25` 已登记 → 利用率全部 `null` / 待补全。

## 4. 已知限制

- 月故障时长需要至少两天的日快照；上线当天只能给累计可用率回退值。
- 若某传感器被删除/重建，累计停机计数会回退，`snapshot_day` 检测到负增量会跳过该日（`reset_skipped`），不产生负数故障时长。
- 区域排名目前 24 条为 `Unknown`（站点映射待人工确认，见 #226）。
- 容量需人工补；未登记前 SLA 页签的容量表全部显示「待补全」，这是设计行为而非数据缺失报错。
