# 03 数据层设计

## 双模数据库：当前跑的是 PostgreSQL

`backend/database.py` 用一个开关切换两种后端：

| 开关 | 取值 | 行为 |
| --- | --- | --- |
| `MY_APP_DB_MODE` | `sqlite`（默认，`database.py:27`） | 走本地文件 `my_app/db/app.db`（`database.py:31`） |
| | `pg` | 走 `DATABASE_URL` 连接串（`database.py:34,37`），连接池由 `PG_POOL_SIZE`/`PG_MAX_OVERFLOW` 控制（:54-55） |

**`backend/.env` 当前实际是 `MY_APP_DB_MODE=pg`**，即远程 PostgreSQL 为主库，SQLite 作为回退熔断保留（`database.py:23-26`）。手册里所有"查库"操作都以此为准。

⚠️ 但 `db/app.db` 并没有因此变成死文件——它仍在被使用（见下方"例外"），当前体积约 954 MB，**不要当垃圾删除**。

## 方言适配层

跨方言的公共逻辑集中在两处，写新 SQL 前先确认这里有没有现成工具：

**`backend/database.py`**
- `_qmark_to_pyformat`（:215）：把代码里统一使用的 `?` 占位符转成 psycopg 的 `%s`。**因此新增查询请一律写 `?`，不要写 `%s` 或命名参数。**

  ⚠️ **同一层转换还有一个易踩的坑：SQL 字面量里的 `%` 必须转义或绕开。**

  psycopg2 把 `%` 当作参数占位符的起始符。`_qmark_to_pyformat` 只转 `?`，
  **不会**动字面量里的 `%`；于是任何 `LIKE '前缀%'` 写法在 PG 模式下都会抛：

  ```
  IndexError: list index out of range
  ```

  因为它把 `Closed%` 里的 `%` 当成了第 N 个参数位置，而 params 里没有那一项。
  SQLite 模式下不会报错（`?` 与 `%` 互不相干），所以**只有切到 PG 才暴露**，
  单测若只跑 SQLite 就发现不了。

  2026-10-18 实例：`tt_wo_dashboard.py` 的下钻状态分桶用了
  `t.status NOT LIKE 'Closed%'`，分桶结果**永远算不出来**；又因为外层是
  `except Exception: buckets = None`，异常被静默吞掉，「未闭环 N 个」这行
  悄悄消失、日志里一个字都没有（见 `d390f495` → 修复 `d074ed74`）。

  两种正确写法：

  ```python
  # ① 用函数避开 % —— 前缀比较推荐这个，语义最直白
  LEFT(t.status, 6) <> 'Closed'

  # ② 确实需要 LIKE 时，把 % 写成 %%（交给 psycopg 还原）
  t.status NOT LIKE 'Closed%%'
  ```

  **同时**：凡是把异常吞成"降级值"的地方，`except` 里必须留一行
  `logging.warning(..., exc_info=True)`。上面那个 bug 之所以潜伏，
  完全是被静默 `except` 藏住了——「不影响主结果」不等于「不需要留痕」。
- `_resolve_pg_schema`（:184）：把"SQLite 文件路径 + 表名"的历史调用映射到 PG 的 schema，兼容旧代码。
- `_PsycopgRaw`（:308）：给需要裸 DB-API 行为的代码提供 psycopg 连接包装。
- `_ClosingConnection`（:104-140）：修复连接泄漏，保证取到的连接最终被关闭。
- SQLite 侧 PRAGMA：`busy_timeout=30000`、`synchronous=NORMAL`（:76-89）——回退到 SQLite 时才不会 `database is locked`。

**`backend/dialect_helpers.py`（:73-168）**
- 表/列枚举、`safe_pragma`
- `upsert`：SQLite 用 `INSERT OR REPLACE`，PG 用 `ON CONFLICT`，一个函数两种行为
- `pg_trunc_ident`：PG 标识符按 63 字节截断（中文表名列名尤其容易踩）

## 例外：仍然硬绑 SQLite 的代码

`backend/services/credential/credential_store.py:263-266` 用 `sqlite3` 直连 `db/app.db` 读写凭据表，**绕过 `MY_APP_DB_MODE`**。后果：

- PG 模式下凭据仍然存在本地 `app.db`，不随 PG 备份走；
- 只备份 PG 会丢凭据，只备份 `app.db` 会丢业务数据。备份策略见 `13-operations.md`。

同类"独立 SQLite"还有各任务的专用库，它们**从来不在主库里**：

| 文件 | 写入任务 | 消费方 |
| --- | --- | --- |
| `data/battery/battery_kpi.sqlite3` | `battery_kpi_sync`（每小时 :05） | 电池看板 |
| `data/packet_lost/lte_pl.sqlite3` | `packet_loss_sync`（每天 06:30） | 丢包看板（注意目录名是 `packet_lost`，不是 `packet_loss`） |
| `data/ms_sla/ms_sla_dashboard.sqlite3` | `ms_sla_full`（每 6 小时） | MS SLA 看板 |
| `data/ms_sla/lbr_ms_sla_kpi.sqlite3` | 同上流程的旧产物 | 旧库名，PROJECT_DOCS.md §7 引用的就是它；当前看板读 `ms_sla_dashboard.sqlite3` |

