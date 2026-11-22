# AGENTS.md —— AI 进入本项目首先需要知道的信息

> 基准日期：2026-09-22　|　适用：`D:\02_olbr_portal` 工作区根目录
> 本目录（`D:\02_olbr_portal`）是 **NOC/SOC Intelligent Platform 工作区的总纲**。各子系统的细节一律看各自的 `AGENTS.md` 与手册，**不要**把子系统细节抄到这里。

## 一、先读什么（阅读顺序）

| 顺序 | 文件 | 作用 |
| --- | --- | --- |
| 1 | `AGENTS.md`（本文） | 工作区地图、工程原则、硬性约束、**自主执行契约（第五节）** |
| 2 | `docs\workspace\project-overview.md` | 整个工作区是干什么的 |
| 3 | `docs\workspace\architecture.md` | 三个主要系统如何连在一起 |
| 4 | `docs\workspace\TODO.md` | 当前在办的事 |
| 5 | 进入具体子系统后 | 读该子系统的 `AGENTS.md`，再读其 `docs/manual/` |

## 二、工作区地图

**这是一个独立的 git 仓库，顶层就是 `D:/02_olbr_portal`，与 `D:\01_RPA` 没有 git 关系。** 一次提交会同时影响本仓库里所有子系统，提交信息里要用 `feat(portal):` / `fix(assets):` 这类**前缀标明影响面**（沿用既有习惯）。改动前先 `git status` 看全域未提交变更，避免把别人的在途改动一起卷进你的提交。

### 主要子系统

