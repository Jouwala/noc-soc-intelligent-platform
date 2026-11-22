# NOC 统一网络拓扑中心 —— 数据契约（Phase 0）

> 适用范围：`frontend/src/pages/nocTopology/`（NOC Network Topology Center 1.0）
> 基准日期：2026-11-19
> 状态：**已按现网接口实测核对**（2026-11-19 17:53 起对 `127.0.0.1:7038` 取数）

本文件是「统一数据模型」与三个来源之间的**契约**。任何字段的上游出处、缺失值含义、
以及**不得越界推断**的红线都写在这里。改适配器前先改本文件，反之亦然。

---

## 一、总原则

1. **只做展示层归一，不改上游口径。** 三个适配器只把各自的原生结构翻译成
   `NocNode` / `NocEdge`，不重算利用率、不改告警级别、不合并方向、不猜链路关系。
2. **缺数据不是 0。** 上游没有采样、端口不在 KPI 覆盖内、时间槽没有记录，一律
   映射为 `null` + `trafficAvailable=false`，界面显示 `N/A` 并用中性灰虚线。
3. **设备身份 = `source + nativeId`。** 不按站名、不按坐标距离合并跨系统设备。
   只有 IP 精确相等、或 `siteId` 精确相等、或**人工确认过的映射表**，才允许在
   Phase 4 建立关联；1.0 不做任何自动跨域关联。
4. **每个来源独立成败。** 取数走 `Promise.allSettled` 语义，每个来源各自记录
   `state / fetchedAt / sampledAt / warnings`；一个来源失败不得清空其他来源。

---

## 二、统一模型字段与出处

### 2.1 `NocNode`

| 字段 | IPRAN 出处 | MW 出处 | OLIB 出处 |
| --- | --- | --- | --- |
| `key` | `ipran:${node.id}` | `mw:${node.ip}` | `olib:${site.index}` |
| `nativeId` | `node.id`（UME 网元 UUID） | `node.ip` | `site.index`（SITES 下标） |
| `name` | `node.name` | `node.name` / `node.site` | `site.name` |
| `ip` | `node.ip` | `node.ip` | 无 |
| `siteId` | 无（IPRAN 响应不带主数据 siteId） | `node.site_id` | `site.siteId`（GPS 映射表命中时） |
| `latitude/longitude` | `node.lat/lon`（`null` = 未定位） | `node.lat/lon` | `site.lat/lng`（`located=false` 时为 `null`） |
| `deviceType` | `tier` 映射：0→`isp`、1→`core`、2→`aggregation`、3→`access` | 固定 `microwave` | `core`→`core`、`isp`→`isp`、其余→`site` |
| `layer` | 当前 UME 层 key（`ipran`/`ipcore`/`metro`/`dwdm`） | `meta.granularity`（`1day`/`15min`） | `null` |
| `status` | 由 `up` + `crit/major/minor` 归并，见 §3.1 | 原生四级 `status` 原样映射，见 §3.2 | 固定 `static` |
| `alarms` | `crit/major/minor/warn` 原值 | 无（MW 告警在链路级） | 无 |
| `degree` | `node.deg` | `node.degree` | 由参与绘制的链路数计算 |

**IPRAN 节点 `site` 只作为展示用的站名，不当作 siteId。** 上游没有给主数据站点 ID，
站名还可能是近似匹配（`meta.geoFuzzyPairs`），不能拿来做跨系统关联。

### 2.2 `NocEdge`

| 字段 | IPRAN 出处 | MW 出处 | OLIB 出处 |
| --- | --- | --- | --- |
| `key` | `ipran:${s}->${t}#${i}` | `mw:${source}->${target}#${i}` | `olib:core:${id}` / `olib:fiber:${id}` |
| `sourceNode` / `targetNode` | `edge.s` / `edge.t` 对应节点 key | `edge.source` / `edge.target` | `link.a.index` / `link.b.index` |
| `medium` | `fiber` | `microwave` | 骨干关系 `schematic`；PDF 数字化链路 `fiber`（**仍标 `schematic=true`**） |
| `capacityMbps` | `edge.gb × 1000`（`gb` 单位为 Gbps；`null` → `null`） | `edge.capacity`（原值即 Mbps） | `null` |
| `txUtilization` | `edge.txUtil`（无 `traffic=1` 时为 `undefined` → `null`） | `kpi_backed ? edge.tx_util : null` | 恒 `null` |
| `rxUtilization` | `edge.rxUtil` | `kpi_backed ? edge.rx_util : null` | 恒 `null` |
| `status` | `down>0`→`down`；`crit>0`→`critical`；`major>0`→`major`；否则 `ok` | 原生 `healthy/minor/major/critical/unknown` 映射 | 计划链路 `planned`，其余 `static` |
| `trafficAvailable` | `txUtil != null 或 rxUtil != null` | `kpi_backed === true`（**不看数值是否为 0**） | 恒 `false` |
| `planned` | `false` | `false` | `fiberLink.dash === true` |
| `schematic` | `false` | `false` | **恒 `true`**（见 §4） |
| `ports` | `edge.ports`（最多 12 条链路名） | 由 `checkpoints` 的端口名派生 | `[link.type]` |
| `checkpoints` | `edge.utilPorts`（逐端口方向利用率） | `edge.checkpoints`（`cp/ncp/tx_t/tx_u/rx_t/rx_u`） | `null` |
| `history` | **不提供**（见 §5） | `edge.history`（日粒度全部日期 / 实时最近 96 槽） | `null` |