同目录下还有 `data/ran_ume/ran_kpi_daily.sqlite3`、`data/ipran_ume/ipran_ume_kpi.sqlite3`、`data/cnn_ume/`、`data/eflow/eflow.sqlite3`、`data/ran_kpi_avalability_1day.sqlite3` 等，命名沿用历史习惯（含 `avalability` 这类既有拼写错误），**不要顺手改名**——多处 SQL 与任务按字符串引用它们。

### `data/` 下的「按次产物」与保留期（2026-11-20 起）

`data/` 里除了上面这些业务库，还混着**按次写、用完即无用**的副产物（各网管导出的中间 xlsx、Playwright 登录的临时 profile、损坏镜像快照、`olib_soc_report` 的逐次运行摘要、`ip_trace` 抓取留档）。它们由任务 `tmp_cleanup`（`backend/services/cleanup/data_residue_cleanup.py`，每天 03:47）按**逐类保留期**回收：先同盘移进 `data/_quarantine_YYYYMMDD/`（保留相对路径，可原样挪回），隔离满 30 天才真删。

- 规则表在该模块的 `RULES`，**只处理显式列出的 glob**；`data/` 下业务库与报表源一律不碰（通用「超过 N 天就删」在这里是灾难，不是策略）。
- 加一类新残留：在 `RULES` 加一行，并写清**谁写的、为什么留着没用**；说不清的不许加。
- **新代码不要往 `data/` 落按次产物**：临时文件落 `TMP/`（见 `docs/CODE_CLEANUP_GUIDE.md`）。确需落 `data/` 的，同步在 `RULES` 登记，否则又会变成下一个「没人读、没人删」的遗留。
- 背景（2026-11-20 审计）：`data/cnn_ume/exports/` 里两个 2026-07-31 的 UME 导出 xlsx 一直留到 11 月。根因不是忘了写清理，而是写入方改内存模式后不再落盘、删除逻辑只删「自己刚读到的那一个」、`data/` 被 `.gitignore` 挡住导致 `git status` 看不见，三者叠加。


## 裸 `sqlite3.connect` 点位清单（治理登记）

`get_raw_connection`（`backend/database.py:145`）提供三样东西：双模路由（PG 模式按路径映射 schema）、`busy_timeout` / `synchronous` PRAGMA、以及 `with` 退出时提交**并关闭**的连接语义。裸 `sqlite3.connect` 这三样都没有（且 `with sqlite3.connect(...)` 只管事务、不关连接，见 `database.py:107` 的注释）。

但全量核查（2026-11-13）表明：现有裸连绝大多数打开的是**独立数据文件**——这类路径在 PG 模式下走 `get_raw_connection` 会被误映射成 PG schema，对它们而言裸连目前是唯一正确写法。治理动作因此是**冻结清单**：新增裸连必须先在本节登记分类与理由；未登记的新点位视为待治理项。

复核命令（在 `apps\my_app` 下）：`grep -rn "sqlite3\.connect" --include="*.py" backend tools`。2026-11-13 实测 71 处命中 = 17 处注释 / docstring 提及 + 1 处本体（`backend/database.py:163`，即 `get_raw_connection` 自身实现）+ **53 处实际调用**，分类如下：

### A. 本地权威表 · 故意直连 `app.db`（6 处）

与 DB 模式无关地固定写 SQLite，判据见上文 `SKIP_TABLES` 一节：

| 点位 | 说明 |
| --- | --- |
| `backend/services/credential/credential_store.py:696` | 凭据表，故意裸连（理由写在 `:683` 注释） |
| `backend/services/email/email_recipient_store.py:341` | 收件人表，同上（理由写在 `:334` 注释） |
| `backend/services/task/task_runner.py:412` / `435` / `495` / `561` | `task_schedule_config` 读写；判定只读本地这一份，PG 侧只是双写镜像 |

### B. 独立工具直读 `app.db`（2 处）

| 点位 | 说明 |
| --- | --- |
| `backend/services/eflow_rules/eflow_rules_matcher.py:43` / `194` | 独立可运行的 eFlow 规则匹配工具（理由写在 `:38` 注释）⚠️ 未设 timeout |

### C. 独立数据文件 · 只读打开（10 处）

| 点位 | 说明 |
| --- | --- |
| `backend/services/avail_ms_service.py:189` | 可用率库 `AVAIL_DB`，`mode=ro` |
| `backend/services/bp/bp_report_store.py:184` | `connect(read_only=True)` 分支，`mode=ro` |
| `backend/services/dashboard_service.py:282` | 传入的本机库路径，`mode=ro` + `query_only` |
| `backend/services/db/db_freshness_check.py:418` / `432` | 新鲜度巡检任意库文件，`mode=ro` |
| `backend/services/tt/mttr_tt_sync.py:143` | 读源库，`mode=ro` |
| `backend/services/ran/ran_cells_kpi_service.py:60` | `_connect_ro`：不用 `file:` URI（避 UNC 路径坑），改用 `PRAGMA query_only=ON` |
| `backend/services/site/site_availability_service.py:326` | 本地可用率库 `mode=ro`；注释已说明 PG 模式走封装会被误映射 schema |
| `backend/services/soc_trend/soc_trend_store.py:51` | `connect(readonly=True)` 分支，`mode=ro` |
| `backend/services/sp_report_gen/generate_sp_weekly_report.py:243` | `mode=ro`，timeout=10 |

### D. 独立数据文件 · 读写（19 处，各任务专用库）

