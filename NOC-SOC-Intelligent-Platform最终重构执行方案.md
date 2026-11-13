# NOC/SOC Intelligent Platform

## 第一轮重构执行方案

> 版本：按 2026-11-13 仓库实况修订
>
> 工作区：`D:\02_olbr_portal`
>
> 新仓库：<https://github.com/Jouwala/noc-soc-intelligent-platform.git>
>
> 产品名：NOC/SOC Intelligent Platform（NOC/SOC 智能运维平台）
>
> 状态：方案。目录还没搬。不改 `D:\01_olbr_portal`，不改 `D:\01_RPA`。

---

## 1. 这一轮做什么

把 02 从「便携副本的旧目录」收成边界清楚、可独立维护的平台仓库，并推到新的 GitHub 仓库。

这一轮只做四件事：

1. 目录归位：`apps`、`docs`、`ops`。
2. 把写死的旧路径改到新位置，保证现有功能还能启动。
3. 加上离线检查和隔离冒烟。
4. 换成新仓库身份后再推送。

这一轮不改 Python 包路径，不拆 `backend/services`，不拆 `routers`，不把 `email_reports` 并进 `backend`。

原则：一批一个提交；这一批验收不过，不进下一批；失败只回滚这一批。

---

## 2. 三个目录

| 环境 | 路径 | 这一轮 |
|---|---|---|
| 生产 | `D:\01_olbr_portal` | 不改。它正在听 80/443 |
| 编辑区 | `D:\01_RPA` | 不改，也不再把 02 同步回这里 |
| 重构工作区 | `D:\02_olbr_portal` | 只改这里 |

硬性规则：

- 不在 01、不在 RPA 里执行重构命令。
- 不从 02 自动同步回 01 或 RPA。
- 不提交 `.env`、数据库、日志、session、构建产物、凭据。
- 不改 01 或 RPA 上已经注册的 Windows 计划任务。02 的验收只检查 02 自己的脚本和快捷方式。

---

## 3. 已经核实、方案必须遵守的事实

这些不是目标，是当前代码就是这样写的。

### 3.1 兄弟目录不能拆开

`my_app` 和 `whatsapp-kb-bot` 必须处于同一父目录，bot 的叶子名不能改。

| 代码 | 实际找法 |
|---|---|
| `portal_qa_ctl.py` | `my_app` 的父目录 / `whatsapp-kb-bot` |
| `tt_alarm_render.py` | 同上，再进 `scripts\tt-analyze.py` |
| `wa_notify_service.py` | 文件向上 3 级，再接 `whatsapp-kb-bot` |
| `myAppPaths.js` | `src` 向上 3 级是仓库根；bot 的父目录 / `my_app` |