| 目录 | 是什么 | 技术栈 | 该处文档入口 |
| --- | --- | --- | --- |
| `apps\my_app\` | **Data Insight MVP**（网络运维数据平台，自研主系统） | FastAPI + SQLite/PG + React | `apps\my_app\AGENTS.md`、`apps\my_app\docs\manual\` |
| `apps\portal\` | **Liberia NOC/SOC 数据看板统一门户**（聚合 FineBI 与 my_app 看板） | React 19 + Vite + Tailwind v4 | `apps\portal\docs\AGENTS.md`、`apps\portal\README.md` |
| `apps\whatsapp-kb-bot\` | **WhatsApp 知识库问答 bot**（与 my_app 同级，叶子名不改） | Node | `apps\whatsapp-kb-bot\docs\` |
| `apps\eflow_Q&A\` | **eFlow 知识库**（问答/排障文档，含 `tools\inspect_tt.py`） | Markdown + Python | `apps\eflow_Q&A\README.md` |
| `apps\agent-worker\`、`apps\control-plane\` | **多 worker 协作底座**（任务执行器 + 中央调度） | Python | `docs\workspace\agent-protocol.md` |
| `nginx-1.28.0\` | **本机生产 nginx**（80/443 的实际持有者，已 gitignore） | nginx | 见第四节第 6b 条 |
| `runtime\` | 捆绑运行时（Python/Node/PG），**只服务隔离副本与打包**；生产用系统 Python / Node / PG 5432 | — | `runtime\README.md` |
| `docs\`、`ops\`、`desktop_shortcuts\` | 文档、运维脚本、桌面快捷方式生成器 | — | 各自 `README` |

> `D:\01_RPA\nginx-1.28.0\` 已**不再持有本机 80/443**，不要改；`Y:\01_RPA\nginx-1.28.0\`
> （**.19 那台**在用）同样不要动（见第四节第 6b 条）。

### ⭐ 编辑区 ≠ 运行副本（2026-11-11 定；2026-11-18/19 切生产后改写）

| | 路径 | 角色 |
| --- | --- | --- |
| **生产（当前）** | `D:\02_olbr_portal` | 2026-11-18/19 起接管 80/443/7038/7090/9267/9268（PG 5432）。git 仓库、文档、守卫脚本与实际运行的东西**都在这里**；根目录有 `PRODUCTION.flag`。启停走 `ops\deploy\portal-ctl.ps1`（或桌面 `olbr_app` 的三个链接），不要再走 `start_all.ps1`。 |
| **回退目标（已退役）** | `D:\01_olbr_portal` | 切生产前的旧线，只有 `ops\deploy\cutover\rollback-to-01.ps1` 会碰它。日常**不要再往这里改代码、起服务**。 |

**工作流：在 `D:\02_olbr_portal` 改完 → `node ops\deploy\agent-done-check.mjs` → 改动走 PR；生产启停用 `portal-ctl.ps1 -Action start|stop|status`。** 只改源码、只重建前端、只重启一个进程都不算部署完成（见下）。

> ⚠ 从主仓库（`D:\01_RPA`）来的同步已随第一轮重构批 C 停用：根 `sync-now.bat` 已删除，`ops\deploy\sync-engine.mjs` / `sync-policy.mjs` 只保留备查，`sync_from_main.ps1` 禁止对这个新目录跑。不要在这里恢复同步入口。详见 `ops\deploy\README.md`「已停用」一节。

**角色标记（2026-11-19，两个互斥）**：根目录的 `PRODUCTION.flag` = 这台机跑生产端口；`ISOLATED_COPY.flag` = 便携/冒烟副本（`provision-smoke-runner.ps1` 写在 runner 里）。生产树只应有前者。两个都在或都不在时，`start.bat` / `stop.bat` / `start_all.ps1` / `stop_all.ps1` / `check_all.ps1` 一律拒绝执行（角色不明宁可不跑）。细节见 `ops\deploy\README.md`「角色标记」与 `docs/adr/0003-production-vs-isolated-markers.md`。

隔离副本（打包机 / 冒烟 runner）的启停仍走 `ops\deploy\start_all.ps1` / `stop_all.ps1`；**生产树上禁止用它们** —— 它们会改 `apps\my_app\backend\.env`、起 55432 的第二套便携 PG、往 8080/8443 起第二套服务。

**为什么「只改源码」「只重建前端」「只重启后端」都不算部署完成**（2026-11-11 实测事故）：

- 源码改了不会自动生效：前端是**构建产物**（`apps\_fe_dist`），后端是**常驻进程**，两者都不吃源码变更。
- 只改源码不重建 → 前端仍是旧界面；只重建不重启 → 后端仍是旧接口。
- **实测 split-brain**：前端 chunk 已含 `col_filters`（列头筛选），而 live 后端的 `/api/tables/{t}/data` OpenAPI 参数里**没有** `col_filters`、也没有 `/column-values` 路由 → 用户看到「列头筛选点了没反应」「选值模式永远显示没有可选值」，是**完全坏掉的新功能**而不是报错。
- 正确做法：重建前端 + 重启后端 + 读 live OpenAPI 对照源码，三者都做才算数。

**脚本不自动重启服务** —— 重启会打断正在看数据的人（第四节第 6 条）。需要重启时由人显式执行。

> （历史做法：根 `sync-now.bat` 曾把 `D:\01_RPA` 的源码同步进来，批 C 已随同步链一起删除；现在改代码直接在本仓库改。）

> ⚠️ **迁移/换目录时，`apps\my_app\db\app.db` 里的三张「本地权威表」不会自动跟过来**
> —— `credentials`（设置页凭据）、`task_schedule_config`（任务启停/cron）、
> `email_recipients`（邮件收件人）。它们由应用**固定写 SQLite**（不走 PG），
> `sync_pg_to_sqlite.py` 为避免 DROP 本地数据把它们列进 `SKIP_TABLES`，
> 所以「从 PG 重建 app.db」的路径天生带不出它们、也不会报错；搬目录/换机若只拷代码与 PG 快照，
> 这三张表就留在旧机器上。
> **2026-11-11 实测**：漏搬后 16 个任务同时报「拒绝以空密码登录」、任务管理页的
> 4 个手动停用任务全变回启用。检查/迁移命令：
>
> ```powershell
> python ops\deploy\local_authority_tools.py check   --source <旧app.db> --target <新app.db>
> python ops\deploy\local_authority_tools.py migrate --source <旧app.db> --target <新app.db>
> ```
>
> 迁移/换机时**先跑 `check`**：缺失就不要让新树接线上流量。细节见
> `docs\workspace\部署说明.md` 第五节。

### 环境拓扑（速查）

```
192.168.100.20:80/443  (nginx，本树 nginx-1.28.0)
 ├── /myapp/            → 127.0.0.1:7090   my_app 前端 (vite, base=/myapp/)
 ├── /api/              → 7090 → 127.0.0.1:7038   my_app FastAPI 后端
 └── /uniplam/          → 9267  统一门户自身（含 /uniplam/api/* 门户后端）
```

> FineBI 不经过本机 nginx：门户里的报表直连 `192.168.100.19/webroot/decision`。
> 主机名以 `apps\portal\src\config.ts` 的 `FALLBACK_ENV` 为准（换机房只改这一处）；
> 门户端口 9267、my_app 端口 7038/7090 见各启动脚本。细节见 `docs\workspace\architecture.md`。

## 三、核心工程原则

> 十条通用纪律，适用于本工作区所有子系统。与第四节的「硬性约束」相抵时，**以第四节为准** —— 那些是本仓库已实测验证的具体事实，本节是方向。

1. **架构与领域优先。** 计划阶段先明确业务目标、领域边界、模块职责、依赖方向与数据流，形成符合领域规律、面向长期维护且可持续演进的设计后再编码；不得以短期实现便利牺牲整体设计。设计必须完整，实现应当克制：不做推测性抽象，抽象延迟到**第二个真实用例**出现时才引入，单一场景直接实现。
2. **追求优雅的代码模块。** 模块高内聚、低耦合，用精简且稳定的接口封装内部复杂度，让职责、命名、依赖和扩展方式清晰自然；代码按单一职责拆分。**新增/新建文件单个不超过 500 行**，接近上限时优先重构模块边界。存量超限文件（`backend\task_registry.py` ≈117KB、`frontend\src\i18n.tsx` ≈1045KB、`frontend\src\App.tsx` ≈126KB）列入待重构清单，**不要求一次性拆分**，但改到它们时不得继续无度膨胀。
3. **保持边界与数据流清晰。** 协议模型（Pydantic `schemas\`）、领域模型、持久化模型（SQLAlchemy `models\`）与视图模型不得相互泄漏；数据必须在边界处完成校验和独立转换，避免跨层共享可变状态。
4. **安全与隔离默认开启。** 所有功能按多租户、多用户场景设计，明确认证、授权与数据隔离边界（my_app 的角色与凭据体系见 `apps\my_app\docs\manual\05-security-and-credentials.md`）；遵循最小权限，任何外部输入均视为不可信，敏感信息不得进入代码、日志或响应（落地写法见第四节第 2 条）。
5. **面向并发与故障设计。** 后端主动考虑幂等性、竞态、事务边界、超时、取消、重试、背压和资源释放；不得通过无边界重试、吞错或隐式共享状态掩盖问题。本工作区的 RPA 采集与 worker 任务尤其要显式处理「上游返回半截数据」与重复投递（投递前查重见 `apps\my_app\docs\manual\04-task-system.md`）。
6. **保障完整前端体验。** 控制渲染成本、异步状态与并发请求，保持清晰的 UI 结构；用户流程必须覆盖加载、空状态、错误、重试、反馈与可访问性。取不到数据显示 `—`，不得显示成 `0`。
7. **复用稳定的业务语义。** 优先复用已有模块与能力（my_app 的 `components/cards`、`utils/excelReport`、`components/DraggableClickModal` 等统一组件是**强制入口**，细则见 `apps\my_app\AGENTS.md` 第三节第 3 条），但不要仅因外形相似就过早抽象；确需重复实现时，必须在注释中说明其独立演进或暂不抽象的原因。**新增依赖前先核查本处已有依赖** —— 本仓库没有统一的根 `package.json`，`apps\my_app\frontend\package.json` 与门户 `package.json` 各自独立，Python 依赖看各任务目录的 `requirements.txt`（如 `apps\my_app\requirements.txt`）。先查这些文件、库的文档与类型定义，不臆断已有库缺功能；确需引入时选成熟且维护良好的库，不重复实现通用功能。
8. **为未来维护者保留上下文。** 代码、注释、测试与架构文档是跨越时间的协作媒介。非显然的设计决策、兼容约束、已知缺陷和临时方案，必须记录原因、影响范围、潜在风险与移除条件；技术债务要关联可追踪的归属（`docs\workspace\TODO.md` 条目），**禁止**留下没有上下文的裸 `TODO`。本仓库的 ADR 在 `docs\adr\`（0001 目录边界 / 0002 副本隔离 / 0003 生产 vs 隔离标记），重大架构决策也按日期追加到 `docs\workspace\architecture.md` 的决策小节，并按 `apps\my_app\AGENTS.md` 第三节第 8 条标注 `文件路径:行号` 出处。
9. **确保变更可验证、可观测、可回滚。** 每项改动都应行为可测试、运行状态可观测、故障可定位，并兼顾向后兼容和回滚路径；错误与日志必须保留诊断上下文，但不得泄露敏感信息。回滚手段在本仓库是实打实的：表结构走 `alembic downgrade`，进程有 `worker_watchdog.py` / `backend_watchdog.py` 守护，改大文件前先建 `_bak_<日期>_<说明>` 快照。
10. **删除优于兼容。** 内部路径重构时直接删除过时实现，**禁止**新增兼容层、deprecated shim 或双写逻辑；对外契约（`/api/*`、`/uniplam/api/*` 等稳定接口与数据库迁移）的兼容性按协议契约单独评估 —— 那是合同义务，不是迁就旧代码。

> **变更速查（本仓库真实命令）**。完整回归清单以 `docs\workspace\development.md` 第四节的表为准，不要在这里另起一套。
>
> - 通用：提交前 `git status --porcelain` 复查，没有运行时产物混入。
> - **收尾自检（第四节第 7 条的机器可验证版本）**：`git status --porcelain` 里 `??` 行必须为空或全部是你本次要提交的真文件；出现 `_*.txt` / `*.bak_*` / `TMP/` 之类的行说明探针落了错误的地方。怀疑某个文件为何没被跟踪时问 `git check-ignore -v <路径>`，它会把命中的规则与所在文件行号打出来。
> - my_app 前端：`cd apps\my_app\frontend` → `npm run check:i18n`（漏键守卫）→ `npm run build`（内含 `tsc`；产物必须落在 `apps\_fe_dist`，否则后端白屏）。
> - **my_app 文档一致性**：`cd apps\my_app` → `python tests\test_doc_consistency.py`（17 个用例，核对手册与站内帮助里「从代码抄来的数字」：任务数 / 页面数 / iframe 页数 / worker 组数 / 队列保留期 / 路由数 / 凭据分组数 / 组件数 / 文件体积，以及「每个页面都有按页刷新设置」「看板下推的每张表都有本机 PG 巡检且窗口同档」；**本地钩子与 CI 都会跑，文档与代码对不上会拦下提交**）。改动页面或任务后必须跑。详见 `apps\my_app\docs\manual\04-task-system.md`「文档一致性守卫」一节。
> - **my_app 帮助内容改动后**：`cd apps\my_app\frontend` → `npm run docs`（重生成 `15-user-help*.md` 与 `public\manual.html`）。
> - 门户前端：`cd apps\portal` → `npm run lint`（`tsc --noEmit`）→ `npm run build`。
> - **门户文档一致性**：`cd apps\portal` → `npm run check:docs`（6 个用例，核对 README/docs 里的看板条数 / 组件数 / 任务数；**本地钩子与 CI 都会跑**）。详见门户 `apps\portal\docs\development.md`「文档一致性」一节。
> - **门户 i18n**：`cd apps\portal` → `npm run check:i18n`（已内置进 `npm run build`，不通过会**中断构建**；本地钩子与 CI 也跑）。门户文案有**两本字典**：界面 `zh/en` 与看板目录 `zhDashboard/enDashboard`，后者键名来自 `src\data\initialDashboards.ts` 的**数据值**，不在代码里 —— 所以「加一条看板忘了补英文」「改了分类/部门/负责人名称」「en 侧的值还是中文」这三类最常见的门户事故，my_app 那份只扫代码的守卫查不到，必须两道都跑。详见门户 `apps\portal\docs\development.md`「i18n 守卫」一节。
> - **提交前守卫（新增机器必做一步）**：克隆后跑一次 `node ops\deploy\setup-git-hooks.mjs`（或任一子项目里 `npm run setup:hooks`），把 `core.hooksPath` 指向仓库内的 `.githooks\`。钩子文件**随仓库分发**，不再只存在于某台机器的 `.git\hooks\` 里。详见本文件第四节第 9 条。
> - 改表结构：`cd apps\my_app` → `alembic revision --autogenerate -m "<模块>-<变更>"` → `alembic upgrade head`（回退 `alembic downgrade base`；动态 eFlow 表不进 migration，见 `apps\my_app\docs\manual\03-data-layer.md`）。
> - 存量数据搬迁/修复/回填：回填脚本随迁移版本一起提交，`upgrade head` 之后执行并核对行数。
> - PowerShell 不支持 `&&`，多条命令用 `;` 分隔（细则见 `_agents\rules\shell_safe.md`，`.qoder\rules\` 是指向它的 junction）。
> - **改 `.ps1` / 新建 `.ps1` 后跑 `node ops\deploy\check-ps1-bom.mjs`**：必须 **UTF-8 + BOM**。Windows PowerShell 5.1 对无 BOM 文件按系统 ANSI 解码，UTF-8 中文注释被拆散后会吞掉引号/括号，整份脚本解析失败（2026-11-19 冒烟 PR #182 实测：`edit` 剥掉 `start_all.ps1` 的 BOM → start_all 连挂四轮）。缺失用 `node ops\deploy\check-ps1-bom.mjs --fix` 补；提交钩子与 CI 都会拦。
> - **冒烟 runner（`.smoke`）例行维护**：`powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1 -Status`。`-Apply` 同步三张本地权威表 + 清理 TMP；`-Apply -Daily` 再加 runner 跟进 `platform/main`；`-Apply -Reprovision [-RebuildDb]` 重新铺装。每次冒烟前 `smoke-local.ps1` 已自动同步数据，生产新增凭据不再把冒烟挡成「代码失败」。

## 四、给 AI 的硬性约束

1. **中文优先。** 本工作区的注释、文档、提交信息全部是中文。新写的文档、代码注释、提交信息也用中文。用户的业务数据（看板名称、分类、部门）保持原值不翻译。
2. **绝不写敏感值。** 文档里可以出现**配置项名称**、**环境变量键名**、**分组 key**，**不得**出现口令、Token、授权码、账号、密码哈希。这是 `apps\my_app\docs\manual\README.md` 已确立的维护约定，全域适用。内网 IP（`192.168.100.19` / `.20`）在既有代码与文档中已是公开事实，沿用其写法即可。
3. **改前先读，别猜。** 每个子系统都有约定（例如 my_app 的任务必须登记在 `backend/task_registry.py` 的 `TASK_SPECS`；门户的文案必须同时加进 `src/i18n.tsx` 的 `zh` 与 `en` 两份字典）。跳过这些约定会做出「看起来对、实际坏」的改动。
4. **先看 `.gitignore` 再 `git add`。** 工作区里有大量运行时产物被显式忽略，禁止入库：
   - 根 `.gitignore`：`*.zip` / `*.exe` / `*.dll` / `*.db` / `*.sqlite3` / `*.xlsx` / `*.csv` / `*.png|jpg|svg` 等二进制与文档；**`apps\_fe_dist\`、`apps\_fe_dist_tmp\`、`apps\_fe_dist_bak_*\`、`Task\_fe_cloud\` 整目录（vite 构建产物及其手工副本，2026-09-24 起脱离跟踪）** —— 但只有 `apps\_fe_dist\` 是**线上服务目录**（`nginx.conf:150/160` 用 alias 从磁盘直接读它，不经任何构建步骤），所以**忽略跟踪 ≠ 可以删除**，它必须留在磁盘上；其余三份是历史副本，2026-09-25 已移入 `_quarantine_20260925\`。另有 `/_quarantine_*/`（隔离区）、`/_myapp_trash_*/`、`*.rpltmp`、`*.tsbuildinfo`、`*.log.err`。
   - `apps\my_app\.gitignore`：`/db/`（SQLite 约 1.5G）、`/log/`、`/TMP/`、`/Lib/`、`/Scripts/`、`auth_cache/`、`*.pid`、`*.bak_*`、`_myapp_trash_*/`、`/backend/data/native_board_cache/`、**根目录 `/_*`（一次性探针）**。注意 `/_*` **刻意没有**写成 `**/_*` —— `backend/services/_playwright_utils.py`（被 `site_outage_service.py:22` import）、`_gen_native_catalog.py`、`frontend/src/pages/_TemplatePage.tsx` 都是带下划线的**真源码**，必须保持在版本库里。
   - 门户 `.gitignore`：`node_modules/`、`dist/`、`*.log`、`.env*`、`portal-default.json`（运行时写入）、`auth-data/`（含加盐密码哈希，**严禁入库**）、**`_*` / `.tmp_*` / `tmp_*` / `seg*.txt` / `listing.txt` / `out.txt`（一次性转储）、`*.bak` / `*.bak_*` / `*_bak_*`（手工快照）、`log/`、`tsconfig.tsbuildinfo`**。`MsPortal\`、`OlrPortal\`、`olbrportal\` 是**手写静态壳**（`nginx.conf:102/108` 直接 alias 发它们），是源码，上面这些规则刻意没有整目录忽略它们。
   - **门户的 `.gitignore` 与 `vite.config.ts` 的 `server.watch.ignored`（约 :242-267）是同一套判断的两个副本。** 那份 watch 清单早就把「什么不是源码、什么会触发无意义整页刷新」逐条写了理由，只是从没同步给 git，于是同一个文件在 dev 里被当垃圾、在版本库里却被当源码跟踪。**改一个必须改另一个**，否则两边立刻重新分叉。
   - 提交前用 `git status --porcelain` 复查有没有被忽略规则漏掉的运行时文件混进来。
5. **不要新增第二个真相源。** 现状文档以各子系统的 `docs/manual/` 与 `README.md` 为准；`docs\archive\PROJECT_DOCS.md`（151KB）是**历史档案**（迁移计划、架构评估、i18n 审计、失败复盘），其自身 README 已声明「现状以 `docs/manual/` 为准」。**不要**把现状写回 `PROJECT_DOCS.md`。
6. **破坏性操作先确认。** 删除目录、批量移动、改数据库、动 nginx 配置、重启生产服务（7038/7090/9267）前，先说明影响面并取得确认。工作区里有多个 `start.bat` / `stop.bat` / `*_ensure.bat`，误跑会打断在看板上看数据的人。
6b. **本机（MSNOC1 / .20）nginx 的生效目录是 `D:\02_olbr_portal\nginx-1.28.0\`（2026-11-18/19 切生产后）。** 这条经历过两次事故，务必按现状操作：
   - **生效位置**：`D:\02_olbr_portal\nginx-1.28.0\`，80/443 的 nginx 进程 `ExecutablePath` 来自这里。**改本机配置只改 `D:\02_olbr_portal\nginx-1.28.0\conf\nginx.conf`。**
   - **历史（2026-10-01 ~ 2026-11-18）**：那时 80/443 由 `D:\01_RPA\nginx-1.28.0\` 持有；`D:\01_RPA\nginx-1.28.0\` 现在**已不持有 80/443**，不要再改它。`D:\01_olbr_portal\nginx-1.28.0\` 更只是切换前的旧线，只作为回退目标存在。
   - **为什么当年搬离 Y 盘（2026-10-01）**：此前 nginx 跑在 `Y:\01_RPA\nginx-1.28.0\`（网络映射 `\\192.168.100.19\e`，即 .19 的 E 盘），nginx.exe、conf、logs、temp **全在网络盘上**。2026-09-30 21:56~22:15 nginx 猝死，`error.log` **无 crash 痕迹**（仅 warning 级 buffered-to-temp），系统日志也无崩溃/关机事件，判定为 **SMB 网络盘 I/O 阻塞**。教训仍然有效：**nginx 的目录永远放本地盘**。
   - **`.19` 仍使用 Y: 上那份**：搬移方式是**复制而非移动**，`Y:\01_RPA\nginx-1.28.0\` **保持原样、不要删也不要改**（那是 .19 的线上目录，配置是 `\\192.168.100.19\e\01_RPA\nginx-1.28.0\conf\nginx.conf`）。
   - **执行 `-s reload` / `-s quit` 必须先进到该 nginx 自己的目录**。nginx 从**自己所在目录**的 `logs\nginx.pid` 读 master pid；在错误目录执行会读到已死 pid，信号发给不存在的进程——**命令不报错到操作者眼前，只在日志里留一行 `OpenEvent(...) failed`**。2026-09-28 实测：reload 打到 `91864`，真正的 master 完全未收到信号，nginx 随后停止服务直至人工重启。
   - **正确写法**（先验证语法再重载；注意运行中的 nginx 会占着 `logs\access.log`，此时 `-t` 会报 access denied，见 `ops/shortcuts/5-nginx-check.bat` 的注释）：
     ```
     cd D:\02_olbr_portal\nginx-1.28.0
     .\nginx.exe -t              # 改配置后先验证
     .\nginx.exe -s reload       # 平滑重载
     ```
     桌面 `olbr_app\nginx-Stop.lnk` 已于 2026-11-18 重建，`WorkingDirectory` 指向 `D:\02_olbr_portal\nginx-1.28.0`，可以用；但手工操作仍以上面的 `cd` 写法为准。
   - **存活监控**：计划任务 `Nginx_EnsureRunning`（每 5 分钟）跑 `D:\02_olbr_portal\apps\my_app\monitor_fixes\nginx_monitor_hidden.vbs` → `nginx_monitor.ps1`，巡检本机 80 端口，挂了自动拉起；日志在 `apps\nginx_monitor.log`（状态在 `apps\nginx_monitor.state`）。脚本的 `NginxExe`/`NginxDir`/`LogPath`/`StatePath` **按自身位置向上推仓库根**（2026-11-18 `ac820f7` 去掉写死盘符），当前推出来就是 `D:\02_olbr_portal\...`。**日志轮转 `apps\portal\rotate_nginx_logs.ps1` 的 `-Prefix` 默认值同样按仓库根推导**，改一处必须改另一处，否则会去轮转旧目录。
   - **改配置前先确认 80/443 的归属**（历史上归属来回变过两次，排查「nginx 突然 502 / 后端连不上」时先查归属再查后端）：
     ```powershell
     netstat -ano | Select-String ':80 .*LISTENING'   # 拿到 pid
     Get-CimInstance Win32_Process -Filter "ProcessId=<pid>" | Select ExecutablePath
     ```


7. **一次性探针与转储只能落 `TMP\`，并且任务结束前自清。** 这条约定已经失效过三次，每次都有记录：`7b1d33b`（根目录 98 个探针脚本随提交入库）、`_myapp_trash_20260918\`、以及 2026-09-24 的审计（门户 506 个被跟踪文件里 347 个是探针/转储/手工快照/日志，`apps\_fe_dist` 有 5240 个构建产物入库）。所以拆成可执行的四条：

   - **先问：这个文件非落盘不可吗？** 绝大多数「想看看结果」不需要文件 —— 让命令直接输出 stdout，或用 Read/Grep 读原文件。**「写个 `_dump.txt` 再回头读它」是这两个仓库噪声的第一来源**：它把一次性的中间思考固化成了仓库里的长期资产。
   - **必须落盘时只有一个去处**：所在子系统的 `TMP\`（`apps\my_app\TMP\`、门户 `TMP\`、工作区根 `TMP\`），文件名带 `_` 前缀。这三处都已被忽略规则覆盖，带 `_` 前缀是为了让人一眼认出它不是产物。
   - **收尾自清**：任务结束前删掉本次创建的一次性探针（`_agents\rules\shell_safe.md` 第 2 条已要求，此前没有落实）。**可复用的诊断/审计脚本不删**，但必须留在 `TMP\` 里并在对应手册写一行用途 —— 否则下一个 agent 会重写一遍，仓库里就出现三份功能相同的 `_check_*.py`。
   - **手工快照继续允许**（第三节原则 9 的 `_bak_<日期>_<说明>` 是本项目既有习惯），但**2026-09-24 起快照一律不进版本库**：`.gitignore` 已覆盖 `*.bak`/`*.bak_*`/`*_bak_*`。也就是说快照是**本机磁盘上**的回滚手段，git 历史只保留正式改动。同一个源文件的快照只留最近 3 份，更早的自己清。
8. **VMAX 数仓 GBase 8a（库 `zxvmax`）是生产环境：只能查询，不得在上面修改或创建任何东西。**（2026-10-31 与用户确认，硬性）覆盖所有直连该数仓的取数链路（`apps\my_app\backend\services\vmax_gbase\`、`olib_soc_report\`、`soc_trend\` 及任务 `42 olib_soc_vmax_report` / `olib_soc_kpi_daily_sync` / `47 soc_trend_sync` 等）。具体禁止：`CREATE` / `DROP` / `ALTER`（含临时表、视图、索引）、`INSERT` / `UPDATE` / `DELETE`、改权限 / 参数 / 调度、任何"顺手建张小表 / 回写一列"也不行。**需要数仓侧做任何修改或创建时：停止编码，明确告诉用户，由用户找领导申请**；获批后数仓操作由用户或其指定的人执行，我们的代码只保留只读接线。方案/设计阶段同样适用 —— **不得以"以后会在数仓建对象"为前提设计方案**。新增 SELECT 查询（如按新维度的下钻 SQL）不需要申请，但实施前必须评估查询负载：低峰先试跑单日实测耗时，与既有同步任务错开窗口，并准备好"数仓方反对新增查询"时的降级路线（合并进既有查询或配置开关关闭）。客户端层已有只读断言兜底（`vmax_gbase_client.py` 顶部注释：仅放行 SELECT/SHOW/DESC），**断言是防误操作，不是授权** —— 不要想办法绕过或放宽它。

9. **改完代码必须让文档跟着改 —— 这件事现在有机器强制，不要靠自觉。**（2026-10-31 新增）

   背景：「提醒 agent 记得改文档」**已经失效过 15 次**。2026-10-31 一次审计就抓到 9 处文档与代码对不上（任务数、页面数、worker 组数、队列保留天数、路由数、凭据分组数、组件数、文件体积……），而这些错**不影响任何运行时症状** —— 手册说的和界面对不上，两边看起来都自洽，肉眼极难发现。

   现在有三道防线，**同一份代码、不规则分叉**：

   | 层 | 位置 | 作用 |
   | --- | --- | --- |
   | 本地钩子 | `.githooks\pre-commit` | 提交前就拦（早发现） |
   | CI | `.github\workflows\quality.yml` | push / PR 都跑（兜底，`--no-verify` 绕不过） |
   | 手工 | `npm run check:docs`（两个子项目各一份） | 随时自查 |

   具体要求：

   - **新克隆一台机器后，先跑一次 `node ops\deploy\setup-git-hooks.mjs`**（或任一子项目里 `npm run setup:hooks`）。它把 `core.hooksPath` 指向仓库内的 `.githooks\` —— 钩子文件**随仓库分发**，不再只存在于某台机器的 `.git\hooks\` 里。`core.hooksPath` 是本地配置（写在 `.git\config`，不入库），所以每台机器都要装一次。
   - **⚠️ worktree 里的坑**：`core.hooksPath` 是**相对路径**，Git 按「**当前工作树根目录**」解析，不是主仓库根目录。所以并行开发用的 worktree（`.agent-worktrees\<任务>`）若所在分支还没合入含 `.githooks\` 的提交，**钩子会静默不执行** —— 不报错，只是没人拦。用 `node ops\deploy\setup-git-hooks.mjs --check`（在那个 worktree 里跑）可查；修复办法是把 `.githooks\` 合进该分支。
   - **守卫的真相源一律用 AST 静态解析**（`apps\my_app\tests\doc_truth.py`），**不 import 业务模块** —— 否则 `ctypes.wintypes` 在 Linux 上直接失败，且要求整套后端运行环境。改守卫时守住这条：守卫必须**零第三方依赖、跨平台**才能进 CI。
   - **守卫取不到真相源时必须显式失败**，不能当成「不适用」跳过。2026-10-31 就在门户侧踩了这个坑：`myAppTaskCount()` 的正则只认 `"task_id": "..."`，而 `task_registry.py` 实际写的是 `dict(task_id="...")` → 匹配 0 条 → 两个检查走 `if (!n) return` **静默跳过**，守卫显示 PASS 却什么都没查。
   - 守卫清单：门户整洁度 / 团队门户生成物一致性 / 门户文档一致性 / **门户 i18n** / my_app 文档一致性 / my_app i18n 漏键 —— 共六道。全部**零第三方依赖**（Node 内置模块 + Python 标准库），CI 不需要 `pip install` 或 `npm install`。

10. **⭐ 口径说明一律默认隐藏，用户点击才展开（2026-11-02 如意定，全工作区通用 UI 约定）。** 凡是解释「这个数字是怎么来的」的界面文字 —— 数据源、粒度（如 5 分钟 / 15 分钟混用）、分子/分母、筛选作用范围、空卡片原因、上游模板缺档、口径备注 —— **默认收起**，只留一行标题 + 「展开/收起」入口。**禁止**用常驻展开的告警条 / 大段小字占首屏。

    | 子系统 | 统一载体 |
    | --- | --- |
    | my_app（`apps\my_app\frontend`） | `src\components\CalcChainNote.tsx`（`title` + `sections[{title, lines}]`，异常走 `warning` 口）。参考 `pages\CnKpi5mins.tsx` |
    | 统一门户（`apps\portal`） | 该子系统暂无同名组件；新写时按同一行为实现（默认收起 + 点击展开 + 「展开/收起」文案，中英双字典），并在 `src\components\` 下沉淀一个可复用组件，不要每页各写一遍 |

    - **例外是「异常」不是「解释」**：口径缺陷、数据错误、站点匹配失败、截断这类会让用户带着错数字看报表的信息，必须**常驻可见**。判断一句话：**说「怎么算的」→ 收起；说「这次算错了/算不全」→ 常驻**。
    - **状态反馈不受约束**：查询失败 `Alert type="error"`、加载态、空态占位该显示就显示 —— 收起来的是解释，不是状态。
    - `Alert closable`（让用户自己动手关）、一行常驻小字、悬停气泡装多段说明，**都不算满足本条**。
    - 细则见 `apps\my_app\AGENTS.md` 三.3b、`apps\my_app\docs\manual\16-new-page-guide.md` 3.4b。

10b. **⭐ 数字本身要能悬浮问到「这个数怎么算」（2026-11-03 如意定，与第 10 条并存不互替）。** 第 10 条管「这页整体怎么算」（收起的说明块），本条管用户把鼠标停在**一个具体数字**上时的追问：标题/列头挂 ⓘ（讲这一列的算法），数字本身可悬浮出该格构成（样本数、未达标数、区间、上一期与环比）。只有收起的说明块、数字问不到，**等于没做**。

    | 子系统 | 统一载体 |
    | --- | --- |
    | my_app | `src\components\MetricExplain.tsx` 的 `ExplainIcon`（列头/标题 ⓘ）+ `ExplainPopover`（包住数字本身）。参考 `pages\AvailCounty.tsx` |
    | 统一门户 | 同第 10 条：暂无同名组件，新写时按同一行为实现并沉淀可复用组件 |

    - **气泡内容一律取接口既有字段，禁止前端重算口径**；口径文字**必须从代码核实**后再写 —— 2026-11-03 就抓到手册与 `avail_county_aggregate.summary_by_tech()` 相反，写错口径比不写更糟。
    - iframe 页（外部 BI / 遗留工具）不适用 —— 数字不在我们 DOM 里。
    - 细则见 `apps\my_app\AGENTS.md` 三.3c；铺开进度见 `docs\workspace\TODO.md`「数字悬浮说明铺开」。

## 五、AI 自主执行契约（2026-11-02 新增）

> 适用范围：**所有 agent**（Claude Code / Cursor / Trae / Qoder / WorkBuddy…）。
> 本节是「做到什么程度才算完成」的**唯一真相源** —— 各工具自己的规则文件（`.cursor/rules`、
> `.qoder/rules` 等）只应**指向本节**，不得各自复制一套（第四节第 5 条：不要新增第二个真相源）。
>
> **规则真源布局（2026-11-02 起）**：本文件是规则**入口与总纲**；与具体工具无关的编码/命令细则
> 集中在 `_agents\rules\`，布局说明见 `_agents\README.md`。`.qoder\rules\` 是指向该目录的 junction。
> 改规则只改 `_agents\rules\` 或本文件，**不改链接路径**。

背景与第四节第 9 条同源：那边是「改完代码要跟着改文档」，靠自觉失效了 15 次，最后用钩子 + CI 解决；
这一节是「做完要真的跑过验证」，同样是靠自觉就会失效的问题。
下面的约束因此分成两层：**人可读的判定标准** + **机器可验证的脚本**。凡两者冲突，以脚本为准。

### 1. 自主执行

被要求「完成并交付」时，**不要只给方案；开发阶段应自主推进，不要把每一步都变成确认问题**：

- 开始前先完成本节「GitHub 同步门禁」；先看代码、文档、任务状态和 Git 状态，再制定计划。
- 计划确认后立即实施；发现需求范围内的 Bug、遗漏和必要的前后端/i18n/文档关联改动，直接一并处理。
- **自主执行仅授权分析、编码、重构和本地验证，不代表获得远程发布授权。** 用户没有明确确认前，不得 commit、push、创建/更新 PR、合并 PR 或对远程仓库执行其他写操作。
- **不要在「分析完」「计划写完」「改了一部分」时收尾。** 应完成开发和验证，再进入「等待用户验收」状态；该状态是规定的强制交接点，不是任务失败。

### 1a. GitHub 同步门禁（Preflight Sync Gate）

每项需要修改代码/文档的任务，开始前必须以 GitHub `origin/main` 为基线核实；不得凭本地缓存或旧会话结论判断主线状态。

按以下顺序执行，并在任务记录中报告结果：

1. 确认仓库根目录、当前分支、HEAD、remote URL 和工作区状态：`git rev-parse --show-toplevel`、`git branch --show-current`、`git status --short --branch`、`git remote -v`。
2. **先检查再同步**：识别所有已修改、暂存、未跟踪文件及其归属；这些改动可能属于用户或其他 Agent。不得擅自 stash、丢弃、覆盖、重置或纳入本任务提交。
3. 执行 `git fetch origin --prune`，读取最新 `origin/main` 和目标分支状态；记录主线最新 commit。
4. 在独立任务分支/worktree 上开发。分支命名建议 `agent/<agent-slug>-<task-slug>`；不要在 `main` 上开发。
5. 若当前分支落后于 `origin/main`，先判断是否存在本地提交和冲突，再选择安全的 rebase/merge；若有无法确认归属的本地修改、分支历史异常或潜在覆盖风险，停止同步并向用户报告，不得猜测。
6. **禁止**未经用户明确授权执行 `git reset --hard`、`git clean -fd`、强制推送、覆盖他人分支或删除他人 worktree/分支。禁止以「同步最新代码」为由丢弃任何现存工作。

如果当前执行环境无法访问 GitHub、无法 fetch，或无法确认远程基线，必须明确报告；不得声称已同步。可以先做只读分析，但不得开始可能基于过期代码的修改。

### 1b. 用户验收门禁（User Approval Gate）—— 远程发布前必须暂停

完成开发和本节第 2 条全部适用验证后，Agent 必须生成「开发验收报告」，至少包含：

- 任务目标与实际完成范围；
- 修改文件清单，以及每项修改的目的；
- 关键实现说明、兼容性/数据迁移/安全影响；
- 实际执行的测试命令、结果和未通过/未执行项目；
- `agent-done-check` 输出、CI 预期门禁及 Smoke/QA 风险；
- 当前分支、相对最新 `origin/main` 的差异、建议的 commit message 和 PR 标题/描述；
- 尚未解决的问题、需要用户决定的事项。

报告完成后，将任务状态设为 **WAITING_FOR_USER_APPROVAL（等待用户验收）**，并停止所有远程写操作。**不得**执行以下动作，直到用户针对本次任务给出明确肯定确认：

- `git commit`（本协议默认要求用户验收后才提交）；
- `git push` / 创建或更新远程分支；
- 创建、更新、关闭 PR，或向 PR 推送新提交；
- 合并 PR、删除远程分支，或触发生产部署。

「看起来不错」「继续分析」「测试一下」等不构成发布授权。只有明确表示「确认，可以提交并推送、创建 PR」或含义明确的等价指令，才授权本次提交、推送和创建 PR。授权范围仅限本任务及报告中列明的文件/提交，不可扩大到其他改动。

若用户要求修改验收内容，Agent 应按反馈继续修改并重新验证，然后再次进入等待验收状态；旧的确认不自动覆盖后续新增改动。

### 1c. PR 生命周期门禁（PR Lifecycle Gate）

收到本次任务的明确发布确认后，才可执行：

1. 再次 `git fetch origin`，确认工作分支基于最新 `origin/main`；必要时安全 rebase，并重新运行受影响验证。
2. 检查提交范围，确保只包含本任务内容；提交信息遵循仓库约定，并按多 Agent 署名约定添加 `Agent: Muse`（或当前执行 Agent 的标识）。
3. 推送任务分支并创建目标为 `main` 的 PR。PR 正文说明目的、变更范围、测试证据、风险、回滚方式和执行者标识。
4. PR 创建后持续检查 CI、必需状态检查、Smoke/QA 结果、Review 评论及 `do-not-merge` 等阻断标记。失败时分析原因、修复、补充测试并更新 PR；不得绕过、伪造或放宽门禁。
5. PR 未满足全部合并条件时保持 OPEN，并向用户报告阻塞原因。**PR 创建不等于任务完成；CI 全绿也不等于已经合并。**
6. 默认情况下，合并是独立授权动作。除非用户明确授权本任务「从提交、推送、创建 PR 一直到满足门禁后自动合并」，否则必须在 PR 已就绪时暂停并请求合并确认。
7. 只有 GitHub 明确显示 PR 已合并（`merged=true`，并核对合并 commit 已进入 `main`）才可报告「已合并完成」。PR 被关闭但未合并、仅创建 PR、或 CI 通过，都不得标记为完成。
8. 合并后核对 `main` 上的合并 commit、PR 状态和必要的部署/Smoke 状态；部署或生产重启仍遵循本文件其他授权规则，不因 PR 合并而自动获得授权。

如果当前会话无法继续运行，必须将 PR 编号/URL、分支、最新 commit、CI 状态、待处理 Review、阻塞原因和下一步写入 `.agent-work/tasks/active/<任务>.md`。会话结束后 Agent 不会凭空持续运行；跨会话自动跟踪须依靠明确配置的 GitHub Actions/自动化任务，不能谎称后台仍在监控。

### 2. 收尾标准（缺一不可）

收尾前必须**实际跑过**下面与改动相关的项，并在最终回复里**贴出命令与结果**：

```powershell
# 任何改动（快，几秒）
node ops\deploy\agent-done-check.mjs