| 点位 | 目标库 / 说明 |
| --- | --- |
| `backend/services/battary_kpi_fetch/battery_kpi_service.py:300` | `battery_kpi.sqlite3` |
| `backend/services/bp/bp_report_store.py:187` | `connect(read_only=False)` 分支 |
| `backend/services/cm_data/cm_data_export.py:1906` | `cm_cell_data.sqlite3` |
| `backend/services/fw_kpi/fw_kpi_services.py:520` | 候选路径循环打开 FW 库（TODO 原文点名点位，行号已由 488 漂移至此） |
| `backend/services/sp/get_sp_table.py:574` | 镜像同步路径，带 `MIRROR_CONNECT_TIMEOUT` |
| `backend/services/gp_ran/gp_ran_store.py:52` | 本模块独占旁路库（理由写在文件头 `:12`） |
| `backend/services/native/native_board_mirror_sync.py:147` | 本机镜像库 `_open_local`，自设 PRAGMA |
| `backend/services/olib_soc_report/olib_soc_kpi_store.py:167` / `171` | olib 专用 store 库 |
| `backend/services/olib_soc_report/olib_soc_report_service.py:1028` / `1104` | `olib_soc_report.sqlite3` |
| `backend/services/ran/ran_cell_availability_sync.py:376` / `463` / `470` | `ran_kpi_avalability_1day.sqlite3` ⚠️ 未设 timeout |
| `backend/services/ran/ran_cells_kpi_service.py:570` | 物化库 `ran_cells_kpi.sqlite3` ⚠️ 未设 timeout |
| `backend/services/soc_trend/soc_trend_store.py:54` | 写分支，自设 WAL + `busy_timeout` |
| `backend/services/sp/sp_mirror_push.py:124` | 推送用单文件镜像，自设 `journal_mode=delete` ⚠️ 未设 timeout |
| `backend/services/vmax_multi_data/vmax_multi_data_service.py:230` / `278` | `vmax_multi_data.sqlite3` |

### E. 临时 / 产物文件（5 处）

写临时文件后原子替换、或一次性产物，对象不是共享库：`backend/services/avail_recon_sync.py:280`、`mttr_tt_sync.py:179`、`progress_sync.py:376`、`ran_ume_service.py:1075`、`tt_alarm_render.py:271`（未设 timeout，但对象是临时文件，风险低）。

### F. PG → SQLite 快照重建（1 处）

`backend/scripts/sync_pg_to_sqlite.py:355` —— 下一节「PG → SQLite 快照」的实现本体。

### G. 迁移与离线工具（10 处，人工执行、不在运行期）

`backend/migrations/fix_tt_alert_log_columns.py:210`、`reconcile.py:161`（`mode=ro`）、`transfer_lib.py:97`；`backend/services/backfill/backfill_new_group_history.py:79`、`backfill_old_group_history.py:86`（均 `mode=ro`）；`tools/backfill_manual_stop_flag.py:81`、`fix_task_schedule_dupes.py:77`、`reconcile_task_schedule_state.py:163` / `209`、`sync_manual_stop_to_pg.py:32`。

### 核查结论（2026-11-13）

1. TODO 原文点名的 `ipran_chart_service.py:233` 已不存在：该文件已改走 `get_raw_connection`（现 `ipran_chart_service.py:239-240`），此例已消。
2. 运行期真正直连 `app.db` 的只有 A、B 两类，全部有既有注释或手册依据；**不存在未登记的服务绕过连接管理读写主库**。此前担心的「图表数据源绕过」实际是打开各自的独立数据文件（C / D 类）。
3. 后续治理候选（本清单只登记，不动代码）：7 处未设 timeout 的点位（D 类 5 处 + B 类 2 处）用的是 sqlite3 默认 5 秒，与其余点位 10~60 秒的口径不一致；它们多是图表数据源，改造会静默坏图，按 TODO 约定另行排期、逐个验证。

## PG → SQLite 快照

`pg_to_sqlite_sync` 把远程 PG 全库同步到本地 `db/app.db`，作为离线备份与回退快照（`task_registry.py:723`）。这意味着 `app.db` 里的业务表是**上一次同步之前的镜像**，别在它上面做"实时"假设。
⚠️ 估新鲜度别看注册表默认（`45 * * * *`），要看**库里的生效值**：`task_schedule_config` 把它覆盖成了 `45 */12 * * *`（后端/worker 启动日志的 `[SCHEDULE]` 告警会明说这条差异），2026-10-21 实测还出现过一次非整点执行（16:05:07 认领）。要确认最近一次，查 `log/worker_runtime_m_*.log` 里的认领/完成行。

⚠️ **例外：`SKIP_TABLES` 里的"本地权威表"不是镜像**（`backend/scripts/sync_pg_to_sqlite.py:80`）。
`credentials`、`email_recipients`、`task_schedule_config` 由本机应用无条件写 SQLite，
同步任务一律跳过它们 —— 一旦被同步就会 `DROP TABLE` 重建，抹掉本地数据连带丢主键
（`credentials` 2026-09-18、`task_schedule_config` 2026-10-21 各踩过一次，判据与事故记录写在
该文件注释里）。判断一张表能不能进 `SKIP_TABLES`：**它的写入方是否与 DB 模式无关地固定写
SQLite**。`task_schedule_config` 尤其特殊：判定**只读本地这一份**，PG 那份是
`_save_schedule_config` 双写出来的**只写不读镜像**（细节见 `04-task-system.md` 的
「双库一致性：权威只有一份，PG 只是镜像」）。

