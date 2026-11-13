# apps — 子系统目录

本仓库的四个子系统都在这里，彼此是**平级目录**，不要再往根上加子系统。

| 目录 | 是什么 | 技术栈 | 文档入口 |
| --- | --- | --- | --- |
| `apps/my_app/` | Data Insight MVP（网络运维数据平台，自研主系统） | FastAPI + SQLite/PG + React | `apps/my_app/AGENTS.md`、`apps/my_app/docs/manual/` |
| `apps/portal/` | Liberia NOC/SOC 数据看板统一门户 | React 19 + Vite + Tailwind v4 | `apps/portal/docs/AGENTS.md`、`apps/portal/README.md` |
| `apps/whatsapp-kb-bot/` | WhatsApp 知识库问答 bot | Node | `apps/whatsapp-kb-bot/docs/` |
| `apps/eflow_Q&A/` | eFlow 知识库（问答/排障文档，含 `tools/inspect_tt.py`） | Markdown + Python | `apps/eflow_Q&A/README.md` |

## 目录边界（硬性，见 `docs/adr/0001-directory-boundaries.md`）

- `my_app` 与 `whatsapp-kb-bot` **必须同级**，bot 的叶子名不改。`my_app` 里的
  `portal_qa_ctl.py`、`tt_alarm_render.py`、`wa_notify_service.py` 和门户的
  `myAppPaths.js` 都是按「父目录 / `whatsapp-kb-bot`」找 bot 的，拆开即坏。
- `eflow_Q&A` **必须在 `my_app` 的父目录**（即 `apps/eflow_Q&A`），叶子名不改，
  不放进 `docs`。`backend/routers/eflow_qa.py` 的默认目录是「本文件向上 3 级 /
  `eflow_Q&A`」，`main.py` 启动时还会预热其中的 `tools/inspect_tt.py`。
- `_fe_dist` **必须在 `my_app` 的上一级**（即 `apps/_fe_dist`）。`vite.config.ts`
  的 `outDir` 是 `../../_fe_dist`，后端 `main.py` 是「向上 3 级 / `_fe_dist`」。
  构建产物不入库（见根 `.gitignore`），02 里没有这个目录时不要为搬家去建空目录。
- 相对关系总表见 `docs/architecture/module-map.md`；`ops/quality/check_all.ps1`
  第 9 步会断言这三处相对关系。

## 启动（硬性，见 `docs/adr/0002-copy-isolation.md`）

- 02 隔离副本**禁止**运行 `apps/my_app/start_hidden.ps1` 和同目录的 `start.bat`
  （那是生产启动脚本）。隔离启动只走 `ops/deploy/start_all.ps1`。
- 不要把 `apps/my_app/start_hidden.ps1` 之类的生产启动脚本搬到 `ops/deploy`。
