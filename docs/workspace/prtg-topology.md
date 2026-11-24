# 门户流量拓扑（Issue #230）

> 基准日期：2026-11-24　|　实现：`backend/services/prtg/topology.py`、`routers/network_topology.py`
> 前端：`apps/portal/src/components/topology/TrafficTopologyView.tsx`（`NetworkTopologyView` 的并列视图）

## 1. 建模口径

- **Sensor ≠ Interface ≠ Link ≠ Edge**：一条 `network_links` 绑定一个 PRTG 传感器；
  两端节点由设备名 + 传感器名解析（`(10152) to CSQUARED` → 对端 CSQUARED）。
  一条链路只画一条边；两端容量分方向存 `capacity_ab_mbps` / `capacity_ba_mbps`。
- 节点坐标是 **0–1000×620 画布相对坐标**（不是经纬度）；自动布局按类型分圈
  （device 内圈、customer 中圈、peer 中圈、unknown 外圈），保证 y 落在 0–620。
  `PUT /layout` 支持保存人工坐标（`pinned=true`）；**门户页面当前只读、未接拖动保存**，
  拖动编辑属于 Issue #230 Phase 2 待办。
- 映射流程：`discovered`（自动发现）→ `candidate`（自动匹配待确认）→ `verified`（人工确认）。
  `network_node_aliases` 与 `network_link_layouts` 表已建但**尚无写入路径/API**（别名与折线偏移
  留待 Phase 2）；当前链路折线由前端按两端坐标绘制。

## 2. 表（迁移 `0013_network_topology`）

| 表 | 内容 |
| --- | --- |
| `network_topology_nodes` | 节点（device/peer/customer/unknown），坐标 0–1000、pinned |
| `network_links` | 链路（sensor_objid 唯一）、两端节点、分方向容量、类型、映射置信度 |
| `network_node_aliases` | 节点别名（人工确认用） |
| `network_link_layouts` | 链路 bend_offset（防重叠） |

节点/链路由 `ensure_topology()` 从 PRTG 传感器幂等生成：重复调用只补新节点/链路，
**保留人工坐标、容量与置信度**。

## 3. 容量与利用率（硬口径）

- PRTG **不提供**接口容量（实测 `columns=speed` 返回 `Not found`）；容量只来自
  `network_link_capacity`（人工 `manual`，或后续 SNMP `snmp_ifspeed`）。
- 方向语义：source_node 是传感器所在设备 A，target 是对端 B；PRTG `Traffic In` 是进入 A（B→A），
  配 `capacity_ba_mbps`；`Traffic Out` 是 A→B，配 `capacity_ab_mbps`。
- 利用率 `U = max(In/Cba, Out/Cab)`（分方向容量）；容量缺失 → `utilization_pct=null`，
  前端显示「待补全 + 容量未知」，**绝不按 0 或猜测值算百分比**。
- 分档（前端）：0–1 / 1–10 / 10–25 / 25–40 / 40–55 / 55–70 / 70–85 / 85–100（紫→红），
  >100% 标 `OVER CAPACITY`；无容量走中性色 + 线宽按绝对流量分级。
- 每条链路返回 `data_quality{capacity_known, traffic_available, sample_age_seconds,
  mapping_confidence}`；PRTG 不可达时页面显示最后数据并标过期，不伪造实时值。

## 4. 接口

| 端点 | 说明 |
| --- | --- |
| `GET /api/network-topology/traffic?period=live\|1h\|24h\|7d` | 节点 + 链路 + KPI + 数据质量 |
| `GET /api/network-topology/nodes` / `/links` | 节点 / 链路清单 |
| `GET /api/network-topology/links/{id}/history?period=` | 单链路 In/Out 时序 |
| `GET/PUT /api/network-topology/layout` | 节点坐标读取 / 保存（PUT 仅 admin；页面暂只读） |
| `GET/PUT /api/network-topology/mappings` | 容量、链路类型、映射置信度读取 / 更新（PUT 仅 admin；非法值 4xx 且不落库） |

`PUT /mappings` 同步镜像到 `network_link_capacity`，保证 SLA 月报与拓扑用同一份容量。

## 5. 2026-11-24 开发环境实测（部署后需复测）

- `ensure_topology()`：过滤 Ping 后为 24 条流量链路 + 24 个节点；Ping 不再被换算成 0.001 Mbps 假链路。
  状态 Up/Unusual/Down/Unmonitored 分开计数，未监控不显示成 Up。
- 容量 0/25 已登记 → 全部 `utilization_pct=null`（页面「待补全」）；
  `over_capacity=0`。
- 采样年龄约 82 秒（60 秒轮询 + 5 分钟聚合窗口），`traffic_available=true`。

## 6. 已知限制

- 对端解析：`(10152) to CSQUARED` 这类可解析为 peer；OLIB 的
  `(369098753) port-channel2` / `======= PEER-LINK =======` 这类接口名只能识别为
  interface/unknown 节点，需要人工用 `network_node_aliases` 与映射管理确认。
- 容量为 0 时无法演示利用率色阶；Phase 1 的 Mock 数据覆盖了有/无容量两种形态。
- 页面未实现拖动保存/别名/折线偏移/60 秒自动刷新；`PUT` 接口已具备但仅 admin 可调用。
- `update_mappings` 只把 AB 方向容量镜像到 `network_link_capacity`（#231 SLA 仍是单容量口径），
  BA 方向只存在于拓扑表；非对称容量的 SLA 月报需等 #231 扩展。
- 拓扑编辑 UI（增删节点/链路、绑定传感器、确认上下游、拖动保存）尚未实现；
  当前只有接口层 `PUT /mappings`、`PUT /layout`（admin），完整管理面板留待 Issue #230 Phase 2。