从 `Task\` 一起上移到 `apps\` 后，层数不变，这几处不用改逻辑。

### 3.2 `_fe_dist` 跟着 my_app 的上一级走

`vite.config.ts` 的 `outDir` 是 `../../_fe_dist`。后端 `main.py` 是「向上 3 级 / `_fe_dist`」。现在都指到 `Task\_fe_dist`。上移一层后都指到 `apps\_fe_dist`，仍然对齐。

要改的是写死字符串的地方：`nginx.conf.template`、`start_all.ps1`、根 `.gitignore`。构建产物不入库。02 里如果还没有这个目录，不要为了搬家去创建空目录。

### 3.3 `eflow_Q&A` 不是纯文档

`backend\routers\eflow_qa.py` 的默认目录是：本文件向上 3 级 / `eflow_Q&A`。`main.py` 启动时会预热其中的 `tools\inspect_tt.py`。

`my_app` 进 `apps` 之后，向上 3 级就是 `apps`。所以知识库必须是：

```text
apps\eflow_Q&A
```

叶子名保持 `eflow_Q&A`。不改名为 `eflow-qa`，不放进 `docs`。这样默认路径不用改。`start_all.ps1` 里的 `EFLOW_QA_DIR` 改成这个新路径，给经过启动脚本的进程用。

### 3.4 平台数据库模式已经是 PostgreSQL

`MY_APP_DB_MODE=pg`。健康检查返回 `db_mode=pg`。SQLite 是熔断回退，不是当前主库。

登记表里把这句话写成**已核实的现状**。还没逐个盘清的是每一张表、每一个文件库谁在写，那些标「待确认」，不要把整句平台结论倒回成「还不能写」。

已知且应照实写上的：

- 三张本地权威表只在 SQLite：`credentials`、`task_schedule_config`、`email_recipients`。同步和「从 PG 重建 app.db」都不会带上它们。
- `eflow_tt` 有双库；同步写 `app.db`。
- 告警表双写，页面读 `app.db`。
- `db_ps_cs_cn` 的真数据在远端，仓库里的同名文件不是真源。
- VMAX GBase 只允许查询，代码不得绕过只读断言。

### 3.5 根上的 `sync-now.bat` 会把新目录盖回去

它调用 `D:\01_RPA\scripts\deploy_to_copy.ps1`，按旧目录把 RPA 源码同步进当前仓库。这一轮必须把它改成直接退出并说明原因，不能留在根上当入口。

### 3.6 本机没有 gitleaks

开工前不要把 `gitleaks detect` 写成必过命令。先用仓库里已有的、不依赖额外安装的扫描。本机装上 gitleaks 之后，再补扫完整历史。

### 3.7 基线已经建过

下面两个已经在 02 里，不要再创建一次：

- tag：`refactor-baseline-2026-11-13`
- 分支：`refactor/round-1`

开工时只核对它们指向的 commit。重复 `git tag` / `git branch` 会失败。

### 3.8 门户有三份未提交的内容改动

相对 HEAD 已有内容差异，不是换行噪声：

- `liberia-noc_soc-数据看板统一门户\src\App.tsx`
- `liberia-noc_soc-数据看板统一门户\src\components\DynamicIcon.tsx`
- `liberia-noc_soc-数据看板统一门户\vite.config.ts`

搬迁提交不得包含这三份文件的内容变化。批 B 之前把它们单独留出（stash，或先另交一个提交）。搬完再放回来。hash 用来确认搬家本身没有改内容。

### 3.9 测试还没有 `live` 标记

`Task\my_app\pyproject.toml` 里没有 pytest marker。现在执行 `pytest -m "not live"` 等于把所有测试都跑一遍，其中有会访问外部系统的。

在标记真正加上之前，目录批次的验收不使用这句命令。

### 3.10 这一轮不动 my_app 内部这些目录

上次讨论过可以收进 `extensions\`、`docs\archive\`、`ops\production-launcher\`。对照代码后，这一轮不动：

- `whatsapp_sender_extension`、`portal_login_extension`：服务注释、任务说明、扩展 README 都写着 `Task\my_app\whatsapp_sender_extension`。Chrome 也是按这个目录加载。
- `PROJECT_DOCS.md`：手册和多处代码注释用相对路径指着它。
- `start_hidden.ps1`、`start.bat`、`stop.bat` 等：这是生产看门狗入口。02 的隔离启动不走它们，但本轮不搬家，只在 `apps\my_app\README.md` 写明「02 禁止运行这些脚本」。

内部整理放到第二轮，并且每次只动一组、同时改完引用。

---

## 4. 目标目录

只画这一轮真会落地的树。

```text
D:\02_olbr_portal\
├── AGENTS.md
├── README.md
├── start.bat                      转调 ops\deploy
├── stop.bat
│
├── apps\
│   ├── my_app\                    内部目录保持原样
│   ├── portal\                    原中文长目录
│   ├── whatsapp-kb-bot\           叶子名不改
│   ├── eflow_Q&A\                 叶子名不改，含 tools\inspect_tt.py
│   └── _fe_dist\                  构建产物，不入库；没有就不要造
│
├── docs\
│   ├── architecture\
│   ├── adr\
│   ├── operations\
│   └── workspace\                 原根上的长文档
│
├── ops\
│   ├── deploy\                    原 scripts\
│   ├── shortcuts\                 原 my_portal\
│   └── quality\
│
├── runtime\                       不动，不入库
├── nginx-1.28.0\                  不动，不入库
└── .github\workflows\
```

不在这一轮出现的目录：

- `config\`、`env.copy.example`。没有任何代码读 `.env.copy`。配置仍是仓库根 `.env`，模板随脚本进 `ops\deploy\env_template.txt`。
- `ops\sync\`、`ops\healthchecks\`。同步脚本跟着 `scripts\` 进 `ops\deploy`，并标成停用。
- `docs\knowledge\eflow-qa`。见第 3.3 节。
- `integrations\`。第二轮若要做，也放在 Python 包里面，不在仓库根另起一套靠路径拼接的目录。

根上不再保留 `sync-now.bat` 这个可执行入口。

---

## 5. 路径对照

| 旧 | 新 | 做法 |
|---|---|---|
| `Task\my_app` | `apps\my_app` | 整棵移动，不改包名 |
| `Task\whatsapp-kb-bot` | `apps\whatsapp-kb-bot` | 整棵移动，叶子名不改 |
| `Task\eflow_Q&A` | `apps\eflow_Q&A` | 整棵移动，叶子名不改 |
| `liberia-noc_soc-数据看板统一门户` | `apps\portal` | 整棵移动并改名 |
| `Task\_fe_dist` | `apps\_fe_dist` | 仅当目录已存在时移动；不入库 |
| `scripts` | `ops\deploy` | 整棵移动 |
| `my_portal` | `ops\shortcuts` | 整棵移动 |
| 根上长文档 | `docs\workspace` | 见批 E 的清单 |
| `runtime`、`nginx-1.28.0` | 原地 | 不搬，不入库 |
| `AGENTS.md`、`README.md` | 根目录 | 改内容，不移动 |
| `sync-now.bat` | 删除入口 | 改成拒绝执行后移除 |

搬完后删除空的 `Task\`。

---

## 6. 开工前检查

全部核对完再开始批 0。

### 6.1 确认是 02，且基线已在

```powershell
cd D:\02_olbr_portal
git status --short
git branch --show-current
git rev-parse refactor-baseline-2026-11-13
git remote -v
```

确认：

- 当前目录是 02。
- 当前分支是 `refactor/round-1`。
- tag 存在。不存在才创建，已存在不重复打。
- 现有 origin 仍是旧仓库 `Jouwala/MS-Intelligent-O-M-Platform`。这一步不要改 remote。
- 未提交改动已登记。三份门户文件按第 3.8 节处理。

### 6.2 旧路径扫描

```powershell
rg -n --hidden --glob '!.git/**' --glob '!runtime/**' --glob '!nginx-1.28.0/**' --glob '!**/node_modules/**' "Task[\\/]|liberia-noc_soc|_fe_dist|my_portal|eflow_Q&A|scripts[\\/]" .
```

结果放在 `TMP\pre-refactor-path-references.txt`。`TMP\` 已忽略，不要提交。

扫描要覆盖 Python、TypeScript、JavaScript、PowerShell、BAT、VBS、nginx 模板、Markdown 链接、`.env` 模板、Git hooks。

这是清单，不是要求把文档里的历史路径一次改光。代码、启动脚本、nginx、钩子、守卫里的路径必须改。文档里描述旧事故的句子可以保留，但现行操作步骤必须改成新路径。

### 6.3 敏感信息

先做不依赖 gitleaks 的检查：

- `git ls-files` 里不得有 `.env`、`*.pem` 私钥、`auth_info`、session、cookie 导出、数据库文件。
- 工作区里的 `.env`、`runtime\`、`nginx-1.28.0\` 必须被忽略。

装上官方 gitleaks 之后再跑：

```powershell
gitleaks detect --source . --log-opts="--all" --redact
```

有真实凭据：先轮换，再清历史。不要把秘密值写进报告或聊天。只有命中才轮换，不要按文件名猜测去轮换生产口令。

### 6.4 计划任务只登记

列出动作路径落在 02 里的计划任务和桌面快捷方式。指向 01 或 RPA 的，只记录，不修改。

---

## 7. 批次

### 批 0：修语法，不搬家

文件：`Task\my_app\email_reports\send_via_browser.py`

- 约 153 行：f-string 里嵌了 JavaScript 花括号。改成普通三引号字符串，正文用 `json.dumps` 注入。
- 约 280 行：重复的 `else`。删掉不可达的那个，行为不变。

```powershell
python -m compileall -q Task scripts
cd Task\my_app
python -m ruff check backend --select E9,F821
```

`compileall` 若再报语法错误，在这一批修完。不修业务逻辑。

同时写最小的 `ops\quality\check_all.ps1`（目录可以先建，脚本先只做这四项）：

1. 当前目录是 02，且存在 `ISOLATED_COPY.flag`。
2. `.env` 里的端口不是 80、443、5432、7038、7090、9267。
3. `compileall`。
4. 在 `Task\my_app` 跑 ruff `E9,F821`。

提交：`fix(my_app): 修复 send_via_browser 语法错误`

### 批 A：my_app、bot、知识库一起进 apps

必须同一批。知识库的默认路径依赖 my_app 的父目录，不能晚于 my_app 移动。

```text
Task\my_app            -> apps\my_app
Task\whatsapp-kb-bot   -> apps\whatsapp-kb-bot
Task\eflow_Q&A         -> apps\eflow_Q&A
```

`Task\_fe_dist` 仅当目录存在时移到 `apps\_fe_dist`。

同一提交里改写死的路径：

- `scripts\start_all.ps1`：`Task\my_app`、`Task\whatsapp-kb-bot`、`Task\eflow_Q&A`、`Task\_fe_dist`
- `nginx.conf.template` 里的 `Task/_fe_dist`
- `my_portal_ctl.ps1`
- `scripts\post_sync_fix_paths.ps1`
- `scripts\ensure_portal.ps1`
- `scripts\setup-git-hooks.mjs`
- 门户 `scripts\check-doc-consistency.mjs`：此时门户还在根上，改为 `../apps/my_app/...`。批 B 还要再改一次
- 根 `.gitignore` 的 `Task/_fe_dist`、`Task/_fe_dist_tmp`、`Task/_fe_dist_bak_*`、`Task/_fe_cloud`
- `scripts\sync-policy.mjs` 里的 `Task/my_app`、`Task/whatsapp-kb-bot`、`Task/eflow_Q&A`。这个文件即将停用，但路径不改会让干跑直接报错

不改：

- `backend.services` 的 import
- `routers` 的组织
- bot 叶子名
- `eflow_Q&A` 叶子名
- `eflow_qa.py` 里 `parents[3] / "eflow_Q&A"` 这行。移动之后它自然指向 `apps\eflow_Q&A`

验收（在仓库根）：

```powershell
python -m compileall -q apps
cd apps\my_app
python -m ruff check backend --select E9,F821
python -c "from pathlib import Path; import backend.routers.eflow_qa as q; r=Path(q._KB_ROOT); assert r.name=='eflow_Q&A'; assert (r/'tools'/'inspect_tt.py').is_file(); print('eflow ok', r)"
```

再断言这三个目录存在，且 `apps\whatsapp-kb-bot\scripts\tt-analyze.py` 存在。不在这里跑全量 pytest。

提交：`refactor: 将 my_app、bot 与 eflow 知识库移入 apps`

### 批 B：门户改为 apps\portal

先处理第 3.8 节的三份未提交文件，再移动：

```text
liberia-noc_soc-数据看板统一门户 -> apps\portal
```

同一提交修改：

- `nginx.conf.template` 里所有中文目录 alias，改为 `apps/portal/...`
- 涉及：`MsPortal`、`olbrportal`、`v1`、`v2`、`v3`、`team_portal`、`team-gate`、`team-login`
- `start_all.ps1`、`my_portal_ctl.ps1`、`ensure_portal.ps1`：不再用 `liberia-noc_soc-*` 在根上猜目录，改为固定 `apps\portal`
- 门户文档守卫里指向 my_app 的相对路径，改为 `apps\portal` 到 `apps\my_app` 的实际深度（`../../my_app`）

nginx 语法检查必须用 02 自己的程序和 02 渲染出的配置，不能用 PATH 里或 01 的 nginx：

```powershell
D:\02_olbr_portal\nginx-1.28.0\nginx.exe -p D:\02_olbr_portal\nginx-1.28.0 -t
```

渲染配置的脚本若会写文件，先确认写出的路径在 02 的 `nginx-1.28.0\conf` 下，不是 01。

门户检查：

```powershell
cd apps\portal
npm run lint
npm test
```

`npm ci` 只在 `node_modules` 缺失或锁文件变更时做。02 的门户依赖已经在。不要为了这一批去连外网重装，除非 lint 因依赖缺失失败。

`npm run build` 放到第 10 节冒烟前，和 my_app 前端构建一起做。批 B 先保证类型检查和单元测试通过。

提交：`refactor: 将统一门户移入 apps/portal`

### 批 C：运维脚本

```text
scripts   -> ops\deploy
my_portal -> ops\shortcuts
```

同一提交修改：

- 根 `start.bat`、`stop.bat`
- `my_portal_ctl.ps1`（若仍在根上）
- `.githooks\post-checkout`、`.githooks\post-merge`
- 快捷方式 bat 里的相对路径

`sync-now.bat`：改成打印「本仓库不再从 D:\01_RPA 同步」并 `exit /b 1`，然后从根目录移除。不要把它指到 `ops\deploy` 后继续调用 RPA 的 `deploy_to_copy.ps1`。

`ops\deploy\sync-engine.mjs` 和 `sync-policy.mjs` 保留文件，在 `ops\deploy\README.md` 写明：停用，禁止对这个新目录跑从 RPA 来的同步。

验收：干跑启动脚本，打印出的路径只有 `apps\` 和 `ops\`，没有 `Task\`，也没有 `D:\01_RPA`。钩子不把路径写回 01。

提交：`refactor: 将启动脚本移入 ops 并停用 RPA 同步入口`

### 批 D：长文档

根上只留 `AGENTS.md` 和 `README.md`。

移到 `docs\workspace\`：

```text
architecture.md
development.md
TODO.md
project-overview.md
user-guide.md
DESIGN.md
component-api.md
部署说明.md
```

同一提交更新 README、AGENTS 和这些文档里的现行路径、产品名、仓库地址。

不要改文档守卫用来对数字的那几句原句，例如门户 README 里「N 个 my_app 真实页面直链」「my_app 后端 N 个定时任务」。改路径可以，改这些句式会导致守卫自己失效。

验收：

```powershell
cd apps\portal
npm run check:docs
cd ..\my_app
python tests\test_doc_consistency.py
```

提交：`docs: 将根上长文档移入 docs/workspace`

### 批 E：边界说明、数据源、检查与 CI

新增：

```text
apps\README.md
docs\README.md
ops\README.md
runtime\README.md
docs\architecture\module-map.md
docs\architecture\data-source-registry.md
docs\adr\0001-directory-boundaries.md
docs\adr\0002-copy-isolation.md
ops\quality\check_all.ps1          扩展到下面的完整顺序
.github\workflows\quality.yml
```

`module-map.md` 写明：

- `my_app` 与 `whatsapp-kb-bot` 必须同级。
- `eflow_Q&A` 必须在 `my_app` 的父目录，叶子名不改。
- `_fe_dist` 必须在 `my_app` 的上一级。
- 02 禁止运行 `apps\my_app\start_hidden.ps1` 和同目录的 `start.bat`。隔离启动只走 `ops\deploy\start_all.ps1`。

`data-source-registry.md` 按第 3.4 节：平台模式写成已核实事实；单个数据源未盘完的标待确认。字段分两列：「当前事实」和「目标状态」。目标里可以写统一数据访问入口，但不要把目标写成已经完成。

`check_all.ps1` 的顺序：

1. 当前目录是 02，存在 `ISOLATED_COPY.flag`。
2. `.env` 端口不在生产端口集合里。
3. 被跟踪文件里没有 `.env`、数据库、私钥、session。
4. `python -m compileall -q apps ops\deploy`。
5. 在 `apps\my_app` 跑 ruff `E9,F821`。
6. 在 `apps\portal` 跑 `npm run lint` 和 `npm test`。
7. 在 `apps\portal` 跑 `npm run check:docs`。
8. 在 `apps\my_app` 跑 `python tests\test_doc_consistency.py`。
9. 路径断言：bot 引擎脚本、`eflow_Q&A\tools\inspect_tt.py`、`_fe_dist` 的相对关系。

第 6 到 8 步在依赖或基线还没准备好时可以单独跳过，但脚本必须说明跳过原因，不能假装通过。

GitHub Actions 同一份检查，注意：

- 使用 `windows-latest`。后端有 Windows 专用代码。
- 每个 job 写明 `working-directory`。
- 不访问 UME、eFlow、生产库、邮箱、WhatsApp、内网地址。
- 不跑还没标记好的「全量 pytest」。CI 先跑 compileall、ruff、门户 lint/test、两份文档守卫。
- live 标记加完、并且离线集合有基线之后，再把 `pytest -m "not live"` 加进 CI。那是后续提交，不塞进目录搬家。

提交可以分成两条：

```text
docs: 记录目录边界与数据源现状
ci: 增加离线质量检查
```

---

## 8. 隔离冒烟

目录批次都验收之后做一次。失败就修，不另开目录结构。

### 8.1 构建

```powershell
cd apps\my_app\frontend
$env:FE_DIST_OUT = 'D:\02_olbr_portal\apps\_fe_dist'
npm run build