## 远端 SQLite → 本机 PG 镜像（2026-11-22，看板不再跨机取数）

### 背景与范围

一批看板原先经 `db_writer_api`（`.19:8765`）读远端 SQLite。2026-11-04 对 IP RAN 已
落过「数据在本机产生，远端只是给其它 BI 的副本，看板不要跨机取数」；2026-11-22
如意要求**所有从 .19 取数的页面都改读本机 PG**，先落地 CN/RAN 这一批。

| 远端库 | 本机 PG schema | 说明 |
| --- | --- | --- |
| `db_ps_cs_cn.sqlite3` | `cn_kpi` | CN KPI 全系列（cn_kpi / cn_kpi_split / cn_kpi_5mins / CS·PS 15min / 小时）与 MOU/CCN 邮件；原生看板 33 个组件 |
| `ran_kpi_daily.sqlite3` | `ran_kpi` | RAN 15 分钟长表 `ran_kpi_15mins`（另含 4 张 OLIB 日表） |
| `db.sqlite3` | `native_db` | 原生看板（FineBI 重建）6 张日表 + `Eflow_TT_FB` / `Eflow_WO_FB`，共 58 个组件 |
| `fw_db.sqlite` | `fw_native` | 原生看板 `device_whole_throughput`（2 个组件） |

> `db.sqlite3` 只镜像**被看板 SQL 引用**的 8 张表（`MIRROR_TABLES`）；库里的
> `2G/3G/4G_Main_Kpi_Cell_Daily`、`umts_PL_Hourly_7` 等百万行表没有消费者，不拉。

### 写入：`backend/services/db/pg_mirror.py`（常驻守护）

- 后端 `startup_event` 起一个 daemon（`start_mirror_daemon`），**每 5 分钟**跑一轮，
  不登记成 `TASK_SPECS` 任务 —— 它不能被 worker 分组里的长任务（`pg_to_sqlite_sync`
  9~20 分钟、`eflow_sync` 数分钟）堵住；为它单开 worker 组要同步改五处，代价不对等。
  先例：`native_board_mirror_sync.start_mirror_daemon`。
- 表结构从远端 `sqlite_master` 读（列名 + `PRIMARY KEY`），本机 PG 建**同名、全 TEXT**
  的表；所有表按远端主键 upsert，幂等。
- 增量：**有主键**的表按时间列（`Begin Time` / `begin_time` / `End Time` …）取
  `本机 MAX(时间列) − 24h` 之后的行，按主键 upsert。
- **窗口替换**：`db.sqlite3` 的日表没有主键但有 `Begin Time`/`End Time`；首轮拉全历史，
  之后每轮删掉「水位 − 24h」之后的行再写回（窗口内吸收上游修正，窗口外保留历史）。
- 整表重拉：**无主键**的表**每轮都重拉**（没有唯一约束就无法幂等，走增量会把
  20 行镜像成 40 行 —— 2026-11-22 实测 3G UME 表踩过）；表名 `1month_*` 或
  `*_EMS`（上游 full_replace）最多每 6 小时一次。单表超过 30 万行直接报错
  （要求另设计增量，避免每轮全拉）。
- **进程间互斥**：`run_mirror` 用 PG advisory lock（`0x5A4D4952`）保证 daemon
  与手工 CLI / 两个后端实例不会同时 upsert 同一张表（实测并发会死锁）；拿不到锁
  的那一轮直接跳过，下一轮再来。
- 隔离副本（`ISOLATED_COPY.flag` / `.smoke-runner`）**不启动**守护，避免冒烟栈
  打生产远端。
- 原生看板的取数层（`dashboard_service.query`）把 3 个历史库的别名换成
  `__local_pg__:<schema>`，**PG 优先 → 本机 SQLite 镜像 → 远端**三级回退；
  `NATIVE_BOARD_PG=0` 一键恢复旧行为（仍走本机镜像）。

### 读取：`backend/services/db/pg_query.py`（PG 优先、远端回退）

页面取数统一走 `query_data(sql, db=...)`：

1. `PG_READ_ENABLED=1` 且 `MY_APP_DB_MODE=pg` → 先在本机 PG 执行；
2. PG 缺表 / 方言不支持 / 返回 0 行 → 回退 `.19`（`query_remote`），行为与改造前一致；
3. 远端也空 → 返回 PG 的空结果（形状一致）。

SQL 仍是**唯一真相源**（`cn_kpi_service.WIDGETS` / `cn_kpi_split_widgets` 等），
PG 侧只做最小改写（`to_pg_sql`）：

| SQLite | PG | 原因 |
| --- | --- | --- |
| `CAST(x AS REAL)` | `CAST(x AS DOUBLE PRECISION)` | `REAL` 是 4 字节，会丢精度 |
| `x GLOB '*[0-9]*'` | `x ~ '[0-9]'` | PG 无 GLOB |
| `HAVING t >= …` | 外层 `WHERE` | PG 的 HAVING 不认 SELECT 别名 |
| `WHERE t >= …` | 外层 `WHERE` | 同上（WHERE 也不认） |
| `FROM (子查询)` | 补 `AS _pg_sN` | PG 要求子查询必须带别名 |
| `date(...)` | `schema.date(...)` | `pg_catalog` 永远排在 search_path 前，不限定会命中内建 `date(timestamp)` |
| `SUM/AVG(裸列/别名/加法表达式)` | 包 `safe_num()` | 镜像列全是 TEXT；`safe_num` 对数值类型有重载，非数值返回 NULL |

