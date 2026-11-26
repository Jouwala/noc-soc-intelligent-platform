# PRTG 通道语义字典（Issue #225）

> 来源：2026-11-23 对 PRTG 14.2.10.2256（Cellcom PRTG / GRAPHER-ISP）只读账号的实测，
> 浏览器侧逐项验证（原始截图与记录在 MSNOC1 本机 TMP，未入库）；2026-11-24 在 MSNOC1 以
> 后端程序直连补齐 .xml 原文与 CSV 表头复核（第 5 节，证据文件在 MSNOC1 本机 TMP，未入库）。
> **不照搬新版文档**——实测与现代文档有多处差异，写代码时以本表为准。
> 代码实现：`apps/my_app/backend/services/prtg/channels.py`、`parsing.py`；测试：`apps/my_app/tests/test_prtg_channels.py`、`test_prtg_parsing.py`。

## 1. 适用范围

- 传感器：SNMP Traffic 64bit，共 24 个（25 个传感器中的其余 1 个是 Ping，通道集不同，不在本字典内）。
- 扫描间隔：60 秒；传感器 2721（PTP_SW → CSQUARED 接口 10152）为抽样核对基准。
- 传感器详情页核对字段（2721）：name、type=SNMP Traffic 64bit、interval=60s、status=Up、
  Last Down 2026-10-30 02:35:34、Uptime 99.8774%（955 天）、Downtime 0.1226%（28h9m）、Coverage 100%。
  以上为 2026-11-23 快照：可用率随累计漂移，2026-11-24 直连复核时已变 Uptime 99.8775% /
  Downtime 0.1225%（差在第四位，属正常累计，不是口径变化）。
- 2026-11-24 直连复核 25/25 传感器可识别（详见第 5 节）；状态计数是时点快照
  （复核时 Up 17 / Unusual 8，浏览器侧 2026-11-23 为 18 / 7），引用时注明时点。

## 2. 通道表（11 个）

| ID | 通道 | 值形态 | 显示单位 | 原始值单位 | 语义与口径 |
| --- | --- | --- | --- | --- | --- |
| -1 | Traffic Total（流量合计） | 速率 + 体积成对 | kbit/s / KByte | byte/s / byte | **实测 Total = In + Out**（3 个样本行显示值与原始值均相等，仅 1 行体积差 1 KByte 为取整）。利用率仍按 `max(In/Cin, Out/Cout)`，不要拿 Total/容量（会翻倍）。 |
| 0 | Traffic In（入向流量） | 速率 + 体积成对 | kbit/s / KByte | byte/s / byte | 利用率分子取 In/Cin。 |
| 1 | Traffic Out（出向流量） | 速率 + 体积成对 | kbit/s / KByte | byte/s / byte | 利用率分子取 Out/Cout。 |
| 2 | Unicast In（单播入向） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | **区间值，不是累计计数器**：120 行内逐行波动、非单调递增。不需要再差分。 |
| 3 | Unicast Out（单播出向） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | 同上。 |
| 10 | Errors In（入向错误） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | 区间值，不差分。实测窗口（2026-11-23 09:00–11:00）全 0，劣化预警规则需换有误差的时段再验证。 |
| 11 | Errors Out（出向错误） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | 同上。 |
| 12 | Discards In（入向丢弃） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | 区间值，不差分。实测窗口全 0。 |
| 13 | Discards Out（出向丢弃） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | 区间值，不差分。实测窗口全 0。 |
| 14 | Unknown Protocols In（未知协议入向） | 区间计数 + 速率成对 | # / #/s | count / count-per-second | 区间值，不差分。 |
| -4 | Downtime（停机时间） | 无数值 | — | — | 详情页列出但无数值；停机时长以 Uptime/Downtime 百分比与 Last Down 为准。 |

historicdata 每行 20 组 value/value_raw 的排列顺序即上表顺序（Total、In、Out、Unicast in/out、Errors in/out、Discards in/out、Unknown in）。

## 3. 两大定版结论

