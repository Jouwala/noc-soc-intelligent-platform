# ADR 0002：副本隔离

- 状态：已接受
- 日期：2026-11-13
- 范围：第一轮重构，`refactor/round-1`

## 背景

同一份代码有三个位置：生产 `D:\01_olbr_portal`（听 80/443/7038/7090/9267/
5432）、编辑区 `D:\01_RPA`（主仓库）、重构工作区 `D:\02_olbr_portal`。
2026-11-11 实测事故证明「只同步源码」是不够的：前端是构建产物、后端是常驻
进程，只同步不重建、只重建不重启会造成前后端 split-brain（前端 chunk 已含
`col_filters`，live 后端却没有对应参数与路由），用户看到的是「完全坏掉的新
功能」而不是报错。

## 决策

1. **编辑区 ≠ 运行副本**。在 `D:\02_olbr_portal` 改完，按
   `ops/deploy/README.md` 隔离启动验证；生产本轮不动。
2. **隔离启动只走 `ops/deploy/start_all.ps1`**。02 禁止运行
   `apps\my_app\start_hidden.ps1` 和同目录的 `start.bat`；启动前确认调度器
   关闭、WhatsApp bot 关闭、`.env` 是 02 的隔离配置。
   02 的端口：nginx 8080/8443、my_app 后端 7039、my_app 前端 7091、门户
   9277、PostgreSQL 55432、WhatsApp bot 9279（保持关闭）。
3. **从 RPA 来的同步已停用**。根 `sync-now.bat` 已删除，
   `ops/deploy/sync-engine.mjs` / `sync-policy.mjs` / `sync_from_main.ps1`
   只保留备查，禁止对这个新目录跑从 RPA 来的同步；不要在这里恢复同步入口。
4. **凭据与运行态不入库**。不提交 `.env`、数据库、日志、session、构建产物、
   凭据；`ops/quality/check_all.ps1` 第 3 步用 `git ls-files` 检查被跟踪文件。
   三张本地权威表（`credentials`、`task_schedule_config`、`email_recipients`）
   只放在 02 本机数据库，见 `docs/architecture/data-source-registry.md`。
5. **停止只结束 02 自己 pid 文件里的进程**。停止后确认 80/443 仍由
   `D:\01_olbr_portal\nginx-1.28.0\nginx.exe` 监听，02 的端口已释放。
6. **第一轮合并后，给 `main` 加分支保护**（PR + 必过检查，与主仓库做法一致），
   否则「经 PR 合并」没有强制力。**这是 GitHub 网页操作，不在提交里**：
   合并 `refactor/round-1` 的 PR 之后，在仓库 Settings → Branches 里给 `main`
   开启保护（要求 PR、要求检查通过），再打 tag `refactor-round-1-complete`。

## 后果

- 正面：02 的改动、启动、停止都不会碰到生产；同步链停用后，02 不会被旧目录
  的同步盖回去。
- 负面：02 每次验证都要走完整隔离启动；新机器要先补齐 `runtime/`（见
  `runtime/README.md`）并装一次 git 钩子（`node ops/deploy/setup-git-hooks.mjs`）。
- 校验：`check_all.ps1` 第 1–3 步（目录与 flag、`.env` 端口、被跟踪文件）
  与方案第 8 节隔离冒烟。

## 相关

- 方案第 2、8、10 节；`docs/architecture/module-map.md`；根 `AGENTS.md`
  第四节第 6b 条（两套 nginx 进程并存）与「编辑区 ≠ 运行副本」一节。
