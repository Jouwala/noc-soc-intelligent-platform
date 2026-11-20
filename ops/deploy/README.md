# ops/deploy — 02 的运维脚本目录

本目录是 `D:\02_olbr_portal` 的运维脚本目录，由第一轮重构批 C 从仓库根的
`scripts\` 搬入。**2026-11-18/19 起 02 就是生产树**（80/443/7038/7090/9267/9268，
PG 5432），所以本目录同时装着「生产启停」与「便携/冒烟副本的隔离启动」两套工具 ——
它们靠根目录的**角色标记**区分，见下节。入口：

- 根 `start.bat` / `stop.bat`：**按标记分流**（2026-11-19 改）
  - 有 `PRODUCTION.flag` → `portal-ctl.ps1 -Action start|stop`（生产栈）
  - 否则 → `start_all.ps1` / `stop_all.ps1`（隔离副本）
- 根 `start.sh` / `stop.sh` → `start.sh` / `stop.sh`
  （Linux 一键启动 / 停止本副本，对标 Windows 版；要求 POSIX sh + GNU coreutils）
- `ops/shortcuts/` 里的分步入口 bat（状态 / 启停 / nginx 只读检查 / 打开首页与日志 / 重建桌面快捷方式）：
  **只转发**到本目录的 `portal-ctl.ps1` 与 `olbr-*.bat`，自身不含启停逻辑（2026-11-19 改写）
- `setup-git-hooks.mjs`：把 `core.hooksPath` 指向仓库内的 `.githooks/`
- `post_sync_fix_paths.ps1`：checkout / merge 后把两个监控脚本里的本机
  绝对路径改成本机副本根目录（由 `.githooks/post-checkout`、`post-merge` 调用）
- `local_authority_tools.py`：迁移时校验 / 复制 SQLite 本地权威三表
  （`credentials`、`task_schedule_config`、`email_recipients`）
- `check_runtime_parity.py`：**运行时状态一比一核对（只读，2026-11-19 加）**：
  `python ops\deploy\check_runtime_parity.py [--source D:\01_olbr_portal] [--target D:\02_olbr_portal]`。
  对比门户 `auth-data/portal-auth.db`（账号、team 分配、待审申请、team_scope）、
  `portal-default.json`、my_app `backend\data\*.json`（含看板目录条目数与分组计数，
  CXO View / Friends Link 缩水会直接 FAIL）、本地权威表与 `.env` 键。
  缺东西 exit 2，单纯内容不同只给 WARN。切完、回退后、改过用户或目录后都跑一次。
  ⚠️ 2026-11-19 的「CXO View 只剩 3 条、OSS Portal Login 只剩 1 个、team 分配丢失」
  就是只查存活不查内容的检查方式漏掉的。
- `portal-ctl.ps1`：**生产启停总控（2026-11-18 起 02 即生产）**。`-Action start|stop|restart|status`：
  `start` 清掉 `stopped.flag` 后 `schtasks /run` 三个自愈任务（与 5 分钟周期同一路径），再等端口就绪；
  `stop` 走 my_app `stop.bat` + 门户 `stop_services.ps1` + 02 的 `nginx -s quit`；`status` 打端口归属/健康/任务状态。
  桌面入口见同目录 `olbr-start.bat` / `olbr-stop.bat` / `olbr-status.bat`（快捷方式已建）。
- `post-cutover-check.ps1`：**切后过夜复核（一条命令）**。端口与进程归属（80/443 必须归本树
  nginx，带启动时长）、HTTP 探活（80/443、7038 `db_mode=pg`、7090、9267/9268）、openapi 特征路由、
  近 N 小时任务失败率 + Top 失败任务、17 个原生看板逐页 failed/stale/组件 error/数据最新日期、
  关键看板数据新鲜度、WhatsApp bot/notify 心跳与计划任务指向、EWS 可达性（只报告），
  以及 **8b 运行时状态一比一**（调 `check_runtime_parity.py`，只读）、8c 邮箱通道降级、8d 仪表盘报告条目。
  `-NoPrewarm` 跳过预热、`-SinceHours N` 改失败率窗口、`-Render` 追加 Playwright 渲染探针
  （10 个看板页，缺依赖自动 SKIP）。**有 FAIL 时 exit 1**，报告落 `TMP\post-cutover-check\*.json`。
  手动双击 `olbr-check.bat`；计划任务入口 `olbr-check-task.bat`（不 pause，输出重定向到 TMP）。
- `cutover/`：**生产切换（D:\01 → D:\02）专用**
  - `snapshot-prod-state.ps1`：切前只读快照（端口归属 / 守护任务状态 / 02 的 .env
    端口键 / 两侧 nginx.conf / 三表行数），落 `TMP\cutover\<时间戳>\`，不入库；
  - `cutover-to-02.ps1`：**切**。默认干跑，`-Apply` 才动；停 01（自愈任务 / my_app / 01_RPA 门户 / nginx）→
    搬门户状态 → 用 01 的 `backend\.env` 生成 02 的（路径改写 + 调度器 0 + CA 证书）→ 生产端口 nginx →
    用 02 自带的 `apps\my_app\start_hidden.ps1`、`apps\portal\start_hidden.ps1` 起栈 → 探活 → 写 manifest。
    **不要用 `start_all.ps1` 切生产**：它是隔离启动器（强制关调度器、DATABASE_URL 指 55432、
    起便携 PG 与 8080/8443）。2026-11-19 起它在生产树上会直接 throw（见「角色标记」一节）；
  - `rollback-to-01.ps1`：切换失败时把线上拉回 01（用 02 的 `apps\my_app\stop.bat` +
    `apps\portal\stop.bat` + `nginx -s quit` 停栈 → 还原 `.env.bak_cutover_*` →
    恢复并触发 01 的守护任务 → 探活），**不碰数据库**；先 `-DryRun` 再实跑；
  - 操作底稿见 `docs/workspace/cutover-2026-11-20.md`。
- `sync-ui-config-from-01.ps1`：把生产（`D:\01_olbr_portal`）的三份「全局 UI 配置」
  （`ui_tool_visibility.json` / `ui_tool_order.json` / `ui_menu_layout.json`）同步到本副本。
  默认试运行，`-Apply` 才写；**写之前先校验**：从本副本自己的代码取真相源
  （`ui_tool_order.py` 的 `VALID_TOOL_KEYS`、`App.tsx` 的 `VALID_PAGES`），未知 key
  整批拒绝（前端对未知页面键是静默丢弃，坏了不报错，只会让页面从侧栏消失）；覆盖走
  `.tmp` + `Move-Item` 原子替换（直接 `Copy-Item` 覆盖时，读方可能读到半截）；旧文件留
  `*.bak_<时间>_from01`（每份只留最近 3 份），运行记录写 `TMP\sync-ui-config.log`；
  目标推导出落在生产目录下时直接拒绝执行。
- `ensure_portal.ps1`：门户保活（计划任务调用）
### 冒烟运行副本：`<项目>\.smoke`（2026-11-19 起）

02 现在是生产，冒烟**不能**在生产树里跑：它会重建 `apps\_fe_dist`、按副本根
重写 `nginx.conf`，并在本副本根下 `nginx -s quit`。2026-11-18 的应急做法是把
隔离栈放到项目外的 `D:\02_olbr_portal_smoke`，但那份树是手工搭的、仓库里
没有脚本能重建，junction 还指回项目 —— 违反便携副本自包含的约定。

现在改为项目内副本，由 **`provision-smoke-runner.ps1`** 生成/修复（默认干跑，
`-Apply` 才写；目标默认 `<项目>\.smoke`，已在 `.gitignore`）：

```powershell
# 生成 / 修补副本（clone + junction + 复制 nginx/构建产物 + 从生产库克隆 schema
# 与三张本地权威表）
powershell -NoProfile -File ops\deploy\provision-smoke-runner.ps1 -Apply

