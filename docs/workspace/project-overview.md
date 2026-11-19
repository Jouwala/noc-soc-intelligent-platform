# 04 project-overview.md —— 工作区整体说明

> 基准日期：2026-11-19　|　适用：`D:\02_olbr_portal` 工作区根目录

## 一、这个工作区是什么

`D:\02_olbr_portal` 是**利比里亚（Liberia）电信网络运维（NOC/SOC）的自动化与数据看板平台**，
服务于 ZTE 侧的网络运维交付。它同时是：

- **源码仓库**：所有子系统、文档与运维脚本都在这里，独立 git 仓库（`main` 跟踪
  `Jouwala/noc-soc-intelligent-platform` 的 `platform` 远程）；
- **生产部署目录**：2026-11-18/19 起接管本机（MSNOC1 / `192.168.100.20`）的
  80/443/7038/7090/9267/9268 与系统 PostgreSQL 5432，根目录有 `PRODUCTION.flag`。

它把三类东西收在一起：

1. **自研数据平台**（`apps/my_app`，Data Insight MVP）—— 网络 KPI、告警、工单、库存等数据的采集、入库与展示；
2. **统一门户**（`apps/portal`）—— 把 FineBI 报表与 my_app 页面聚合到一个入口，给不同角色看；
3. **协作与交付工具**（`apps/agent-worker`、`apps/control-plane`、`apps/whatsapp-kb-bot`、
   `apps/eflow_Q&A`、`apps/Site_outage`）—— 多 worker 任务执行、通知与知识库。

三者不是并列的独立项目，而是**一条数据链**：上游系统 → my_app 采集与入库 → 门户聚合呈现 → 人（值班/管理）决策。

> **与 `D:\01_RPA` 已完全脱离**：不同步、不互为主副本。`D:\01_olbr_portal` 是切换前的旧生产，
> 只作为回退目标。详见根 `README.md` 第四节。

## 二、主要系统

### 1. my_app —— Data Insight MVP（自研主系统）

- **位置**：`apps/my_app/`
- **定位**：网络运维数据的**唯一自研数据底座**。功能页覆盖同步、告警、工单、SLA、GIS、库存、电池、丢包、IP Trace 等。
- **技术栈**：后端 FastAPI + SQLAlchemy（SQLite / PostgreSQL 双模）+ Alembic；前端 React + Vite；
  任务经 worker 分组执行 + 看门狗守护。
- **部署**：前端 `7090`（`base=/myapp/`）、后端 uvicorn `7038`，由本机 nginx 反代；
  生产库仍以 PostgreSQL `192.168.100.20:5432/myapp` 为主。
- **文档**：`apps/my_app/docs/manual/`（**现状以此为准**）；`docs/archive/PROJECT_DOCS.md` 是历史档案。

### 2. apps/portal —— 统一入口

- **位置**：`apps/portal/`
- **定位**：**聚合入口**。内置 my_app 真实页面直链（`?page=` 查询参数格式）+ FineBI 报表分类条目，
  提供卡片网格 / 高密清单 / 业务分类矩阵三种视图。
- **技术栈**：React 19 + Vite + Tailwind v4；门户自身后端（Express/server 目录）提供鉴权、看板配置、QC 指标接口等。
- **关键能力**：环境一键切换（批量替换 IP/域名）、深浅双模、中英文国际化、收藏置顶、访问热度、
  JSON 导入导出、二维码扫码与 iframe 预览、实时任务调度部件（每 60s 拉 `/api/tasks`）。
- **部署**：本机 nginx 把 `/uniplam/` 反代到 `9267`；门户后端 API 前缀为 `/uniplam/api/*`
  （**不是** `/api/*`，见 `src/config.ts` 中的原因说明）。
- **文档**：`apps/portal/README.md`（功能概览）；`MsPortal/` 下有若干专项记录。

### 3. FineBI 决策报表中心（Liberia Data Portal）

- **定位**：上游既有 BI 平台，承载历史与官方口径的报表。
- **入口**：`http://192.168.100.19/webroot/decision`（不经过本机 nginx，门户里直接链过去）
- **状态**：部分报表链接仍为**目录占位**（标签含"链接待更新"、状态 beta），需在门户内"编辑看板配置"逐条替换为实际 URL。
- **方向**：正在把 FineBI 看板逐步迁移为 my_app 自研页面（见 `apps/my_app/docs/FINEBI_MIGRATION.md`、
  门户 `apps/portal/scripts/migrate_finebi_to_myapp.py`）。

### 4. 协作与支撑

