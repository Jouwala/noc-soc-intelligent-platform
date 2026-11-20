# ZIP 构建与发布（Phase 1）

> 依据：Phase 0 报告（`docs/workspace/zip-delivery-phase0-review.md`）§6.1–§6.6。
> 任务：Issue #146。构建脚本：`ops/deploy/build-zip.mjs`。

## 产物

| 产物 | 内容 | 发布节奏 |
| --- | --- | --- |
| 应用 ZIP `noc-soc-platform-<版本>-<平台>.zip` | **运行必需**源码（后端 / 门户 server / bot / eFlow 知识库）+ 预构建前端 + 预装 `node_modules` + `wheels/` + 启停与安装脚本 + 《安装与运行说明.md》 + `ISOLATED_COPY.flag` + `build-info.json` + `SHA256SUMS` | 每次发版（SemVer 标签） |
| 运行时 ZIP `noc-soc-runtime-<运行时版本>-<平台>.zip` | Python / Node / PostgreSQL / nginx 发行包 | 运行时版本变化时（手动） |

平台后缀：`win-x64`、`linux-x64`。`node_modules` 必须在对应平台的 runner 上 `npm ci`，不得跨平台复用。

### ⭐ 最小化原则（2026-11-19，用户定）

判据只有一条：**目标机解压后，为了把平台跑起来并登录使用，这个文件是否必须存在？**
必须 = 进包；否则一律不进。具体：

- **不进包**：`.github/`、`.claude/`、`.githooks/`、`.agent-work/`、`.workbuddy-ai/`、
  `.gitignore`、`.gitattributes`、`README.md`、`AGENTS.md`（仓库自身信息）、
  `docs/`、开发手册、`tests/`、代码生成器、冒烟工具、`agent-worker` / `control-plane`、
  浏览器扩展、前端源码（`frontend/src`、`portal/src`）、生产看门狗脚本；
- **不进包（运行期缓存，此前真实混入过）**：`backend/services/cn_ems/_pmcache`
  （27,072 文件 / 618.5 MB，曾把 source 模式从约 5 MB 撑到 84 MB）、
  `backend/data/native_board_cache`、`backend/data/tt_timeline_cache`、`backend/data/gp_ran`；
- 由 `zip-manifest.mjs` 的 `PACKAGE_FORBIDDEN_PATHS` 机器守卫，构建时命中即失败
  （`assertPackageMinimal`）；`release-build.yml` 另在解包后复核一遍（第二道闸）。

同时，GitHub 自动生成的 `Source code (zip)` 用 `.gitattributes` 的 `export-ignore`
裁掉同一批开发资产 —— 那个归档不受 `build-zip` 白名单控制，是用户下载到
`.github/` / `.claude/` / `.githooks/` 的来源。

不打包的内容（构建脚本断言，命中即失败）：`pgdata`、`.env`、`*.key`/`*.pem`、
会话/鉴权/凭据文件、`auth_info.json`、数据库与快照、日志、缓存、`.git`、手工快照。
另有凭据扫描（私钥块、JSESSIONID 会话值、密钥/口令赋值），只报位置不打印内容。

## 本地构建

```bash
# 仅源码模式（不装依赖，验证组装/断言/扫描逻辑）
node ops/deploy/build-zip.mjs --mode source --platform linux-x64

# 完整模式（须先 npm ci × 4、npm run build × 2、pip download wheels）
node ops/deploy/build-zip.mjs --mode full --platform win-x64 --version v1.2.3

# 干跑（组装 + 断言 + 扫描，不生成 ZIP）
node ops/deploy/build-zip.mjs --mode source --dry-run
```

产物默认落在仓库内 `TMP/zip-dist/`（已忽略，不入库）。`--staging` 只能在仓库内或系统临时目录内。

## 回归测试

```bash
node --test ops/deploy/lib/zip-package.test.mjs
```

覆盖：Windows 路径守卫（含 `path.win32` 模拟）、拒绝规则（嵌套源码目录保留、应用根级数据目录拦住）、嵌套 `node_modules`/`dist` 保留、符号链接策略、门禁植入（凭据/`.env`/`pgdata` 均被拦截）、真实仓库组装含回归 8 文件、ZIP 写出确定性。`release-build.yml` 在构建前跑本文件。

## CI 流程（release-build.yml）

1. 标签推送（`v*`）或手动 dispatch 触发。
2. 质量门：等待 `quality.yml` 在同一提交上结论为 success（不复制其检查，避免第二个真相源）。
3. 双平台矩阵构建：`npm ci`（4 个项目）→ 前端构建 → `pip download`（lock 文件）→ 构建脚本 → ZIP 校验（可解压 + `build-info.json` 合法）。
4. 标签触发且非干跑时发布到 Release，附 `.sha256` 与校验命令。

`built_at` 的语义：`build-info.json` 里的 `built_at` 取源码提交时间（即统一的 ZIP 时间戳），而非墙上时钟——否则两次构建字节不同，“可重复构建”不成立。

## 运行时 ZIP（runtime-build.yml）

仅手动 dispatch。清单 `ops/deploy/runtime-manifest.json` 驱动下载与 SHA256 校验；**清单里 `url`/`sha256` 为空的条目会直接失败**，禁止无哈希构建。当前状态：

- Node.js：URL 与哈希已从 nodejs.org 的 SHASUMS256.txt 核实。
- Python（Windows）：哈希已核实——下载字节的 MD5 与 python.org 发布页公布的 MD5 一致，SHA256 由同一下载字节算得。
- PostgreSQL / nginx：哈希或 URL 待从官方渠道核实后补齐，补齐前运行时构建跑不通（故意的）。
- Linux 侧 Python 与 nginx 的来源待定（系统包 vs 捆绑发行包），见 Phase 0 附录 B 第 14 项。
- 地图瓦片 `liberia.pmtiles` 不在 v1 内（来源未核实）。

工具假定：`runtime-build.yml` 用 Git Bash 跑，要求 runner 提供 `tar`、`jq`、`sha256sum`、`curl`（GitHub 托管的 windows/ubuntu runner 均预装；工作流有预检步骤，自托管 runner 缺工具会明确失败）。Windows 上 `tar -xf` 解 `.zip` 靠系统自带 bsdtar。

布局差异（P1-6 收口）：运行时 ZIP 解压到 `<NOC_HOME>/runtime/`，内含 `nginx-1.28.0/`；现行 `start_all.ps1` 期望 nginx 在版本根目录。启动脚本改造时统一。

## 校验产物

```powershell
# Windows
Get-FileHash -Algorithm SHA256 noc-soc-platform-v1.2.3-win-x64.zip
```

```bash
# Linux
sha256sum -c noc-soc-platform-v1.2.3-linux-x64.zip.sha256
unzip -l noc-soc-platform-v1.2.3-linux-x64.zip | head
```

## 常见失败

| 现象 | 原因与处理 |
| --- | --- |
| 退出码 2，排除断言失败 | 暂存区混入禁入文件（见报错列出的路径）；从白名单或源头去掉，不要放宽规则。 |
| 退出码 2，凭据扫描命中 | 按报错位置人工复核；真泄露则清掉并轮换，误报则收窄规则（改规则须评审）。 |
| 退出码 2，full 模式缺件 | 工作流漏了某步安装/构建；按报错列出的路径补。 |
| 质量门超时 | `quality.yml` 在该提交上未成功或未触发；先修 quality。 |
| 运行时构建清单门禁失败 | 清单有空哈希/空 URL；从官方渠道核实后补齐，禁止绕过。 |