# 改计划任务指向副本（需管理员）
powershell -NoProfile -File D:\02_olbr_portal\.smoke\ops\deploy\install-smoke-watcher.ps1 -Start
```

> ⚠ **完整性标签**：项目内副本会继承工作区根的 **Low** 完整性标签；Low 的 `nginx.exe`
> 在 `smoke.ps1` 的隐藏控制台链里会以 `0xC0000142`（DLL 初始化失败）起不来，start_all 直接失败。
> 提权运行时 provision 会把副本根改成 Medium（子文件随继承）；非提权会话生成后按提示补跑
> `icacls "<项目>\.smoke" /setintegritylevel (OI)(CI)Medium /T`。2026-11-19 实测，改完 watcher 冒烟通过。

副本里有 `.smoke-runner` 标记，`smoke-local.ps1` / `smoke-watcher.ps1` /
`install-smoke-watcher.ps1` / `smoke-queue-status.ps1` 都以它作为「这是不是
冒烟副本」的判据；在生产 02 根或旧的 `D:\02_olbr_portal_smoke` 上直接拒绝执行。

- `ops/smoke/smoke.ps1`：真正的检查引擎（§8.1–§8.4 + 离线 pytest，PR #26–#43
  已反复使用）。只在 `.smoke` 副本里跑；根目录按脚本位置推导，不写死盘符，缺
  `.smoke-runner` 标记直接拒绝。`smoke-local.ps1` 只是薄壳：UTF-8 BOM 让 PS 5.1 能解析、
  `TMP\smoke.lock` 防并发、校验 HEAD == `-HeadSha`、写
  `TMP\smoke-pr-<PR>-<sha7>.md`（干跑写 `.dryrun.md`，不覆盖真跑报告）。
- `smoke-watcher.ps1`：每 60 秒轮询 GitHub，CI 变绿且无 `smoke-passed` 的
  open PR 就地冒烟、贴评论、通过才打标签；贴前重读 PR，head 变了就作废。
  **冒烟前 `git fetch` 并 `checkout --detach` 到该 PR head，finally 切回原分支**
  （Grok 当年的 checkout 由它接管）；工作树有未提交的已跟踪改动时跳过该 PR，
  不覆盖他人在途工作。Token 依次取 `SMOKE_GITHUB_TOKEN`/`GITHUB_TOKEN`/
  `GH_TOKEN`，都没有时回落本机 `gh auth token`（只在内存，不落盘）。
  开头有硬守卫：根目录落在 `D:\01_olbr_portal` 下、或缺 `.smoke-runner` 标记时直接拒绝执行。
- `install-smoke-watcher.ps1`：把 watcher 注册成常驻计划任务 `OLBR_SmokeWatcher`
  （登录自启、崩溃每分钟重启、单实例）；`-Status` 查、`-Uninstall` 卸、
  **改 `smoke-watcher.ps1` 后跑 `-Start` 重启进程**（否则内存里仍是旧逻辑）。
  日志 `TMP\smoke-watcher.log`。
- `maintain-smoke-runner.ps1`：runner 的例行维护（2026-11-20 新增）。
  runner 的数据是**铺装那一刻**的拷贝，生产新增凭据后 §8.2「三表行数与源一致」
  会误报失败（PR #182 实测：credentials 21 vs 20，代码修好后仍被挡一轮）。四项维护：
  `-SyncData`（生产 app.db → runner，只读生产）、`-UpdateCode`（runner 跟
  `platform/main`）、`-CleanTmp`（清理 >N 天的冒烟日志/报告/迁移备份，watcher 日志与
  `smoke.lock` 永不删）、`-Reprovision`（补 junction/配置/构建产物，加 `-RebuildDb`
  重铺 app.db）。默认干跑，`-Apply` 才执行；`-Status` 看状态。
  **接线**：`smoke-local.ps1` 每次真冒烟前自动跑 `-SyncData`（失败不挡冒烟、只记录）；
  计划任务 `OLBR_SmokeMaintenance`（每天 04:10，`-Daily`）与
  `OLBR_SmokeReprovision`（每周日 04:40，`-Reprovision -SyncData -CleanTmp`），
  用 `-InstallTask` 注册（**要求项目根已含本脚本，即该 PR 合并之后**；
  `provision-smoke-runner.ps1 -Apply -InstallTask` 也会一并注册）。
  日志 `TMP\smoke-maintenance.log`。
- `smoke-reload-watcher.ps1`：改 `smoke-watcher.ps1` 后停旧进程并
  `install-smoke-watcher.ps1 -Start`（避免计划任务 IgnoreNew 仍跑旧代码）。

### 冒烟值班：零 LLM token（推荐）

整条链路设计就是 **本机脚本消耗 GitHub API，不经过对话里的 Agent**：

| 步骤 | 谁做 | token |
| --- | --- | --- |
| quality 全绿 | GitHub Actions | 0 |
| 检出 PR、checkout、跑 `smoke.ps1`、贴评论、打 `smoke-passed` | 计划任务 `OLBR_SmokeWatcher` | 0 |
| 冒烟前同步 runner 的本地权威表 | `smoke-local.ps1` 自动调 `maintain-smoke-runner.ps1 -SyncData` | 0 |
| runner 数据/代码例行维护、临时文件清理 | 计划任务 `OLBR_SmokeMaintenance` / `OLBR_SmokeReprovision` | 0 |
| 合并 | `auto-merge.yml`（认评论里的 `smoke-result` + 标签） | 0 |

**值班人 / 你本机只看这三处即可，不必开 Agent 盯：**

```powershell
# 1) watcher 在不在（每周或怀疑挂了时）
powershell -NoProfile -File ops\deploy\install-smoke-watcher.ps1 -Status

