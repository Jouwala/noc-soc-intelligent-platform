# PRTG 日志、异常原因与设备事件（P0）

> 基准日期：2026-11-26　|　分支：`agent/quick-prtg-p0-logs`　|　迁移：`0014_prtg_logs_incidents`
> 实现：`backend/services/prtg/logs.py`、`incidents.py`；模型 `backend/models/prtg_logs.py`
> 页面：`/myapp/?page=prtg_links`（状态列原因、「24h Down / ICMP 超时 · MTTR」列、设备事件卡片、详情「PRTG 日志」）

## 1. 为什么做（摸底依据：`TMP/prtg_survey/out/`，只读）

| 现象 | 实测 |
| --- | --- |
| PRTG 日志只保留约 30 天 | `content=messages` 共 2963 条（10-27 → 11-26），`start ≥ 10000` 全空；`treesize=1000000` 是假总数 |
| 日志是唯一带原因的数据 | 原因三类：ICMP 超时 942、SNMP 无响应 789、数值变化 1232（30 天） |
| 一台设备 = 多条「链路故障」 | 11-02 10:26:57–10:27:33 NEXUS 3172 的 16 个传感器约每 3 秒依次 Down |
| Unusual 没有原因 | 传感器 `message` 列有 PRTG 基线判定原文，此前没入库 |

## 2. 口径

- **时间**：`datetime_raw`（Excel 序列日）→ naive UTC，四舍五入到秒；PRTG 服务器时钟 = UTC（getstatus `Clock` 与本机 UTC 一致）。
- **去重**：`sha1(objid | datetime_raw | status_raw | message)` 唯一；重叠翻页、重复运行都幂等。
- **增量**：新→旧翻页，页大小 2000；停在「本页最老 < 已入库最新 − 10 分钟」/ 空页 / 不满页 / 10 页硬上限。首次运行没有游标，一直翻到空页（= 一次存下 PRTG 现存的约 30 天）。
- **原因分类**（`logs.classify_message`，命中即停）：`icmp_timeout`（ICMP error / Request timed out）→ `snmp_no_response`（SNMP error / No response）→ `unusual_low` → `unusual_high` → `paused` → `value`（数字 + msec/kbit/s/%）→ `other`。PRTG 改文案时只改这里。
- **派生指标**（`logs.compute_sensor_stats`）：`down_count_24h`、`icmp_timeouts_24h`（Ping 只有延迟通道，用它代替丢包）、`mttr_minutes_7d`（Down → 同传感器下一次 Up 的平均分钟数；没有完整 Down→Up 对时为 null）。日志采集从未成功时三项都返回 null，不是 0。
- **保留**：`PRTG_LOG_RETENTION_DAYS`，默认 730 天；不跟随 90 天原始采样清理。

## 3. 设备事件合并（`incidents.build_incidents`）

1. 只用日志（秒级），按设备名分组（日志没有设备 objid；`device_objid` 从 `prtg_devices` 按名补）。
2. 新 Down 并入当前事件：当前事件还有传感器 Down，**或**距上次 Down ≤ 120 秒；否则另起。
3. 涉及的传感器全部 Up → 事件结束；结束后 120 秒内又 Down → 重开同一事件，`flaps` +1。
4. 类型：事件窗口（含 Down 前 120 秒）有 ICMP 超时或涉及 Ping 传感器 → `device_unreachable`；只 1 个传感器 → `single_link`；否则 `snmp_mgmt`（设备没 Ping 传感器时分不清管理面与整机，`evidence.has_ping_sensor=false` 写明）。
5. 每 5 分钟从近 35 天日志重算，只 upsert 近 30 天开始的事件（`device_name + started_at` 唯一）。

与 `anomaly.py` 规则事件不替代、不对账：「异常链路数」按传感器去重，「设备事件」按设备合并。

## 4. 运行

- runner：启动 20 秒后首跑，之后每 300 秒 `sync_messages` → 成功才 `correlate`；状态键 `messages` / `incidents`（`/api/prtg/collector/status` 可见）。
- 开关：`PRTG_MESSAGES_SYNC_ENABLED=0` 关闭日志采集与合并（需重启 backend），其余采集不受影响。
- 兜底快照：`TMP/prtg_survey/out/prtg_messages_20261126_0910.csv`（摸底时导出的 30 天）。上线时 PRTG 里还在的部分由首跑拿全，**不提供 CSV 导入**（不做第二入口）。

## 5. 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/prtg/logs?sensor_id=&device=&reason=&since=&until=&limit=` | 新→旧；`has_data=false` = 采集没跑过 |
| `GET /api/prtg/incidents?window=24h\|7d\|30d&device=&sensor_id=` | 未结束事件 `ended_at` / `duration_seconds` 为 null |
| `GET /api/prtg/links`（新增字段） | `last_message`、`unusual_reason`、`down_count_24h`、`icmp_timeouts_24h`、`mttr_minutes_7d` |
| `GET /api/prtg/links/{id}`（新增字段） | `last_message`、`last_message_at`、`unusual_reason` |
| `GET /api/prtg/overview`（新增字段） | `incidents_7d`、`top_noisy_device` |

## 6. 已知边界

- 当前账号只能看到 25 个传感器（3 台设备）；其余 39 台设备的传感器 `Access Denied`，日志里也没有它们。
- `message` 列只在实时轮询时刷新；目录同步不带该列时不覆盖已存原因。
- 回滚：`alembic downgrade 0013_network_topology`（删两表两列，旧表旧列不动）。