| 目录 | 作用 |
| --- | --- |
| `apps/agent-worker/` | 按 `docs/workspace/agent-protocol.md` 实现的多 worker 执行器（认领、租约、心跳、取消、一任务一 worktree） |
| `apps/control-plane/` | 多 worker 中央调度器（任务权威状态） |
| `apps/whatsapp-kb-bot/` | WhatsApp 知识库问答 bot（Baileys + RAG） |
| `apps/eflow_Q&A/` | eFlow 知识库（问答/排障文档与取数工具） |
| `apps/Site_outage/` | 掉站相关运行时脚本与浏览器会话 |

## 三、数据流（一页看懂）

```
 网络设备 / 上游系统 (UME、eFlow、CCN、PM、邮件、WhatsApp ...)
        │  采集（定时任务，worker 分组执行）
        ▼
   my_app 后端 (7038)  ──►  PostgreSQL (5432) / SQLite (db\app.db 等)
        │                        │
        │  REST /api/*          │ 报表/图片取数
        ▼                        ▼
   my_app 前端 (7090,/myapp/)   FineBI (192.168.100.19:/webroot/decision)
        │                        │
        └────────┬───────────────┘
                 ▼
        统一门户 (9267, /uniplam/)  ← 人在这里看
```

细节与端口级拓扑见 `architecture.md`。

## 四、角色与关注点

| 角色 | 关心什么 | 主要看哪 |
| --- | --- | --- |
| NOC/SOC 值班 | 实时告警、掉站、工单、任务是否在跑 | 门户告警/QC 面板、my_app 实时页 |
| 网络优化工程师 | KPI 趋势、SLA、地理分布 | FineBI 报表、my_app KPI/GIS 页 |
| 管理层 | 达标率、超期、宏观概览 | 门户矩阵视图、CXO 看板 |
| 交付/开发 | 数据能否采到、任务有没有挂 | `13-operations.md`、worker 日志 |

## 五、目录速查

| 目录 | 内容 |
| --- | --- |
| `apps\` | 各子系统（见上表）；`apps\_fe_dist\` 是 my_app 前端构建产物（线上服务目录，不入库、不可删） |
| `docs\` | 文档：`workspace\`（体系文档）、`adr\`（架构决策）、`architecture\`（模块/数据源地图）、`archive\`（历史档案） |
| `ops\` | 运维脚本：`deploy\`（启停/冒烟/打包/迁移）、`quality\`（离线质量检查）、`shortcuts\`（生产入口 bat） |
| `nginx-1.28.0\` | **本机生产 nginx**（80/443 持有者，不入库、不可删） |
| `runtime\` | 捆绑运行时（Python/Node/PG），只服务隔离副本与打包；生产用系统 Python/Node/PG |
| `desktop_shortcuts\` | 桌面快捷方式生成器 |
| `TMP\` | 一次性探针、快照、切换备份（不入库） |

## 六、术语表

| 术语 | 含义 |
| --- | --- |
| **my_app** / Data Insight MVP | 自研网络运维数据平台 |
| **门户 / uniplam** | Liberia NOC/SOC 数据看板统一门户（部署路径 `/uniplam/`） |
| **FineBI** | 上游决策报表平台 |
| **QC** | Quality Control，门户上的质量指标卡面板（告警、PMT、超期等） |
| **eflow / eflow_tt** | 工单流水数据源与工单表 |
| **TT** | Trouble Ticket，故障工单 |
| **PMT** | 待处理/超期工单指标 |
| **SLA** | 服务等级协议（MS SLA 为一等公民指标） |
| **UME** | 上游网管系统（大屏接口来源） |
| **PM** | Performance Management，性能数据（`cn_ems` 相关） |
| **掉站 / Site out** | 基站退服 |
| **worker** | my_app 的任务执行进程（多个分组 + 看门狗）；与 `apps/agent-worker` 的协作 worker 不是一回事 |
| **`apps\_fe_dist`** | my_app 前端构建产物目录（后端按此路径找静态文件） |

## 七、相关文档

| 内容 | 去哪里 |
| --- | --- |
| 仓库角色、启停、与 01 的关系 | 根 `README.md` |
| 工作区地图与 AI 约束 | `../AGENTS.md` |
| 架构与数据流细节 | `architecture.md` |
| 当前在办事项 | `TODO.md` |
| 使用者说明 | `user-guide.md` |
| 开发与回归 | `development.md` |
| 视觉规则 | `DESIGN.md` |
| my_app 现状（权威） | `apps\my_app\docs\manual\01-overview.md`、`11-feature-guide.md` |
| 门户功能概览（权威） | `apps\portal\README.md` |

> **工具负责整理，人与 AI 负责协作执行。**
