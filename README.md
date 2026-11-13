# NOC/SOC Intelligent Platform —— 利比里亚 NOC 数据看板统一门户（便携部署版）

本仓库是 `D:\02_olbr_portal` 的**源码与运维脚本**版本库。

| 项 | 说明 |
| --- | --- |
| 包含 | 统一门户（React + Vite）、my_app（FastAPI 后端 + React 前端）、WhatsApp 知识库 bot、便携部署与运维脚本 |
| 不包含 | 运行时二进制、数据库、构建产物、任何凭据（见下方「不入库的内容」） |
| 配套主仓库 | `Jouwala/my_tools_Liberia_noc1-rpa`（`D:\01_RPA`）—— 开发态主仓库，本仓库是可独立部署的便携副本 |

---

## 一、这是什么

一套在利比里亚运营的可视化运维平台，由 4 个可独立运行、也可整套联动的子系统组成：

| 子系统 | 目录 | 端口（隔离实例） | 说明 |
| --- | --- | --- | --- |
| 统一门户 | `apps\portal/` | 9277 | 静态壳 + React 应用 + Node 鉴权服务（含加盐密码哈希） |
| my_app | `apps/my_app/` | 7039 / 7091 | FastAPI 后端（采集、KPI、告警、看板数据）+ React 前端 |
| 问答助手挂件 | `apps\portal/v3/` | — | 嵌入门户的大屏与问答组件 |
| WhatsApp bot | `apps/whatsapp-kb-bot/` | 9279 | Baileys bot，知识库 RAG 问答 |

外加 `nginx-1.28.0/` 作反向代理、`runtime/` 作捆绑运行时（Python 3.12 / Node 24 / PostgreSQL 16）。

---

## 二、快速开始

### 1. 拿代码

```bash
git clone https://github.com/Jouwala/noc-soc-intelligent-platform.git
cd noc-soc-intelligent-platform
```

### 2. 补运行时（必需，仓库里没有）

仓库只存源码，运行时需另行准备。可从主仓库的打包脚本获取：

```powershell
powershell -NoProfile -File D:\01_RPA\scripts\build_olbr_portable.ps1 -Stage runtimes
```

或手工准备这 4 样到 `runtime/` 下：

| 目录 | 内容 | 来源 |
| --- | --- | --- |
| `runtime/python312/` | Python 3.12.10 + fastapi/uvicorn/psycopg2/sqlalchemy/alembic/apscheduler | 官方安装包 |
| `runtime/node/` | Node v24.16.0 | 官方安装包 |
| `runtime/postgresql16/` | PostgreSQL 16 服务端 | 官方安装包 |
| `runtime/pgdata/` | 便携 PG 数据目录 | **首次启动时自动 initdb**，无需预置 |

### 3. 装依赖

```bash
cd apps/my_app/frontend && npm ci
cd ../../apps\portal && npm ci
cd ../apps/whatsapp-kb-bot && npm ci
```

### 4. 配置

```bash
cp scripts/env_template.txt .env
```

按需改端口与主机。**`.env` 不入库**（含口令）。

### 5. 启动

```bat
start.bat
```

或用 `ops/shortcuts/` 里的分步入口（`0-status` / `1-start-all` / `5-nginx-check` …）。

入口：<https://localhost:8443/>（自签证书，浏览器提示不信任属预期）

详细部署说明见 **`部署说明.md`**。

---

## 三、不入库的内容

以下内容**刻意不进版本库**，但**必须留在磁盘上**才能正常运行 —— 忽略跟踪 ≠ 删除。

| 路径 | 体积 | 原因 |
| --- | --- | --- |
| `runtime/` | 6.5 GB | Python/Node/PG 第三方发行包 + PG 数据目录（不可移植、远超 GitHub 单文件上限） |
| `nginx-1.28.0/` | 37 MB | nginx 二进制 + 地图瓦片 + `certs/portal.key` 自签私钥 |
| `apps/_fe_dist/` | 277 MB | 前端构建产物，可由 `npm run build` 重建 |
| `apps/my_app/db/` | 2.5 GB | `app.db`（SQLite 兜底库）+ 主站点库 xlsx |
| `apps/my_app/data/` | 2.7 GB | 派生 SQLite |
| `apps/whatsapp-kb-bot/data/` | 103 MB | 向量索引 + 问答记录 + WhatsApp 登录凭据 |
| `TMP/` | 182 MB | PG 快照、切换备份等中间态 |
| `apps/whatsapp_session/` | 40 MB | Baileys 设备凭据（auth_info） |
| `*.env` | — | 含明文口令 / API token；模板以 `.env.example` 形式入库 |