兼容函数（`date` / `strftime` / `printf` / `safe_num` / `round(double,int)`）由
`ensure_schema_ready(schema)` 幂等创建在目标 schema 里；首次 `query_pg` 自动补齐。

### 开关与手工命令

```powershell
# 全量/增量回填（手工）
cd D:\02_olbr_portal\apps\my_app
python -m backend.services.db.pg_mirror --status
python -m backend.services.db.pg_mirror --db db_ps_cs_cn.sqlite3 --full
python -m backend.services.db.pg_mirror --db ran_kpi_daily.sqlite3 --table ran_kpi_15mins
```

| 环境变量 | 默认 | 作用 |
| --- | --- | --- |
| `PG_MIRROR_ENABLED` | `1` | 关闭镜像（守护与手工任务都跳过） |
| `PG_MIRROR_DAEMON` | `1` | 关闭后端内常驻守护（仍可手工跑 CLI） |
| `PG_READ_ENABLED` | `1` | 关闭 PG 读通道，全部回远端（零改码回退） |
| `NATIVE_BOARD_PG` | `1` | 原生看板 93 个组件读 PG；`0` 恢复读本机 SQLite 镜像 |
| `PG_MIRROR_OVERLAP_HOURS` | `24` | 增量重叠窗口 |
| `PG_MIRROR_FULL_HOURS` | `6` | EMS/1month 整表刷新间隔 |
| `PG_READ_TIMEOUT_MS` | `60000` | 单条 PG 查询 statement_timeout |

### 已知边界（写进页面口径说明，不要当 bug）

- **镜像滞后 ≤ 5 分钟**（守护间隔）；上游同步任务本身 15 分钟一轮，所以 D0 最新桶
  可能比远端少 1~2 个点。EMS / `1month_*` 最多滞后 6 小时（人工导出，一天一次）。
- **0.01 级舍入差异**：PG 用 numeric 精确平均，SQLite 用 double 累加，恰在 `.005`
  边界时可能差 0.01（实测 3 个 15 分钟点）。不是取数错误。
- 超 63 字节的列名在 PG 被截断（如 `Byte ... per APN(Byte)`）；建表、`ADD COLUMN`、
  查询都按同一截断口径处理，**不要**在别处再用全名手工建列。
- 远端仍是采集侧的**权威库**（其它 BI / FineBI 在消费）；镜像只读，不在 .19 建索引、
  改表、改 PRAGMA。

## 迁移与建表：三套东西各司其职

| 位置 | 是什么 | 怎么用 |
| --- | --- | --- |
| 仓库根 `alembic/` | 真正的 Alembic 工程。`alembic.ini:7,9` 里的 `sqlite:///db/app.db` **只是占位**，真实 URL 由 `alembic/env.py:38-64` 按 `MY_APP_DB_MODE` 解析 | `alembic upgrade head`；回退 `alembic downgrade base` |
| `backend/migrations/` | **不是 Alembic**，是一次性数据搬迁/校验脚本（`transfer_*`、`reconcile`、`smoke_test_pg`） | 手工按需执行，不在正常发布流程里 |
| `backend/scripts/init_db.py` | 首次初始化建表 | 全新环境第一步 |

`alembic/versions/` 当前 6 个版本：`0001_baseline`（6 张结构表）、`0002_task_queue`、`0003`、`0004`、`5415704359dc_phase4_coverage`、`0005_tt_exclude_table`。

> `0005_tt_exclude_table`（2026-10-06）建 `app.tt_exclude` —— NUR 排除名单，由人工在数据浏览页上传维护（见 `11-feature-guide.md` 第 5 节）。
> 它是**结构表**（人工维护、列固定），因此走 Alembic；不要与「采集任务运行时自建」的动态表混为一谈。

**约定：动态 eFlow 表不进 migration**（`0001_baseline.py:13-15`）。这类表由采集任务在运行时按上游字段自行建表，因此 `alembic upgrade head` 之后表数量少于页面上看到的表数量是**正常现象**，不是迁移失败。

同一条约定也适用于其它「任务独占、运行期自建」的表：`app.ran_element_management*`
（`services/RANElementManagement`）与 NGI 的四张表 `app.ngi_lte_cell_param` /
`app.ngi_nr_cell_param` / `app.ngi_coverage_grid` / `app.ngi_sync_log`
（`services/ngi_data/ngi_store.py` 的 `DDL`，`CREATE TABLE IF NOT EXISTS` 幂等）。
它们的幂等靠主键：前者 `ne_id` / `(snapshot_date, ne_id)`，后者 `cell_uid` /
`(kpi, earthid, offsetx, offsety)`。改字段映射时 DDL 与映射必须同步改，
`tests/test_ngi_param.py` 会比对两边。

### ⚠️ 动态表的唯一约束从哪来（upsert 的隐含前提）

`dialect_helpers.upsert` 的 PG 分支生成 `INSERT ... ON CONFLICT (cols) DO UPDATE`，
**要求 `cols` 上真的存在主键或唯一索引**，否则 PG 直接报
`没有匹配 ON CONFLICT 说明的唯一或者排除约束`（2026-10-07 实测确认过该文案）。