cd apps\portal
npm run build
```

`FE_DIST_OUT` 必须指到 02。不设置的话，相对路径也会落到 `apps\_fe_dist`，但要在构建后核对，防止写进 01 或 RPA。

### 8.2 数据

启用 02 已有的 `runtime\pgdata.off-20261112`，只听 `127.0.0.1:55432`。启动后执行 `SHOW data_directory`，结果必须在 02 下面。不是本机 5432 上的生产实例。

三张本地权威表从 01 的 `app.db` 迁移到 02，使用：

```text
scripts\local_authority_tools.py
```

搬到 `ops\deploy` 之后用新路径。不要直接拷贝正在被生产打开的 `app.db` 文件。

这三张表里包含生产凭据。处理方式：

- 只放在 02 本机数据库，不 `git add`。
- 调度器保持关闭，不跑采集，不发信，不登录 WhatsApp。
- 若冒烟必须带上 `email_recipients`，把收件人改成测试地址，或确认发信开关是关的。
- 不把「复制了 credentials」说成「副本没有生产凭据」。库里有，只是不允许拿它们去连上游。

### 8.3 启动与检查

```powershell
ops\deploy\start_all.ps1
```

启动前确认：调度器关闭，WhatsApp bot 关闭，`.env` 是 02 的隔离配置，不是从 01 拷来的那份。

端口：

```text
nginx HTTP       8080
nginx HTTPS      8443
my_app backend   7039
my_app frontend  7091
portal           9277
PostgreSQL       55432
WhatsApp bot     9279，保持关闭
```

检查：

1. `http://127.0.0.1:7039/api/health` 里 `db_mode=pg`。
2. 一个只读业务接口返回快照中的数据，不是空库。
3. 门户 `9277` 返回 200。
4. nginx `8080` 和 `8443` 返回 200。
5. 前端入口 hash 与 `apps\_fe_dist` 里的 `index.html` 一致。
6. 用第 7 节批 B 的方式跑 `nginx -t` 通过。