另有抓包样本（`url*.txt`）、本地凭据文件（`_local-credentials.js`、`CREDENTIALS_LOCAL_REFERENCE.txt`）被显式排除 —— 它们含**有效会话凭据**。

---

## 四、与主仓库的关系

> ⚠ 本仓库（02 隔离副本）已不再从主仓库同步：同步链已随第一轮重构批 C 停用，根 `sync-now.bat` 已删除。下述同步流程是主仓库 → 01 副本的历史做法，仅供对照；02 的隔离启动见 `ops\deploy\README.md`。

```
D:\01_RPA  (主仓库 / 开发态)          D:\02_olbr_portal  (本仓库 / 便携部署态)
        │                                        │
        │  sync-now.bat → D:\01_RPA\scripts\deploy_to_copy.ps1
        │    └─ ops\deploy\sync-engine.mjs（事务式增量同步，源只读）─►│
        └────────────────────────────────────────►│
                                                 │  同步源码 + 确定性改写两处监控脚本路径
                                                 │  （不再需要 post_sync_fix_paths.ps1）
```

**日常就双击根目录 `sync-now.bat`**。它调用编辑区的 `D:\01_RPA\scripts\deploy_to_copy.ps1`：
同步源码（引擎仍是本目录 `ops\deploy\sync-engine.mjs`，源只读）+ 在副本里重建前端 + 校验线上是否还在跑旧代码。
**不重启服务**。只搬源码、不重建时用 `ops\deploy\sync_from_main.ps1`，或 `sync-now.bat -SkipBuild`。

```bat
sync-now.bat                  REM 同步 + 重建前端 + 校验
sync-now.bat -DryRun          REM 只预览计划，不写任何文件
sync-now.bat -SkipBuild       REM 只同步源码，不重建前端
sync-now.bat -AcceptSource    REM 显式接受源版本覆盖冲突文件（先备份，可回滚）
```

**同步纪律**：

1. **源只读、副本唯一写入目标**；要求副本根有 `ISOLATED_COPY.flag`，源/副本路径不得重叠或为 junction。
2. **只同步源码白名单**（`sync-policy.mjs`）：backend/frontend/alembic/docs/tests/tools 等 + 显式业务资源
   （eflow 规则 xlsx、EMS 模板/样本、公共 CA）。**绝不**同步运行时凭据/会话（`.env`、`*session*.json`、
   `auth*.json`、`config.json` 等）、`node_modules`、`data/db`、构建产物。
3. **构建产物不同步**：`apps/_fe_dist` 与门户 `dist` 由副本**自行构建**（`npm run build`），不从生产拷贝。
4. **两处监控脚本路径在同步时确定性改写**为副本根（`nginx_monitor.ps1` 5 个 param、门户 `rotate_nginx_logs.ps1`
   的 `$Prefix`），匹配数必须恰为 1 否则报错停止 —— 不再依赖 `post_sync_fix_paths.ps1`。
5. **事务式应用**：逐文件备份到 `TMP\_pre_sync_<时间戳>`、原子改名，任一步失败整体回滚；状态最后原子落盘
   （`TMP\_sync_state.json`，SHA256 内容真相）。源已删除的受管文件隔离到 `TMP\_sync_removed_<时间戳>`。
6. **冲突保护**：副本本地改过（哈希≠状态）的**源码**默认整体停止并列出；`-AcceptSource` 才覆盖（仍备份）。
   定时任务重写的运行时产物（`daily_raw.json`、三份 eflow 规则 xlsx、`*_rules_check_latest.json`、`MATCHING_REPORT.md`）
   不算冲突：副本上已有就保留，没有才从源播种。不要用 `-AcceptSource` 去盖这些文件，那会把今天的规则和日报退回源上的旧快照。
7. **同步 ≠ 部署完成**：前端输入/依赖清单/迁移变化时引擎会打印部署提示，需另行构建/核对依赖/评估迁移。

### 换机器 clone 后必做一步

```bash
node ops/deploy/setup-git-hooks.mjs
```

它把 `core.hooksPath` 指向仓库内的 `.githooks/`（该配置是**本地配置**，不入库，每台机器装一次）。装上之后：

| 钩子 | 触发时机 | 作用 |
| --- | --- | --- |
| `.githooks/post-checkout` | clone / checkout / 切分支 | 把两个脚本里的**本机绝对路径**改成本机副本根目录 |
| `.githooks/post-merge` | merge / pull | 同上 |

为什么必须有这一步：本仓库跟踪着两个**含本机绝对路径**的脚本，而路径必须因机器而异。git 检出的是入库时的字节，`D:\02_olbr_portal\...` 会原样出现在新机器上 —— nginx watchdog 会去探测一个不存在的目录，nginx 根本起不来。