1. **Traffic Total = In + Out**（实测成立）：拓扑利用率仍按门户口径用 `max(In/Cin, Out/Cout)`，不要拿 Total/容量。
2. **Errors/Discards/Unicast 是区间值不是累计计数器**：120 行内 Errors/Discards 全 0、Unicast 计数逐行波动非单调递增。
   不要按「累计计数器再差分」处理 —— 这套数据**不需要差分**，PRTG 已给区间速率。

## 4. 解析层实测坑（与现代文档的差异）

- sensortree 返回 **HTML 片段**而非 XML，根为 `<ul class="sensortree">`；全树 74 个对象（6 组、42 设备、25 传感器、1 探针），最深 6 层。
- table.xml（content=sensors/devices）默认只回 5 列（group、device、sensor、status、status_raw，status_raw：Up=3），**objid、lastvalue 都不在默认列里**，必须用 columns 参数显式指定，否则解析拿到空。
- .xml 根元素（2026-11-24 直连复核）：table.xml 根为 `<sensors>`，getsensordetails.xml 根为 `<sensordata>`，字段清单见第 5 节。
- getsensordetails 的 lastup / lastdown / lastcheck 原文是 **Excel 序列日 + `[x d ago]` 后缀**（如 lastdown 原文 `46325.1080343287 [25 d ago]`），不是详情页上的可读时间串；解析先取序列日换算，不要假设这里是可读时间串。
- lastvalue / historicdata 的 value 以**重复键成对出现**（速率 + 体积/计数），按普通 dict 解析会丢一半，必须按序处理。
- historicdata 的 datetime_raw 是 **Excel 序列日**（如 46349.3753943056 ↔ 2026-11-23 09:00:34），入库要换算；datetime 显示为美式 `11/23/2026 9:00:34 AM`（服务器本地时间）。
- getsensordetails **无 .json 版**（.xml 原文浏览器读不到）；chart.png 正确路径是根目录 `/chart.png`（`/api/chart.png` 不存在），且 sdate/edate 不生效——历史图不能靠这个端点按时间取，曲线必须自绘、数据走 historicdata。
- count 参数生效（content=sensors&count=5 时 treesize 仍报 25、数组只回前 5 条），可用于分页。

## 5. MSNOC1 直连复核结果（2026-11-24 已完成）

执行位置：MSNOC1，后端 httpx 直连 PRTG API（只读账号）。证据文件在 MSNOC1 本机 TMP，未入库。

- [x] `.xml` 原文根元素/字段名：table.xml（content=sensors）根元素 `<sensors>`，默认每条
  `<item>` 字段为 group、device、sensor、status、status_raw（与第 4 节一致）；
  getsensordetails.xml 根元素 `<sensordata>`，字段共 21 个，顺序为：name、sensortype、
  interval、probename、parentgroupname、parentdevicename、parentdeviceid、lastvalue、
  lastmessage、favorite、statustext、statusid、lastup、lastdown、lastcheck、uptime、
  uptimetime、downtime、downtimetime、updowntotal、updownsince
  （已同步进 `channels.py` 的 `SENSORDETAILS_FIELDS`）。
- [x] historicdata.csv 表头原文：共 45 列——`Date Time`、`Date Time(RAW)`，其后每个通道
  依次 `(volume)`、`(volume)(RAW)`、`(speed)`、`(speed)(RAW)` 四个变体（通道顺序同第 2 节），
  最后 `Coverage`、`Coverage(RAW)`。与第 2、3 节「列序与 JSON 10 通道一致」的结论一致，
  无相反证据。样本：id=2721、avg=0、2026-11-23 00:00–01:00，响应 200、31,099 字节。
- [x] 25/25 传感器可识别：table.xml 指定 `columns=objid,group,device,sensor,status,type`
  并 `count=100`，返回 25 条、全部带 objid；24 个 SNMP Traffic 64bit + 1 个 Ping
  （objid 2724，AML TOKADEH1）。2721 抽样（name、parentdevice、parentgroup、sensortype、
  interval、statustext）与第 1 节一致。

## 6. 未覆盖

- Ping 传感器（AML TOKADEH1，1 个）的通道集（ping 时间/丢包等）尚未实测，不在本字典内；#227 采集服务接 Ping 传感器时再补。
- Errors/Discards 劣化预警规则的验证时段：实测窗口全 0，需换有误差的时段再验证（见 #229）。