### 8.4 停止

停止命令只结束 02 写在自己 pid 文件里的进程。停止后确认：

- 80/443 仍由 `D:\01_olbr_portal\nginx-1.28.0\nginx.exe` 监听。
- 02 的 8080、8443、7039、7091、9277、55432 已释放。

---

## 9. 回滚

每一批开始前记下：commit、分支、`git status`、02 端口、构建入口 hash。

已提交的批次用 `git revert`，不用 `reset` 去改已经推送的历史。还没提交的，恢复到这一批开始前的 commit。

禁止用 01 的目录覆盖 02，也禁止删除 01。

回滚演练不在 `refactor/round-1` 上做。从批 A 的 commit 拉一个临时分支，在临时分支上 revert，再跑第 7 节批 A 的验收。演练完删掉临时分支。`refactor/round-1` 始终保留已验收的批次。

演练记录写到 `docs\operations\rollback-drill.md`。不要把演练过程中的失败状态留在主重构分支上。

---

## 10. 推到新仓库

本地这一轮在 `refactor/round-1` 上完成后，再换远程。顺序不能反。

### 10.1 先确认新仓库

新仓库应为空，或者只有你确认可以保留的初始化提交。不要在没看过远程内容时覆盖它。

不要先 `git remote remove origin`。旧远程还是回退通道。

