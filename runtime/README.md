# runtime — 捆绑运行时（不入库，本文件除外）

`runtime/` 整个目录被根 `.gitignore` 排除（数 GB 的第三方发行包与运行态数据），
**只有本文件是例外**（`.gitignore` 里 `!/runtime/README.md`），用来说明这里
应该有什么、从哪里补。

> **生产不用这里**：本机生产跑的是系统 Python 3.12（`%LOCALAPPDATA%\Programs\Python\Python312`）、
> 系统 Node（`C:\Program Files\nodejs`）与系统 PostgreSQL 5432。
> `runtime/` 只服务**隔离副本 / 冒烟 runner / 打包交付**。

| 目录 | 内容 | 来源 |
| --- | --- | --- |
| `runtime/python312/` | Python 3.12.10 + fastapi/uvicorn/psycopg2/sqlalchemy/alembic/apscheduler | 官方安装包 |
| `runtime/node/` | Node v24.16.0 | 官方安装包 |
| `runtime/postgresql16/` | PostgreSQL 16 服务端二进制 | 官方安装包 |
| `runtime/pgdata/` | 便携 PostgreSQL 的数据目录（initdb 出来的库文件） | 首次 `start_all.ps1` 自动 initdb |
| `runtime/portal-auth-data/` | 门户鉴权数据（含加盐密码哈希） | 运行时写入，严禁入库 |

## 补齐方式

- 按 `ops/deploy/runtime-manifest.json` 的清单从官方渠道下载，并用清单里的 SHA256 校验
  （`runtime-build.yml` 就是这么做的；`url`/`sha256` 为空的条目会被拒绝构建）。
- 或者从一套已经跑起来的隔离副本整体拷 `runtime/`（同版本、同平台）。
- 全新便携副本可直接用 `ops/deploy/provision-smoke-runner.ps1 -Apply` 生成 `.smoke\`，
  它会处理运行时与构建产物的复制。

## 禁止事项

- **不要**把 `pgdata/`、`portal-auth-data/`、数据库文件、`.env`、私钥、session
  提交进版本库；`ops/quality/check_all.ps1` 第 3 步会用 `git ls-files` 检查
  被跟踪文件里有没有这些东西。
- **不要**把 `pgdata` 当备份：PG 数据文件跨版本/跨机器不保证可移植。
- `pgdata` 里可能包含生产凭据（三张本地权威表，见
  `docs/architecture/data-source-registry.md`）：隔离副本上调度器保持关闭，
  不跑采集，不发信，不登录 WhatsApp。