### 2.3 `SourceMetadata`

| 字段 | IPRAN | MW | OLIB |
| --- | --- | --- | --- |
| `state` | 成功且有节点 → `connected`；有 `warnings` → `partial`；`meta.stale` → `stale`；异常 → `unavailable` | 同左（`partial` 看 `unknown > 0`） | 文件读到 → `static`；失败 → `unavailable` |
| `fetchedAt` | `meta.fetchedAt` | `meta.generated_at` | 文件 `generatedAt` |
| `sampledAt` | `meta.traffic.sampledAt`（仅 `traffic=1`） | `meta.selected_date` | 无 |
| `granularity` | 固定 `15min`（KPI 表为 15 分钟表） | `meta.granularity` | 无 |
| `coverage` | `traffic.matchedEdges / traffic.totalEdges` | `kpis.links / kpis.physical_links` | 无 |
| `trafficLoaded` | `traffic=1` 为 `true`，否则 `false` | 恒 `true` | 恒 `false` |
| `warnings` | `meta.warnings` 原样 + 本层推断提示 | `unknown` 链路数、无坐标节点数 | 跳过的链路数、`stale` 标记 |

**覆盖率的分子分母都只统计 `trafficLoaded = true` 的来源。** IPRAN 不带 `traffic=1`
时 `txUtil/rxUtil` 是 `undefined`，那是「没取」，不是「没覆盖」；把它算进分母会把
覆盖率从 78% 压到 69%。OLIB 没有流量能力，永远不进分母。

---

## 三、状态归并规则

### 3.1 IPRAN

```
up === 0                       -> down
crit > 0                       -> critical
major > 0                      -> major
minor > 0 || warn > 0          -> minor
其余                            -> ok
```

⚠ `meta.warnings` 里的 `alarm_saturated`（2026-11-19 实测 74/93 个网元带紧急告警）
说明**告警级不能当主着色**：80% 节点会同时变红，失去区分度。因此逻辑视图默认按
**在线状态 + 链路状态**着色，告警只做节点角标；这与 IPRAN 专页的处理一致。

### 3.2 MW（原生四级，禁止重定义）

`backend/services/mw_ume/mw_ume_topology.py:58` 的阈值是业务口径：

```
u > 80      critical
u >= 70     major
u >= 60     minor
否则         healthy
```

适配器**只搬运名字**，不把四级压成「正常/异常」两档，也不跟 8 档流量色阶混用。
8 档色阶只表达「链路负载百分比」，节点/链路告警严重度仍用四级名与四级色。

### 3.3 OLIB

无实时状态，恒为 `static`；计划链路为 `planned`。**不得推导「在线/离线」。**

---

## 四、OLIB 的两条硬限制

1. **链路是连接关系示意，不是光缆路由。**
   - 骨干链路来自 `frontend/src/pages/networkTopology/topologyData.ts:768` 的
     `CORE_LINKS`（PDF 第 2 页逻辑图梳理，只有「哪两个核心之间有链路」）。
   - PDF 链路来自 `public/olib/olib-fiber-links.json`（第 1 页数字化，592 条边，
     覆盖 411 个站点；`buildFiberLinks` 见 `networkTopologyV2/geoData.ts:116`）。
   - 两者都按两端站点 GPS 画**直线**，`geoData.ts:8-9` 已写明不是光缆走向。
   - 因此适配器给所有 OLIB 边打 `schematic = true`，界面图例必须显示
     「示意连接，非实际路由」，且**绝不上利用率颜色**。
