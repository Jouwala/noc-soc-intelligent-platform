# PRTG 采集与核心链路健康度（#225–#228）

> 基准日期：2026-11-24　|　执行位置：MSNOC1（`D:\02_olbr_portal`）　|　PRTG：`192.168.15.244`
> 相关 Issue：#225 通道字典、#226 映射与盲区、#227 采集服务、#228 健康度看板。
> 本文件是**现行口径**；接口与实现以代码为准，通道语义以
> `docs/workspace/prtg-channel-dictionary.md` 为准。

## 1. 数据流

```
PRTG 14.2（只读账号 control1，Passhash）
  ├─ /api/table.json?content=sensors       每分钟：状态 + 主通道值
  ├─ /api/table.xml?content=channels&id=…  每分钟：每传感器全部通道（XML 保留重复 lastvalue）
  ├─ /api/getsensordetails.xml             每 5 分钟：Uptime/Downtime/Last Down/扫描间隔
  ├─ /api/table.json?content=devices|sensors 每小时：目录
  └─ /api/historicdata.csv                 回填：全局令牌桶 ≤5 次/分钟，一天一窗
        ↓
backend/services/prtg/collector.py（单实例、单线程）
        ↓
PostgreSQL 独立表（不写 realtime_alarm / history_alarm）
        ↓
/api/prtg/overview|links|links/{id}|collector/status（只读）
        ↓
my_app 页面 ?page=prtg_links（门户目录卡片 PRTG Link Health）
```

## 2. 表与保留

| 表 | 内容 | 保留 |
| --- | --- | --- |
| `prtg_devices` / `prtg_sensors` / `prtg_sensor_channels` | 目录（每小时 upsert） | 长期 |
| `prtg_sensor_status` | 状态采样（**仅状态变化 + 每小时心跳**，避免 25 行/分钟） | 长期 |
| `prtg_channel_samples` | 60 秒原始采样（每传感器每通道一行，唯一键幂等） | 90 天（`PRTG_RAW_RETENTION_DAYS`） |
| `prtg_historical_samples` | historicdata 回填原始行 | 90 天 |
| `prtg_channel_samples_5m` / `_1h` | 5 分钟 / 小时聚合（avg + max + 样本数） | 1 年 / 1000 天 |
| `prtg_sensor_events` | Up/Unusual/Down 变化事件 | 长期 |
| `prtg_sensor_mapping` / `prtg_blindspot_device` | #226 映射与盲区（人工维护） | 长期 |
| `network_link_capacity` | 利用率分母（PRTG 没有；来源只允许 `snmp_ifspeed` / `manual`） | 长期 |
| `network_anomaly_events` | 门户自建异常事件（#229 起使用） | 长期 |
| `prtg_sync_state` | 每类作业的状态、断点、最近错误、数据新鲜度 | 长期 |

迁移：`apps/my_app/alembic/versions/0008_prtg_collector.py`（`0007` 之后）。

## 3. 口径（前端只展示，不重算）

- 流量取通道**速率**：`value_num` 原始 byte/s，展示 Mbps = byte/s × 8 ÷ 1e6。
- **Errors / Discards / Unicast 是区间速率**（count/s），不做差分（#225 实测 120 行非单调递增）。
- **利用率只在有容量时算**，且 `max(In/Cin, Out/Cout)`；绝不用 Total/容量（会翻倍）。
  容量缺失返回 `null`，界面显示「待补全」——不得按 0 或猜测值算利用率。
- 数据新鲜度 = `now - prtg_sync_state.live.last_success_at`；超过 5 分钟界面必须常驻提示
  「数据过期」，不得把旧值当实时值。
- 时区：PRTG 服务器为 **UTC**（2026-11-24 用 `lastcheck` 序列日与本机 UTC 比对实测），
  库里一律 naive UTC，接口补 `Z`。

## 4. 运行与开关

- 常驻线程默认**关闭**：`backend/.env` 设 `PRTG_COLLECTOR_ENABLED=1` 才启动。
  默认关闭是为了让 CI / 冒烟 / 单测环境不去连 PRTG 内网。
- 其他可调：`PRTG_POLL_SECONDS`（默认 60）、`PRTG_BACKFILL_PER_MINUTE`（默认 5）、
  `PRTG_RAW_RETENTION_DAYS`（默认 90）；地址 `PRTG_BASE_URL` 优先于
  `backend/services/prtg_config.json`。
- 凭据：设置页「PRTG 流量 / 传感器采集 (192.168.15.244)」组；口令字段是 **Passhash**，
  不是登录密码。代码与日志只出现键名，不落值。
- 失败：PRTG 不可达时本轮把 `error` 写进 `prtg_sync_state`，线程不退出，下一轮继续；
  恢复后 `last_success_at` 自动更新，不需要重启服务。

## 5. 接口

| 端点 | 说明 |
| --- | --- |
| `GET /api/prtg/overview?window=5m\|24h\|7d\|30d` | KPI（25 条 / Up / Unusual / Down / 平均可用率 / 异常链路数）+ 全网速率趋势 + 数据新鲜度 |
| `GET /api/prtg/links?window=…` | 25 条链路总览（In/Out/Total、Unicast、Errors、Discards、可用率、Last Down、容量/利用率、`data_quality`） |
| `GET /api/prtg/links/{sensor_id}?window=…` | 单链路详情：通道时序、状态事件、全部站点映射 |
| `GET /api/prtg/collector/status` | 五类作业的最近运行/成功/错误（排障用，无凭证） |

窗口 → 数据源：`5m` 用 60 秒原始（近 1 小时）、`24h` 用 5 分钟聚合、`7d`/`30d` 用小时聚合。

## 6. 排障

1. 页面全空 / 全「—」：先看 `GET /api/prtg/collector/status`。
   `live.status=never` → 线程没启动（检查 `PRTG_COLLECTOR_ENABLED`）；
   `live.status=error` → 看 `error`（网络不可达 / Passhash 失效），线程会自动重试。
2. 有状态无流量：`data_quality.traffic_available=false`，说明最近 15 分钟没有采样；
   检查 `PRTG_POLL_SECONDS` 与 PRTG 侧传感器是否 Paused。
3. 利用率全是「待补全」：`network_link_capacity` 没有该传感器行——这是**正常初始状态**，
   PRTG 不提供容量，需要人工补 `snmp_ifspeed` 或设备配置值。
4. 站点列大量 `Unknown`：PRTG 25 个传感器是核心/ISP 链路，553 个拓扑站是客户站点，
   命名体系不同；`mapping_seed.json` 首轮只自动命中 MATADI-SW → Matadi，其余等人工确认
   （`match_status=verified` 后重跑灌库不会覆盖人工修订）。

## 7. 已知限制

- **Ping 传感器通道未入字典**：`AML _TOKADEH_PING` 只有 `Ping Time`（msec）+ Downtime，
  不在 `channels.py` 的 11 个流量通道里；采集端按原始通道名入库，语义等 #229 再做。
- **2026-11-24 实测**：25 个传感器中 24 个 SNMP Traffic 64bit + 1 个 Ping；
  状态 17 Up / 8 Unusual / 0 Down（时点快照，引用要注明时间）；全网总速率约 18–19 Gbps。
- 历史回填从「现在」向前按天推进，90 天全部补完约需 7–8 小时（5 次/分钟 × 25 传感器），
  期间不影响实时轮询；断点存在 `prtg_sync_state.backfill.cursor`。