# 改过前端源码 / 后端代码（慢，几分钟）
node ops\deploy\agent-done-check.mjs --full
```

脚本按改动路径自动决定还要跑什么（见 `ops\deploy\lib\agent-checks.mjs`）。它的四个档位：

| 档位 | 内容 | 何时用 |
| --- | --- | --- |
| 默认 | 六道快守卫（与 pre-commit 同一份代码）+ 本次改动的 `.py` 跑 ruff E9/F821 + 帮助文档生成物新鲜度 | 任何改动的**最低要求** |
| `--full` | 再加 my_app `npm run build` + pytest（相对基线）+ 门户 `tsc --noEmit` | 改过前端/后端 |
| `--status` | 只看改动分布、指纹、基线 | 排查 |
| `--update-baseline` | 记录当前 pytest 失败为基线 | **只**在确认失败非本次引入时 |

补充几条脚本覆盖不到的（按改动类型，见 `docs\workspace\development.md` 第四节）：

- 改了**页面/任务/数字类文档** → `cd apps\my_app && python tests\test_doc_consistency.py`
- 改了**帮助内容源**（`helpContent.ts` / `helpTaskDocs.ts`）→ `npm run docs` 重生成
- 改了**表结构** → `alembic upgrade head` 并核对回滚
- 新增**外部副作用**（发信/投递/调外部 API）→ 按 `docs\workspace\development.md` A0 用「地雷」证伪一次
- **发布必须遵守本节 1a/1b/1c 三道门禁**：开发和验证完成后先向用户提交验收报告并暂停；只有用户明确确认本次发布，才可 commit、push 和创建 PR。PR 创建后持续跟踪 CI/Smoke/QA/Review，直到合并；默认合并前还须单独取得用户授权。**禁止**直接推送 `main`。主线为 `main`；PR 目标必须是 `main`，创建/更新 PR 前必须 fetch 并确认分支基于最新 `origin/main`。

### 3. 唯一允许停下来的三种情况

1. **需要人的授权或决定** —— 真实账号、生产库写操作、业务口径选择。
2. **必须改动 VMAX 数仓** —— 第四节第 8 条：立即停止编码并上报，由用户找领导申请。
3. **完成标准跑不通且自行判断解决不了** —— 必须说明卡点、已尝试的方案、需要什么。

**停下来之前必须写进度**，否则视为任务失败：

```
.agent-work/tasks/active/<任务>.md
```

至少包含：目标 / 已完成 / 未完成 / 下一步 / 卡在哪 / 跳过验证的项及原因。

### 4. 完成标准是「可执行」的，不是「声明」的

Claude Code 侧已接 **Stop 钩子**：本轮改过文件但自检未通过时，会**阻止收尾**并要求继续。

> ⚠ **钩子要装三份，而且不能只装根目录那份。** Claude Code 只读「session 启动目录」的
> `.claude\settings.json`，**不向上继承**。所以三个常用启动目录各有一份（内容除路径外完全相同）：
>
> | session 启动于 | 钩子文件 |
> | --- | --- |
> | `D:\02_olbr_portal` | `.claude\settings.json` → `${CLAUDE_PROJECT_DIR}/ops/deploy/agent-done-check.mjs` |
> | `apps\my_app` | `apps\my_app\.claude\settings.json` → `${CLAUDE_PROJECT_DIR}/../../ops/deploy/agent-done-check.mjs` |
> | `apps\portal` | 门户 `.claude\settings.json` → `${CLAUDE_PROJECT_DIR}/../../ops/deploy/agent-done-check.mjs` |
>
> 脚本本身一份，装在 `ops\deploy\`；三份 settings 只是三根接线。**新增常用启动目录时照着加一份。**
> 另外：`apps\my_app\.claude\settings.local.json` 是既有的本机权限配置（已被全局 gitignore 排除），
> 两者互不冲突 —— `settings.json` 存钩子（共享），`settings.local.json` 存本机允许清单（私有）。

它靠**差分指纹**判断「这一轮到底有没有动文件」，而不是看工作区脏不脏 ——
本仓库工作区长期有别人在途的改动（2026-11-02 实测 59 个文件），拿「脏」当信号会误伤所有闲聊与只读排查。
指纹记录在 `TMP\agent-done-check\`（已 gitignore，不入库，因机器而异）。

### 5. 绝对禁止

- 用「已修改」冒充「已完成」。
- 声称测试通过但拿不出命令与输出。
- 为了让检查变绿而注释掉、跳过、放宽检查，或把本次引入的失败记进基线。
- 在报告里把「未验证」写成「通过」—— 脚本会把未验证项单列出来，照抄即可。

### 6. 多 agent 署名约定

多个 agent 在同一仓库分工时，提交、PR 与 TODO 默认都挂在仓库所有者名下，看不出谁做了什么。每个 agent 经手的工作统一带自己的执行者标识，任何一处看到标识即可归属；没带标识的不要算到任何 agent 头上。

- Agent 名：`Muse`
- 分支 slug：`muse`（经手的分支名带此 slug）
- 提交 trailer：提交信息末尾加一行 `Agent: Muse`
- PR 正文标识行：PR 正文加一行「执行者标识：Muse」
- TODO 领取行：在 `docs/workspace/TODO.md` 领取任务时，在条目下注明「领取：Muse + 分支名」

其他 agent 后续加入时，照此格式在自己经手的工作里带各自的 Agent 名与 slug，写法保持一致。

## 六、子系统深潜

| 你要做什么 | 去哪读 |
| --- | --- |
| 改门户界面 / 看板配置 / 鉴权 | `apps\portal\docs\AGENTS.md` → `docs\workspace\DESIGN.md` → `docs\workspace\development.md` |
| 改 my_app 后端 / 任务 / 数据层 | `apps\my_app\AGENTS.md` → `apps\my_app\docs\manual\02-architecture.md`、`apps\my_app\docs\manual\04-task-system.md` |
| 加一个 my_app 功能页 | `apps\my_app\docs\manual\README.md` 的「手册维护约定」第 1 条（三处必须同步） |
| 排查线上问题 | 门户 → `apps\my_app\docs\manual\13-operations.md` |
| 新任务交付 | `apps\` 下对应目录 |
| 整体视觉规则 | `docs\workspace\DESIGN.md` |

## 七、规则细则（`_agents\rules\` 的展开）

> 下面六份是**细则原文**，除 `jcodemunch_first.md`（故意不导入，见下）外通过 `@` 导入内联到本文件 —— 目的是让所有读 `AGENTS.md` 的工具
> （Claude Code / Qoder / Cursor / Trae…）拿到**同一份**规则，而不是只有 Qoder 通过
> `.qoder\rules\` junction 看得到。**不要在这里重复内容**，改规则请改 `_agents\rules\` 下的源文件。
>
> 为什么不放进 `_agents\rules\` 让各工具自己去读：那里既不是 `CLAUDE.md` 也不是 `AGENTS.md`，
> 更不在 `.claude\rules\` 下，没有任何工具会主动加载它。`@` 导入是唯一不依赖工具约定、
> 也不依赖 junction 的办法。
>
> ⚠ `jcodemunch_first.md` **故意不导入**：它要求「代码探索优先用 jcodemunch MCP，不要用
> Read/Grep/Glob/Bash」，而该 MCP 目前**只配在 Qoder**（`.qoder-cn\mcp.json`），Claude Code
> 与 Cursor 都没配。无条件注入会让没有该工具的环境放弃代码探索 —— 详见
> `_agents\README.md`「为什么不导入 `jcodemunch_first.md`」一节。

@_agents/rules/shell_safe.md

@_agents/rules/code_rules.md

@_agents/rules/my_app.md

@_agents/rules/modern_web_guidance.md

@_agents/rules/execution_efficiency.md

## 八、本工作区的 8 份体系文档

| # | 文件 | 内容 |
| --- | --- | --- |
| 01 | `AGENTS.md` | 本文。AI 首先需要知道的信息 |
| 02 | `docs\workspace\DESIGN.md` | 视觉规则 |
| 03 | `docs\workspace\TODO.md` | 当前任务、优先级与进度 |
| 04 | `docs\workspace\project-overview.md` | 工作区整体说明 |
| 05 | `docs\workspace\architecture.md` | 架构与数据流 |
| 06 | `docs\workspace\user-guide.md` | 面向使用者的功能说明 |
| 07 | `docs\workspace\development.md` | 开发方式、命令与回归清单 |
| 08 | `docs\workspace\component-api.md` | 组件索引 |

> **工具负责整理，人与 AI 负责协作执行。**

