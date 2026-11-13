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
- `ensure_portal.ps1`：门户保活（计划任务调用）
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

## 路径约定

- 所有脚本都用自身位置推导仓库根（`ops/deploy` 的上一级的上一级），
  不写死盘符；`start_all.ps1` / `stop_all.ps1` 要求副本带
  `runtime\`（python312 / node / postgresql16）与 `ISOLATED_COPY.flag`。
- 不要把 `apps/my_app/start_hidden.ps1` 之类的生产启动脚本搬到这里；
  隔离启动只走 `ops/deploy/start_all.ps1`。