2. **OLIB 没有实时流量。** `trafficAvailable` 恒 `false`，`tx/rxUtilization` 恒 `null`。

---

## 五、历史数据边界

| 来源 | 历史能力 | 中心怎么做 |
| --- | --- | --- |
| MW | `edge.history` 现成（日粒度全部日期；实时最近 96 个 15 分钟槽） | 链路详情画 TX/RX 趋势 |
| IPRAN | `topo_traffic.py` 只读**最新** KPI 快照，无历史接口 | **不画曲线**，只显示当前样本；详情里明确写「无历史接口」 |
| OLIB | 无 | 不画 |

**禁止**用前端缓存、定时轮询或两次快照拼一条「伪历史曲线」。真需要历史时，
先在后端新增经过验证的历史接口，再改这里。

---

## 六、数据读取与刷新

| 来源 | 接口/文件 | 中心策略 |
| --- | --- | --- |
| IPRAN 层清单 | `GET /api/ipran-topo/layers` | 首次加载；切换层时复用 |
| IPRAN 拓扑 | `GET /api/ipran-topo/graph?layer=<key>` | 首次只取默认层 `ipran`；切层才取对应层 |
| IPRAN 流量 | `GET /api/ipran-topo/graph?layer=<key>&traffic=1` | **只在切到流量视图时**取，单独缓存槽 |
| MW 日粒度 | `GET /api/mw-ume/topology` | 首次加载；后端缓存 300s |
| MW 实时 | `GET /api/mw-ume/realtime` | 用户切「15 分钟」时才取；后端缓存 60s、采集 15 分钟 |
| OLIB 站点 | `public/olib/olib-site-geo.json` | 静态文件，手动刷新才重取 |
| OLIB 光纤 | `public/olib/olib-fiber-links.json` | 同上 |
| OLIB 骨干 | `topologyData.ts` 的 `CORE_LINKS` | 编译进 bundle，无需请求 |

来源级最小刷新间隔（页面内再拦一层，避免一次刷新打满上游）：

| 来源 | 最小间隔 | 依据 |
| --- | --- | --- |
| IPRAN | 300s | 后端图缓存 120s；告警分钟级 |
| MW | 900s | `mw_ume_kpi_sync_rt` 每 15 分钟采集 |
| OLIB | 1800s | 静态资产 |

顶栏「开启刷新」的建议间隔在 `App.tsx` 的 `AR_PAGE_DEFAULTS` 登记为 **300 秒**；
页面同时登记进 `AR_INPLACE_REFRESH_PAGES`，收到 `myapp-refresh` 时**原地静默重取**，
不重建地图与画布。

---

## 七、实测样本（2026-11-19 17:53，`layer=ipran&traffic=1`）

| 项 | 值 | 说明 |
| --- | --- | --- |
| IPRAN 节点 | 100 | 93 网元 + 7 层外 |
| IPRAN 折叠边 | 141 | 原始 167 条物理链路 |
| IPRAN 有流量边 | 50 / 141（35%） | `meta.traffic.matchedEdges` |
| IPRAN KPI 采样时刻 | 2026-11-20 00:30:00 | 6 张 15 分钟表 |
| MW 节点 | 649 | 物理节点 |
| MW 链路 | 373（其中 353 有 KPI） | 20 条 `unknown` |
| MW 选中日期 | 2026-11-18 | 日粒度 |
| OLIB 站点 | 667（定位 541） | 静态映射快照 2026-11-17 |
| OLIB PDF 链路 | 592（覆盖 411 站） | `olib-fiber-links.json` |
| OLIB 骨干链路 | 17 | `CORE_LINKS` |

这些数字用于判断「界面上的数字是否合理」。它们会随上游变化，**不是断言**。

---

## 八、验收对应关系

| 验收项 | 由谁保证 |
| --- | --- |
| 三个来源均能独立加载、降级 | `useTopologySources`（每来源独立 state） |
| 没流量不显示 0% | 三个适配器的 `trafficAvailable` 规则 + `__tests__` |
| TX/RX 不混淆 | 适配器逐字段搬运，画布上半段=TX、下半段=RX，图例注明方向 |
| MW 四级不被改写 | `mwAdapter` 只做名字映射 + `nativeStatus` 原值保留 |
| OLIB 不显示实时利用率 | `olibAdapter` 恒 `trafficAvailable=false` |
| 地理比例不拉伸 | `layouts/geographicLayout.ts` 等比投影 + 居中留白 |
| 跨系统不误连 | 1.0 不生成任何跨源边；节点 key 带来源前缀 |