SQLite 分支是 `INSERT OR REPLACE`，**不要冲突目标也照样工作**——所以
**只跑 SQLite 的测试永远发现不了这类缺陷**。这正是 2026-10-07 `tt_alarm` 线上故障
（`eflow_tt_whatsapp_alert_log` 老表无约束）能潜伏的原因。动态表加约束有两条路：

| 方式 | 代表 | 说明 |
| --- | --- | --- |
| **DDL 带约束**（推荐） | `ran_ume_network_kpi.py:964` 的 `PRIMARY KEY ("Begin Time","Group")` 写在 `CREATE TABLE` 里；`ms_sla_dashboard.py:80-98` 同理 | 新表天然正确。**但 `CREATE TABLE IF NOT EXISTS` 对已存在的表是 no-op**，不会给老表补约束 |
| **upsert 前显式建唯一索引** | `ipran_ume_kpi_service.py:998`、`mw_ume_kpi_service.py:848`、`ms_sla_sync.py:323`、`ran_ume_network_kpi.py`（PK 路径）、`sp_online_sync.py:433`、`sp_material_sync.py:273`、`sync_from_eflow_api.py:340` | 一律「**先删历史重复行 → DROP 旧同名索引 → CREATE UNIQUE INDEX**」，三件套缺一不可（`sp_material_sync.py:249` 写明：不先清重复行，`CREATE UNIQUE INDEX` 会直接报错） |
| **运行时补约束**（唯一一处） | `tt_alarm_store.py:186` 的 `_ensure_pk_constraint`，调用点 :304 | 全仓仅此一处「表已存在时补约束」的补丁式写法，因为线上表已经存在且无约束。属历史补救，**新代码不要模仿**，优先用 DDL 带约束 |

> **2026-10-07 全量核对结果**：对全部 15 个 upsert 目标表在 PG 实库上逐个执行
> `INSERT ... ON CONFLICT (冲突列) DO NOTHING`（`EXPLAIN`，事务内回滚，零数据改动），
> **15/15 全部通过**，无缺约束的表。涉及 `battery_trend`、`site_pl_summary`、
> `task_schedule_config`、`pl_meta`(在 `lte_pl` schema)、`eflow_tt_whatsapp_alert_log`、
> `portal_qc_snapshot`、`ms_sla_kpi`/`_wo`/`_meta`(在 `ms_sla_dashboard` schema)、
> `ran_ume` 的 4 张 `*_OLIB_Network_KPIs`、`sp_material`、`ume_in_customer_kpi`。
> 复现方式见下方"新增 upsert 目标前的自检"。

> **`ms_sla_*` 与 `pl_meta` 不在 `app` schema**：前者在 `ms_sla_dashboard`
> （由 `ms_sla_dashboard.py:113` 显式建 schema），后者在 `lte_pl`。
> **这两个 schema 都不在 search_path 上**，因此用
> `table_schema = ANY(current_schemas(false))` 这类查法会**误报"表不存在"**——
> 核对时请按表名全库扫描，别只查 search_path。

> **反过来的同一个坑：search_path 上有两份同名表时，裸名会串 schema（2026-10-15 事故）。**
> `realtime_alarm` 与 `history_alarm` 在 **`app` 和 `eflow` 两个 schema 各有一份**，
> 且**列口径完全不同** —— `app` 那份是全量（`realtime_alarm` 实测 149/150 列，
> 列名 snake_case，含 `tt_id` / `alarmraisedtime`，页面与任务读的就是它），
> `eflow` 那份是 `ALARM_COLUMNS` 过滤版（36 列，列名是 `TT ID` 这种原始风格，
> **没有** `alarmraisedtime`）。两条推论，都是这次定下来的硬规矩：
>
> 1. **写端的换表/建索引 DDL 必须带 schema 限定**。PG 的 `DROP TABLE` 会顺着
>    `search_path`（`app,eflow,lte_pl,…`，`backend/database.py:320`）逐级解析，
>    裸名 `DROP TABLE IF EXISTS "realtime_alarm"` 在 `eflow.realtime_alarm`
>    缺失时会**删掉 `app` 的那份**。`eflow_active_alarm` 每轮先写 eflow 副本再写
>    app 主库，两条裸名换名语句因此互相删对方的正式表，实测 `app.realtime_alarm`
>    每分钟消失约 0.14s。落地写法见 `eflow_alarm_service._conn_schema` /
>    `_swap_table_sql`，回归守卫 `tests/test_eflow_alarm_swap_schema.py`。
> 2. **读端判存在性/列集不要取 `ANY(current_schemas(false))` 并集**，会另一份
>    副本污染结论（本例表现为「表在、但 `tt_id`/`alarmraisedtime` 不在」）。
>    要哪份就限定哪份：`tt_alarm_trigger.TRIGGER_SCHEMA`（默认 `app`）就是这条规矩。
>
> **为什么 `eflow` 那份副本不能一删了之**：PG 模式下 `get_raw_connection(…eflow.sqlite3)`
> 返回的是 **eflow schema 的 PG 连接**，物理文件 `data/eflow/eflow.sqlite3`
> 自 2026-08-07 起就不再被写（446MB 冻结快照，直接开该文件的离线工具如
> `Task/eflow_Q&A/tools/gen_datadict.py` 读到的是它）；而一旦按熔断开关
> `MY_APP_DB_MODE=sqlite` 回退（回退方案 R7），`write_alarm_sqlite()` 又是
> 那份**真文件**的唯一生产者，且 `.19` 那台机器可能在 sqlite 模式下跑。
> 所以本次只修「串 schema」，不删双写 —— 删它是跨模式、跨主机的契约变更，需另行评估。