> 换机器后 `git status` 会显示那两个文件有差异，这是**预期**的：那正是「本机路径」的体现。在同一台机器上，改写结果是逐字节一致的，status 保持干净。

---

## 五、隔离模式

本副本当前是**完全隔离实例**，端口与生产（`D:\01_olbr_portal`）完全错开，可与生产并行启动：

| | 生产 | 本副本 |
| --- | --- | --- |
| HTTP / HTTPS | 80 / 443 | 8080 / 8443 |
| my_app 后端 / 前端 | 7038 / 7090 | 7039 / 7091 |
| 门户 / bot / 分析 | 9267 / 9269 / 9268 | 9277 / 9279 / 9278 |
| 实时代理 FMS / VMAX | 8787 / 8790 | 8887 / 8890 |
| PostgreSQL | 5432 | 55432 |

根目录的 `ISOLATED_COPY.flag` 让 `my_portal_ctl.ps1` 拦截所有 start/stop 命令（防误停生产）；
`start_all.ps1` / `sync-engine.mjs` / `bundle_data.ps1` 也都要求该标志存在。**转正为生产时**：改回生产端口 + 删除该 flag。

`start_all.ps1` 的隔离保障：副本 `.env` 优先于继承的进程环境（防生产污染）、拒绝任何落在生产端口上的配置、
派生变量无条件按副本根覆盖、清除 `PYTHONPATH/PYTHONHOME/NODE_PATH`、强制 `MY_APP_DISABLE_SCHEDULER=1` 与
`START_WA_BOT=0`（不接管真实 WhatsApp 会话/投递）、启动前预检核心入口、PG 写入前校验 `data_directory` 属于副本。
实时代理控制器按「进程命令行含本副本脚本绝对路径」验归属，**绝不复用或击杀生产 8787/8790 代理**。

> 源码侧的 `D:\01_RPA` 硬编码路径已于 2026-11-11 收口：业务数据目录、邮件/代理解释器（`sys.executable`）、
> 前端产物目录（`FE_DIST_OUT`/相对路径）、Chrome、cn_ems 模板、eflow 问答工具均改为按副本位置定位或 env 覆盖。

---

## 六、安全提示

本仓库为 **PUBLIC**，故：

- 所有口令、token、私钥、加盐密码哈希**一律不入库**（见「不入库的内容」）。
- **两处历史遗留的硬编码凭据已于 2026-11-10 从源码收口**（同步自主仓库 PR #9）：
  - `apps/my_app/backend/migrations/check_pg_alert_log.py` 的 PG 口令 → 读环境变量 `PG_ALERT_LOG_PASSWORD`；
    未提供时脚本以退出码 2 结束并提示（刻意「不连」而非用弱口令连接）。
  - `apps/whatsapp-kb-bot/scripts/tt-bridge-hidden.vbs` 的 TT 桥接令牌 → 依次读环境变量
    `TT_BRIDGE_TOKEN`、同目录 `.tt_bridge_token` 文件；两者都没有则报错退出。
    该文件已被 `.gitignore` 挡住，**请在本机自建**（内容即原来的令牌值），
    否则手工启动 TT 桥接会报「token is not configured」。
  - ⚠ **改代码不能消除历史泄露** —— 这两个旧值在 git 历史里仍可见，仍需服务端轮换。
- `cxo_dashboard.html` 内嵌抓包数据含有效 `JSESSIONID`，属历史遗留，同样应视为已泄露。
- 抓包样本 `url*.txt` 被显式排除；其中 `vmax_cca/url.txt` 含有效 `JSESSIONID`、`cm_data/new_url.txt` 含有效 `forgerydefense` 会话 cookie。
  - ⚠ **子目录 `.gitignore` 优先级高于根目录** —— 主仓库 `apps/my_app/.gitignore` 若再出现
    `!` 放行（例如 `!/backend/services/vmax_cca/url.txt`），同步过来会**打穿**根目录更严的
    `**/*url*.txt`，把含凭据的抓包重新暴露。改动忽略规则后请两端一起复核。

---

## 七、开发约定

- **i18n 铁律**：新文案一律 `t('key')` 并同时补 zh/en，禁止内联三元与 `label_zh`。
- **标注准确性铁律**：写「某函数访问 X / 上传 FTP」必须 grep 真实调用点，禁止由常量定义推断。
- **快照不进版本库**：`*_bak_*` / `*.bak_*` / `*.bak-*` / `*.bak.*` 一律排除。
- **改动前先跑守卫**：`apps\portal/scripts/check-*.mjs`（整洁度 / 团队范围 / 文档一致性 / i18n）。
- 详见 `AGENTS.md`。

## 八、许可

内部项目，版权归 ZTE 所有。