# 2) 有没有 PR 在等冒烟 / 标签过期（随时，几秒）
powershell -NoProfile -File ops\deploy\smoke-queue-status.ps1

# 3) 最近一轮发生了什么（失败时）
Get-Content D:\02_olbr_portal\TMP\smoke-watcher.log -Tail 30
```

**只有这些情况才值得 @ Agent：** 日志里连续 `smoke-local 退出码=1` 且同一项稳定失败；
`smoke-queue-status` 长期 `blocked` 且工作树确实干净；watcher 进程不存在且 `-Start` 拉不起来。
Agent 在对话里长轮询 CI（`gh pr checks` 循环等待）**极耗 token，已停用**——那是给人看的快查，
应交给 watcher 的 60 秒轮询。

手工补跑（仍 0 Agent token）：`powershell -NoProfile -File ops\deploy\smoke-watcher.ps1 -OnlyPr <N> -Once`
（`-OnlyPr` 不与常驻 watcher 抢 pid；并发靠 `TMP\smoke.lock`。）
- `smoke-queue-status.ps1`：只读冒烟队列（待跑 / 等 CI / 阻塞 / 已通过），
  不抢 watcher 单实例锁；Agent 值班先跑它，有 `ready` 再盯 watcher 或手工
  `smoke-local.ps1`。`gh pr list` 默认 remote 是别的仓，**查 PR 必须**
  `--repo Jouwala/noc-soc-intelligent-platform` 或本脚本。
- **单条 open PR 的 PS 坑（2026-11-18）**：GitHub API 只返回 1 个结果时
  `Invoke-RestMethod` 是单个对象而不是数组，`foreach` 会枚举属性导致
  watcher/队列脚本「看不见任何 PR」。统一用 `Normalize-GitHubList` 归一化，
  **凡是取「列表」的调用点都必须过它**（当前共 10 处：watcher 7 处 — PR 列表
  ×2、评论 ×2、标签 ×2、workflow_runs ×1；队列脚本 3 处）。漏一处就只在
  「恰好 1 条」的边界上出错，平时看不出来。
- **重启 watcher 会强杀进程**：`install-smoke-watcher.ps1 -Start` 会 `Stop-Process -Force`
  掉正在跑的旧 watcher。若它当时正在冒烟，子进程可能残留 `TMP\smoke.lock`，
  下一次冒烟会直接 `exit 2`。处理：确认没有 `smoke-local` 在跑之后手动删
  `TMP\smoke.lock`（脚本刻意不替别人删锁，见 `smoke-local.ps1`）。
- **进程识别按路径段边界匹配**：判定「这是不是 watcher 进程」用
  `'(^|[\s\\/])smoke-watcher\.ps1'`，并排除 `$PID`。早先的 `'smoke-watcher\.ps1'`
  会把 `install-smoke-watcher.ps1` 自己也算成 watcher——`smoke-reload-watcher.ps1`
  通过安装脚本重启时，安装进程会先把自己杀掉，后面的 `Start-ScheduledTask` 永不执行。
- **2026-11-14 已与 Grok 对齐**：PR #44 同 head 下本地链路 18/18 逐项一致
  （见 `TMP\smoke-pr-44-vs-grok.md`）。对齐后方可停 Grok；停之前不要动
  agent-orchestrator 的 webhook。
- `bundle_data.ps1`：把生产 PostgreSQL 快照灌进副本便携实例
- `my_portal_ctl.ps1`、`write_nginx_conf.ps1`：§11-5 自仓库根归位至此，两者都按自身位置向上推导仓库根。
  **2026-11-19 起 `ops/shortcuts/` 不再调用它们**（那套入口改为转发 `portal-ctl.ps1` / `olbr-*.bat`）。
  `write_nginx_conf.ps1` 只用于隔离副本与打包：它按 `.env` 取端口，而本树 `.env` 仍是隔离端口
  （8080/8443/7039/7091/9277），在生产树上跑会用 8080/8443 覆盖线上 80/443 的 `nginx.conf` ——
  `ops/shortcuts/5-nginx-check.bat` 已因此降级为只读，需要重建生产配置时显式给端口参数。
- `build-zip.mjs`（+ `lib/zip-package.mjs`、`lib/zip-writer.mjs`、`lib/zip-dir.mjs`）：
  ZIP 交付 Phase 1（#146）的构建脚本——白名单组装 + 排除断言 + 凭据扫描 +
  行尾规范化 + `build-info.json`/SHA256，产出可重复构建的应用 ZIP；
  流程见 `docs/workspace/zip-build-release.md`
- `noc-paths.ps1`：ZIP 交付 Phase 1（#146，P1-6）的外部目录解析——`start_all.ps1` /
  `stop_all.ps1` 共用（唯一真相源）；设了 `NOC_HOME` 时配置/数据/日志/运行时一律
  走部署根下的外部目录，版本目录零写入；不设则与旧行为一致。见
  `docs/workspace/zip-external-dirs.md`
- `start.sh` / `stop.sh` / `noc-paths.sh`：ZIP 交付 Phase 1（#146，P1-4）的 Linux
  编排器——对标 `start_all.ps1` 六步 + pid 文件 + 熔断 + 干跑；`noc-paths.sh` 与
  `noc-paths.ps1` 同一套模式/变量/断言；nginx 配置展开用内置 awk 展开器（同一模
  板、同一占位符集）。ZIP 产物内 `.sh` 带可执行位（构建时按后缀置 0o755）；仓库
  检出后须 `chmod +x` 或用 `sh` 显式调用。见 `docs/workspace/zip-external-dirs.md` §6
- `runtime-manifest.json`：运行时发行包清单（`runtime-build.yml` 驱动下载与
  SHA256 校验；`url`/`sha256` 为空的条目禁止构建，须从官方渠道核实后补齐）

## 角色标记：PRODUCTION.flag / ISOLATED_COPY.flag（2026-11-19）

同一套脚本要服务「这台机器是生产」和「这台机器是便携/冒烟副本」两种角色，角色
**只由根目录的两个标记决定，且必须二选一**（两个都是本机运行态文件，不入库）：

| 标记 | 谁写 | 含义 | 端口 |
| --- | --- | --- | --- |
| `PRODUCTION.flag` | 切生产时人工放一个**空文件**（2026-11-19 已在 `D:\02_olbr_portal` 建好，**勿删**） | 生产树，任务调度器在跑 | 80/443/7038/7090/9267/9268，PG 5432 |
| `ISOLATED_COPY.flag` | `provision-smoke-runner.ps1` / 打包脚本 | 便携副本 / 冒烟副本 | 8080/8443/7039/7091/9277，便携 PG 55432 |

> ⚠️ `PRODUCTION.flag` 不只是「打个标签」：`post_sync_fix_paths.ps1`（git post-checkout
> 钩子）靠它决定 nginx 看门狗写 80 还是按 `.env` 写 8080。**删掉它，下一次 `git checkout`
> 就会把生产看门狗的 `NginxPort` 改成 8080（脚本现在会先打 WARN，但仍会写），5 分钟周期里连探两轮失败就去重启 nginx**
> （2026-11-19 15:36 实测正在发生）。切生产后 `PRODUCTION.flag` 与「`ISOLATED_COPY.flag`
> 已删」两件事必须同时成立。

守卫生效的地方（都是「角色不明就不跑」，不是静默降级）：

- 根 `start.bat` / `stop.bat`：按标记分流到 `portal-ctl.ps1` 或 `start_all.ps1` / `stop_all.ps1`；
- `start_all.ps1`：见到 `PRODUCTION.flag` 直接 throw（否则会改 `backend\.env`、起 55432 的第二套 PG）；
- `stop_all.ps1`：见到 `PRODUCTION.flag` 直接 throw，并要求 `ISOLATED_COPY.flag` 存在（两个都没有同样拒绝）——它会 `nginx -s quit` 关掉线上入口，却停不到生产后端；
- `my_portal_ctl.ps1`：两种角色下都只允许 `0-status`（老控制器不再驱动启停）；
- `ops/quality/check_all.ps1`：步骤 1 要求标记二选一，步骤 2（`.env` 端口断言）只在隔离副本上跑；
- `post_sync_fix_paths.ps1`（git 钩子）：据 `PRODUCTION.flag` 决定 nginx 看门狗端口写 80 还是按 `.env`；两个标记同时存在时拒绝执行，两个都没有时打 WARN（`-Force` 是显式豁免）。

背景与后果见 `docs/adr/0003-production-vs-isolated-markers.md`（ADR 0002 的隔离前提已被切生产取代）。

## 已停用：从 RPA（主仓库）来的同步

- `sync-engine.mjs` 和 `sync-policy.mjs` **已停用**，只保留文件备查。
- `sync_from_main.ps1`（同步引擎的薄壳）和 `bundle_data.ps1`
  同样属于已停用的同步链：它们的默认源仍指向旧编辑区
  `D:\01_RPA`，**禁止对这个新目录跑从 RPA 来的同步**。
- 本仓库不再从 `D:\01_RPA` 同步；根 `sync-now.bat` 已随批 C 删除。
  如需取数 / 取码，改走主仓库的正常开发流程，不要在这里恢复同步入口。

## 已停用：生产目录里的初版同步脚本

- `D:\01_olbr_portal\scripts\sync-runtime-config-to-02.ps1`（2026-11-16 初版）**已被本目录的
  `sync-ui-config-from-01.ps1` 取代**：初版只有 `Copy-Item`，没有校验、没有备份、覆盖非原子。
  生产机上那份留在原处容易跑错，**请删除**；以后只用本目录这份。

## 路径约定

- 所有脚本都用自身位置推导仓库根（`ops/deploy` 的上一级的上一级），
  不写死盘符；`start_all.ps1` / `stop_all.ps1` 要求副本带
  `runtime\`（python312 / node / postgresql16）与 `ISOLATED_COPY.flag`。
- 不要把 `apps/my_app/start_hidden.ps1` 之类的生产启动脚本搬到这里；
  隔离启动只走 `ops/deploy/start_all.ps1`。

## 从生产（01）同步代码改动 —— 三方合并，不是拷贝（2026-11-16 落地）

生产运行副本 `D:\01_olbr_portal` 的工作区改动要进本仓库时，**不能整目录拷**：
本仓库已完成两轮重构（`apps\` 归位 + `services\` 按域重组），与生产的目录
结构不再一一对应 —— 实测 67 个映射目标文件里 **43 个已分叉**，直接拷贝会
静默覆盖掉重构成果。

正确做法是**三方合并**：base = 生产仓库 `HEAD` 的 blob，ours = 本仓库工作区，
theirs = 生产工作区，逐文件用 `git merge-file --diff3` 合。

> 合并器**必须按字节处理**（Python）。用 PowerShell 会踩坑：PS 5.1 的控制台
> 编解码把 UTF-8 中文按 ANSI 解一遍，base 一侧全变乱码，**合并结果静默损坏**。

### 工具位置

- `TMP/sync-01-to-02/merge3.py` —— 三方合并器（字节精确；含路径映射表生成）
- `TMP/sync-01-to-02/resolve.py` —— 冲突解决（逐条判定，注释写明依据）
- `TMP/sync-01-to-02/pathmap.csv` —— 路径映射表（01 路径 → 02 路径）

`TMP\` 已 gitignore，不入库 —— 脚本是**可复用诊断工具**，按 AGENTS.md 第四节
第 7 条留在 `TMP\` 里，需要时按本节说明重新生成。

### 路径映射（01 → 02）

| 01 | 02 |
| --- | --- |
| `Task/my_app/` | `apps/my_app/` |
| `liberia-noc_soc-数据看板统一门户/` | `apps/portal/` |
| 根级长文档 | `docs/workspace/` |
| `backend/services/<模块>.py`（平铺） | `backend/services/<域>/<模块>.py`（域子包） |

### 同步后必做的三件事

1. **新文件的 import 改成域子包路径** —— 否则在 02 上 `ImportError`：
   `backend.services.ipran_ume_kpi_service` → `backend.services.ipran.ipran_ume_kpi_service`。
2. **运行时 JSON 不入库** —— `backend/data/ui_tool_*.json` 用
   `sync-ui-config-from-01.ps1 -Apply` 同步（带 key 校验 + 原子替换 + 备份），
   不要手工拷贝。
3. **跑 `agent-done-check --full` + 冒烟**（`smoke-local.ps1 -PrNumber <N> -HeadSha <sha>`）。

### 冒烟的坑

手动起过 PG（`pg_ctl -D runtime\pgdata -o "-p 55432"`）再跑冒烟，脚本预检会
发现 02 端口 55432 被占用 → `RESULT: REFUSED`（「可能有别的冒烟在跑」）。
**冒烟脚本自己会起 PG**（`start_all.ps1`），跑完 pytest 若手动起过，先停掉。

完整的执行记录（2026-11-16 那次，含冲突判定表与踩坑清单）见
`.agent-work/tasks/active/task-sync-01-to-02-20261116.md`。