### 看板读端的查询侧索引（`ipran_ume`，2026-11-05）

采集侧给这 6 张 15 分钟表建的唯一索引是 `("Begin Time", "ME", "Measure Object"[, "Counter"])`
—— 前导列是业务时间，对 upsert 幂等正好，但**看板 SQL 的等值条件用不上前导列**：
`WHERE "ME"=? AND "Measure Object"=? AND "Begin Time" 区间` 只能顺着时间范围把整段索引项
走完再丢掉 99.9%（`Metro_ring_me_15mins` 31 天 ≈ 340 万条 + 同量级回表）。
默认窗口 11-05 从 7 天放到 31 天后，`finebi_metro_ring` 整页冷取因此涨到 9~21s，
且成本**随窗口线性**放大（5 天 1.2s / 31 天约 10s）。

补法是加一条以等值列打头的索引，用 `backend/scripts/ensure_ipran_perf_indexes.py`
（幂等可重跑，细节与实测表见同目录 `ensure_ipran_perf_indexes.md`）：

- 5 张表建 `("ME", "Measure Object", "Begin Time")`；
  `Gbarnga_Buchanna_RIA_Kakata_me_15mins` 只建 `("ME", "Begin Time")` ——
  ipran_4region 的 8 个组件是区域聚合、不指定端口，多给一列只是白付体积与写放大。
- **用 `CREATE INDEX CONCURRENTLY`**：采集每小时在 upsert 这些表，普通 `CREATE INDEX`
  的 SHARE 锁会挡住写入。代价是中途失败会留下 INVALID 索引，脚本每次回查
  `pg_index.indisvalid`，非空就退出码 1 并给出 `DROP INDEX CONCURRENTLY` 清理语句。
- 实测：`Metro_ring_me_15mins` 代表查询 7714ms → **29ms**，整页 9~21s → **0.32s**；
  建索引 42.3s、新增约 329 MB。

**三条规矩**：① 这类索引**不进 Alembic**（表本身就是采集运行时建的，
`grep -rl ipran_ume alembic/versions/` 为空），新机器/重建库后跑一次脚本；
② **别删那个唯一索引**，`mirror_rows_to_local` 的 `ON CONFLICT` 靠它保证幂等；
③ 给 `PUSH_DOWN_TABLES` 加表时，脚本的 `PLAN` 要按新表**真实谓词**加一条，
否则又退回全区间扫。

### 已知遗留：`ms_sla_sync` 索引创建失败的降级路径

`ms_sla_sync.py:308-327` 把「清重复行 + 建唯一索引」整段包在 `try` 里，
异常只记 `[SILENT-EXCEPT]` warning 并 `conn.rollback()`，**但没有清空 `pk_cols_db`**；
随后 `:342` 判断 `if pk_cols_db:` 仍为真，`:345` 照常调用
`upsert(..., conflict_cols=pk_cols_db)`。若索引因故没建成，这里会抛出
「没有匹配 ON CONFLICT 说明的唯一或者排除约束」，而**真正的原因（建索引失败）
已被上面的 except 吞掉**，排查时容易被误导。

触发条件苛刻（需要 `CREATE UNIQUE INDEX` 失败，而前置的清重复行已成功），
**当前线上未发生**，故未改动；改这个模块时应在 except 里同时置 `pk_cols_db = []`
把写入降级为纯 INSERT，与 `:301` 已有的降级写法保持一致。

### 新增 upsert 目标前的自检

写新的 `upsert(..., conflict_cols=[...])` 前，用这条 SQL 确认约束存在
（把 schema/表/冲突列替换掉）：

```sql
SELECT pg_get_constraintdef(c.oid)
FROM pg_constraint c
JOIN pg_class cl ON cl.oid = c.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE n.nspname = '<schema>' AND cl.relname = '<表>' AND c.contype IN ('p','u');
```

返回空就说明**缺约束**，要么在 `CREATE TABLE` 里补上，要么在 upsert 前按上表的
「三件套」显式建唯一索引。别只跑 SQLite 测试就认为没事。

### eflow 双库归属：`eflow_tt` 只认主库