```powershell
git remote add platform https://github.com/Jouwala/noc-soc-intelligent-platform.git
git ls-remote platform
```

`ls-remote` 失败就停。此时旧 origin 还在。

### 10.2 先推基线，再推重构分支

当前稳定分支叫 `master`，不是 `main`。新仓库用 `main`。

```powershell
git push platform refactor-baseline-2026-11-13^{commit}:refs/heads/main
git push -u platform refactor/round-1
```

第一句把基线 commit 推成远程 `main`，这样 PR 有对比基线。不要把重构分支直接当成远程上唯一的分支。

若 `ls-remote` 看到远程 `main` 已有别人的提交，停下来看差异，不要强推。

### 10.3 拉取请求

```text
base: main
compare: refactor/round-1
```

说明里写：范围、没做的事、生产隔离、测试命令和结果、新旧路径对照、回滚方式。

CI 通过并且你看过差异之后，再合并到 `main`。合并后打 tag `refactor-round-1-complete`。

确认 `platform` 可用之后，再把 `origin` 改为这个新地址。在那之前保留旧 origin。

---

## 11. 第二轮，不在这一轮做

第一轮冒烟通过、新仓库的 `main` 可部署之后再开。

顺序：

1. 给会访问外部系统的测试加上 `live` 标记，并在 `apps\my_app` 跑出离线测试基线。然后 CI 才加入 `pytest -m "not live"`。
2. 数据访问收口。先只读查询，后写入任务。新入口放在 `apps\my_app\backend\` 里面，不在仓库根建一个靠 `sys.path` 拼接的 `integrations\`。
3. 外部系统客户端。仍放在现有包内，补超时、重试、错误类型和健康检查。
4. `services` 根上大约 162 个散落的 `.py`，按域收进已有子目录。每一组单独提交，同时改 import 和测试。
5. 扩展目录、`PROJECT_DOCS.md`、生产启停脚本的归位。每一组先 rg 引用，再移动。
6. 核心服务里的 `except: pass` 改为带上下文的日志。一次性脚本可以继续打 stdout。

---

## 12. 第一轮完成标准

代码：

- `compileall` 通过。
- `apps\my_app` 下 ruff `E9,F821` 为 0。
- 门户 `lint`、`test` 通过。
- 两份文档守卫通过。
- my_app 前端构建落在 `apps\_fe_dist`。
- 门户构建通过。

目录：

- 根上没有中文长目录，没有空的 `Task\`。
- `my_app`、`whatsapp-kb-bot`、`eflow_Q&A` 三者同级。
- `_fe_dist` 在 `my_app` 的上一级，且未入库。
- `runtime`、`nginx-1.28.0`、`.env` 未入库。

行为：

- 不经 `start_all.ps1`、直接导入 `eflow_qa` 时，知识库路径是 `apps\eflow_Q&A`。
- 根上没有会调用 `D:\01_RPA\scripts\deploy_to_copy.ps1` 的入口。
- 隔离冒烟的健康检查、只读接口、门户、nginx 通过。
- 停止后 01 的 80/443 进程不变。

仓库：

- 远程 `platform` 指向 `Jouwala/noc-soc-intelligent-platform`。
- `main` 是基线，重构经 PR 合并。
- README 使用新产品名。
- 推送的历史里没有凭据、session、数据库和构建产物。

---

## 13. 提交顺序

```text
fix(my_app): 修复 send_via_browser 语法错误
refactor: 将 my_app、bot 与 eflow 知识库移入 apps
refactor: 将统一门户移入 apps/portal
refactor: 将启动脚本移入 ops 并停用 RPA 同步入口
docs: 将根上长文档移入 docs/workspace
docs: 记录目录边界与数据源现状
ci: 增加离线质量检查
```

门户那三份性能改动如果要留，单独一条，不和上面任何一条混在一起。

每条提交只做一件事，能单独看懂，能单独回滚。
