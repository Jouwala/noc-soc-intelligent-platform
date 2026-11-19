# ops/deploy — 02 副本的运维脚本目录

本目录是隔离副本（`D:\02_olbr_portal`）的运维脚本目录，由第一轮重构批 C
从仓库根的 `scripts\` 搬入。入口：

- 根 `start.bat` / `stop.bat` → `start_all.ps1` / `stop_all.ps1`
  （一键启动 / 停止本副本的四个子系统：my_app、portal、nginx、WhatsApp bot）
- `ops/shortcuts/` 里的桌面快捷方式 bat（状态、干跑、打开首页/日志等）
- `setup-git-hooks.mjs`：把 `core.hooksPath` 指向仓库内的 `.githooks/`
- `post_sync_fix_paths.ps1`：checkout / merge 后把两个监控脚本里的本机
  绝对路径改成本机副本根目录（由 `.githooks/post-checkout`、`post-merge` 调用）
- `local_authority_tools.py`：迁移时校验 / 复制 SQLite 本地权威三表
  （`credentials`、`task_schedule_config`、`email_recipients`）
- `portal-ctl.ps1`：**生产启停总控（2026-11-18 起 02 即生产）**。`-Action start|stop|restart|status`：
  `start` 清掉 `stopped.flag` 后 `schtasks /run` 三个自愈任务（与 5 分钟周期同一路径），再等端口就绪；
  `stop` 走 my_app `stop.bat` + 门户 `stop_services.ps1` + 02 的 `nginx -s quit`；`status` 打端口归属/健康/任务状态。
  桌面入口见同目录 `olbr-start.bat` / `olbr-stop.bat` / `olbr-status.bat`（快捷方式已建）。
- `post-cutover-check.ps1` + `olbr-check.bat`：**生产检查点（手动执行，刻意不挂定时）**。
  把 2026-11-19 切换后那次人工复核的检查项固化成一条命令：端口与进程归属（80/443 必须归本树
  nginx）、HTTP 探活（80/443、7038 health 的 `db_mode=pg`、7090、9267/9268）、openapi 特征路由
  （证明跑的是本树代码）、近 N 小时任务失败率与 Top 失败任务、17 个原生看板（预热后逐页核对
  failed / stale / 组件 error / 数据里最新日期）、关键看板数据新鲜度（CXO、CN KPI、OLIB、可用率、
  MW、丢包、电池、SOC、MS SLA、eFlow）、WhatsApp bot 与 notify 心跳及 4 个计划任务指向、外部
  EWS 可达性（只报告不判失败）。`-Render` 额外用 Playwright 渲染 10 个看板页抓 console error。
  **只读为主**：唯一的写操作是触发原生看板预热（`-NoPrewarm` 可关）和在
  `TMP\post-cutover-check\<时间戳>.json` 落一份报告（不入库）。有 FAIL 时 exit 1。
  用法：双击 `olbr-check.bat`，或
  `powershell -NoProfile -ExecutionPolicy Bypass -File ops\deploy\post-cutover-check.ps1 [-Render] [-NoPrewarm] [-SinceHours 12]`。
  默认 ~20 秒；`-Render` 约 +60 秒。伴生渲染脚本：`post-cutover-render.py`（缺 playwright 自动 SKIP）。
- `cutover/`：**生产切换（D:\01 → D:\02）专用**
  - `snapshot-prod-state.ps1`：切前只读快照（端口归属 / 守护任务状态 / 02 的 .env
    端口键 / 两侧 nginx.conf / 三表行数），落 `TMP\cutover\<时间戳>\`，不入库；
  - `cutover-to-02.ps1`：**切**。默认干跑，`-Apply` 才动；停 01（自愈任务 / my_app / 01_RPA 门户 / nginx）→
    搬门户状态 → 用 01 的 `backend\.env` 生成 02 的（路径改写 + 调度器 0 + CA 证书）→ 生产端口 nginx →
    用 02 自带的 `apps\my_app\start_hidden.ps1`、`apps\portal\start_hidden.ps1` 起栈 → 探活 → 写 manifest。
    **不要用 `start_all.ps1` 切生产**：它是隔离启动器（生产端口直接 throw、强制关调度器、DATABASE_URL 指 55432）；
  - `rollback-to-01.ps1`：切换失败时把线上拉回 01（用 02 自己的 `stop.bat` 停栈 → 还原 `.env.bak_cutover_*` →
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

副本里有 `.smoke-runner` 标记，`smoke-local.ps1` / `smoke-watcher.ps1` /
`install-smoke-watcher.ps1` / `smoke-queue-status.ps1` 都以它作为「这是不是
冒烟副本」的判据；在生产 02 根或旧的 `D:\02_olbr_portal_smoke` 上直接拒绝执行。

- `ops/smoke/smoke.ps1`：真正的检查引擎（§8.1–§8.4 + 离线 pytest，PR #26–#43
  已反复使用）。只在 `.smoke` 副本里跑；根目录按脚本位置推导，不写死盘符。
  §8.2 的权威源默认取副本上一级项目根的 `apps\my_app\db\app.db`（可用
  `-AuthSource` 指定），不再读 `D:\01_olbr_portal` 的旧库。
  `smoke-local.ps1` 只是薄壳：UTF-8 BOM 让 PS 5.1 能解析、
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
- `smoke-reload-watcher.ps1`：改 `smoke-watcher.ps1` 后停旧进程并
  `install-smoke-watcher.ps1 -Start`（避免计划任务 IgnoreNew 仍跑旧代码）。

### 冒烟值班：零 LLM token（推荐）

整条链路设计就是 **本机脚本消耗 GitHub API，不经过对话里的 Agent**：

| 步骤 | 谁做 | token |
| --- | --- | --- |
| quality 全绿 | GitHub Actions | 0 |
| 检出 PR、checkout、跑 `smoke.ps1`、贴评论、打 `smoke-passed` | 计划任务 `OLBR_SmokeWatcher` | 0 |
| 合并 | `auto-merge.yml`（认评论里的 `smoke-result` + 标签） | 0 |

**值班人 / 你本机只看这三处即可，不必开 Agent 盯：**

```powershell
cd D:\02_olbr_portal\.smoke   # 以下三个入口都必须在副本根里跑（靠 .smoke-runner 判定）

# 1) watcher 在不在（每周或怀疑挂了时）
powershell -NoProfile -File ops\deploy\install-smoke-watcher.ps1 -Status

# 2) 有没有 PR 在等冒烟 / 标签过期（随时，几秒）
powershell -NoProfile -File ops\deploy\smoke-queue-status.ps1

# 3) 最近一轮发生了什么（失败时）
Get-Content D:\02_olbr_portal\.smoke\TMP\smoke-watcher.log -Tail 30
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
- `my_portal_ctl.ps1`、`write_nginx_conf.ps1`：§11-5 自仓库根归位至此；
  两者都按自身位置向上推导仓库根，`ops/shortcuts/` 的 bat 已指向新位置

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