- **唯一活写源**：`backend/services/eflow/sync_from_eflow_api.py` 的 `sync_from_eflow_api()` 经 `get_raw_connection(str(DB_PATH))`（`:665`）只写主库（sqlite 模式 = `db/app.db`，PG 模式 = `app` schema），覆盖 `eflow_tt` / `eflow_wo` / `eflow_cr` / `eflow_pm` / `eflow_site` / `person`；文件头已声明「不再依赖外部 eflow.sqlite3 中间文件」（`:1-4`）。**读这些表一律读主库。**
- **`data/eflow/eflow.sqlite3` 的同名表不是同步结果**：它是旧流水线遗留快照 + 报告中间库。`eflow_data_mapper.save_to_sqlite()`（`backend/services/eflow/eflow_data_mapper.py:827`）在 sqlite 模式下仍会整表 `replace` 写入（报告生成副产品），PG 模式下已明确跳过。新代码**不得**把 eflow.sqlite3 当作 `eflow_tt` 的数据源——读到的是某次报告生成时的快照，会静默过期。
- **告警两表是例外**：`realtime_alarm` / `history_alarm` 由 `eflow_alarm_service` 双写主库与 eflow 副本，页面只读主库；双 schema 下的换表风险与落地写法见上文「新增 upsert 目标前的自检」中的告警段。
- **物理文件仍有合法读者**（sqlite 模式）：`packet_loss_service` 读其中站点信息（`backend/services/packet_loss_service.py:64`；PG 模式经 `get_raw_connection` 映射到 `eflow` schema），`apps/eflow_Q&A/tools/gen_datadict.py` 用它生成数据字典。删文件或停掉中间库写入前，先处理这两处。
- 结论出处：`PROJECT_DOCS.md` §2 Phase 0.5 / 0.7（2026-08 查代码闭环，历史档案）；本节是现状固化，后续以本节为准。

### `master_site_db` 整表重建：项目自有列必须登记 + 缺列不得拖垮页面（2026-11-22）

`master_site_db` 是用 CMDB 主数据 xlsx **整表 `replace`** 重建的
（`backend/scripts/refresh_master_site.py`，`if_exists="replace"`）。源表里没有的
**项目自有列**必须写进该脚本的 `SUPPLEMENTARY_COLUMNS`，否则下一次重建会静默洗掉：

| 列 | 语义 | 是否源表自带 |
| --- | --- | --- |
| `starlink_recently_migrated` | 是否近期迁星链（NUR 站点维度） | 否 |
| `site_type` | 站点物理形态 `Indoor` / `Outdoor`（GIS 筛选项与站点弹窗） | 否 |
| `power_sources` | 供电方式（GIS 高级筛选） | 否 |

**2026-11-21 事故后果**：那一轮重建洗掉 `site_type` / `power_sources`，并把源表头
`New SuperVisor` 落成 `new_supervisor`（旧列名 `supervisor` 消失）。因为
`backend/routers/gis.py` 的 `/sites`、`/realtime/sites` 把 `site_type` 写死在 SELECT，
PG 直接 `UndefinedColumn` → 两个端点 500 → **GIS 地图与实时告警地图全空**；
`personnel` 的 stats、`site_availability_service` 的筛选器与站点表也因写死
`supervisor` / `site_type` 而读表失败（错误只进日志，页面看起来像「没数据」）。

处置与防线（都在版本库里）：

- **恢复存量值**：`backend/scripts/restore_master_site_columns.py --source <旧快照 app.db>`
  —— 缺列则 `ADD COLUMN`，按 `site_id` → 规范化站点名从旧快照回填，**只填 NULL/空串**，
  不覆盖已有值、不删任何数据；`--dry-run` 先看覆盖数。
- **防再丢**：`SUPPLEMENTARY_COLUMNS` 已含上述三列，重建时按 `site_id` 抢救并并入
  同一次写入（读旧表失败会中止，不再「读失败 → 空值 → 照常 replace」）。
  新增项目自有列时**先登记再重建**。
- **防 500**：站点查询的列名一律按 `table_columns()` 探测后拼接，缺列退化为
  `NULL`/`''`，单个可选列缺失不能让整条查询 500。`supervisor` 的消费方按
  `supervisor` → `new_supervisor` 顺序取（`gis.py::_resolve_filter_cols`、
  `site_availability_service.load_master_sites`、`personnel._fme_site_index`、
  `eflow_data_mapper._pick_col`）。
- **回归守卫**：`tests/test_master_site_supplementary.py`（补充列抢救与默认值）、
  `tests/test_gis_schema_tolerance.py`（缺列时 SELECT 退化、`supervisor` 改名兜底）。

## 运行时目录

| 目录 | 内容 | 备注 |
| --- | --- | --- |
| `db/` | `app.db`、`instance.lock`、`uploads/` | `instance.lock` 是单实例锁，异常退出残留时按 `13-operations.md` 处理 |
| `data/<任务名>/` | 各任务落盘产物（battery、eflow、ms_sla、ip_trace、incident_overview…） | 报表文件的"最新版"来源页是任务报告文件页 |
| `backend/data/` | `settings.json`（目前仅 `remote_host`）、`sp/*.xlsx` 中间产物 | |
| `backend/services/auth_cache/` | 上游站点会话 cookie 缓存 | 由 `services/auth_manager.py:81` 维护，含有效凭据，**不得外发** |
| `data/app.db`、`data/my_app.db` | 0 字节的**历史路径遗留**（已实测根目录下没有同名文件） | 已不使用，可清理 |

## 给新功能作者的检查清单

1. 占位符统一写 `?`；
2. upsert 用 `dialect_helpers.upsert`，不要手写 `INSERT OR REPLACE`；
2b. **新增 upsert 目标表时先确认冲突列上有唯一约束**（见"动态表的唯一约束从哪来"）——
    SQLite 下缺约束不报错，只有 PG 会拦，别让 SQLite 测试给你假绿；
3. 中文/长表名注意 63 字节截断；
4. 结构表变更走 Alembic，动态采集表不要塞进 migration；
5. 新库文件优先落到 `data/<任务名>/`，并在本页的"独立 SQLite"表格里补一行。
