# runtime — 捆绑运行时（不入库，本文件除外）

`runtime/` 整个目录被根 `.gitignore` 排除（6.5 GB 的第三方发行包与运行态数据），
**只有本文件是例外**（`.gitignore` 里 `!/runtime/README.md`），用来说明这里
应该有什么、从哪里补。

| 目录 | 内容 | 来源 |
| --- | --- | --- |
| `runtime/python312/` | Python 3.12.10 + fastapi/uvicorn/psycopg2/sqlalchemy/alembic/apscheduler | 官方安装包 |
| `runtime/node/` | Node v24.16.0 | 官方安装包 |
| `runtime/postgresql16/` | PostgreSQL 16 服务端二进制 | 官方安装包 |
| `runtime/pgdata/` | 便携 PostgreSQL 的数据目录（initdb 出来的库文件，约 4.9 GB） | 首次启动时 `start_all.ps1` 自动 initdb 建库 |
| `runtime/pgdata.off-20261112/` | 02 隔离冒烟用的已有 PG 数据目录，只听 `127.0.0.1:55432` | 02 本机已有，见方案第 8.2 节 |
| `runtime/portal-auth-data/` | 门户鉴权数据（含加盐密码哈希） | 运行时写入，严禁入库 |

## 补齐方式

跑主仓库的打包脚本（`-Stage runtimes`），或按 `ops/deploy/start_all.ps1`
的报错提示逐个装。拿到别人的副本时，先补齐这里再启动。

## 禁止事项

- **不要**把 `pgdata/`、`portal-auth-data/`、数据库文件、`.env`、私钥、session
  提交进版本库；`ops/quality/check_all.ps1` 第 3 步会用 `git ls-files` 检查
  被跟踪文件里有没有这些东西。
- **不要**把 `pgdata` 当备份：PG 数据文件跨版本/跨机器不保证可移植。
- `pgdata` 里可能包含生产凭据（三张本地权威表，见
  `docs/architecture/data-source-registry.md`）：只放在 02 本机数据库，
  调度器保持关闭，不跑采集，不发信，不登录 WhatsApp。
