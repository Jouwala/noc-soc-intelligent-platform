# MS-Intelligent-O-M-Platform 副本仓库重构执行方案

> 目标仓库：`Jouwala/MS-Intelligent-O-M-Platform`
>
> 适用范围：只在 MS 副本仓库实施；RPA 主仓库作为只读参考和必要时的同步源。
>
> 总原则：先建立可回滚基线，再建立质量门禁，随后按边界整理，最后处理数据层和业务代码重构。不进行一次性“大搬家”。

---

## 1. 当前基线与重构目标

### 1.1 当前基线

MS 副本当前更接近“便携部署版平台”，包含以下主要部分：

- `Task/my_app/`：FastAPI 后端、React 前端、采集任务、告警、KPI、工单和数据服务。
- `liberia-noc_soc-数据看板统一门户/`：React/Vite 门户及 Express 服务。
- `Task/whatsapp-kb-bot/`：WhatsApp 知识库机器人和 RAG 相关脚本。
- `scripts/`、`my_portal/`、根目录 BAT/PowerShell：便携部署、同步和服务运维。
- 大量历史文档、规则、测试、一次性脚本以及业务资源。

已知问题：

1. 仓库边界偏大，平台代码、运维脚本、交付资料和历史材料混在一起。
2. Python、Node、前端、外部系统和 Windows 部署逻辑耦合较多。
3. 数据源存在 SQLite、多库镜像、远程 HTTP 数据库和 PostgreSQL 规划并存的情况。
4. 当前至少有一个明确的 Python 语法错误：`Task/my_app/email_reports/send_via_browser.py`。
5. 测试文件不少，但需要区分 unit、integration、e2e 和 live 测试。
6. CI 质量门禁不足，副本仓库当前缺少完整的自动检查流程。
7. 历史文件、Cookie、Token、抓包样本等安全风险需要单独处理。
8. 副本有隔离部署逻辑，重构时不能破坏隔离端口、路径和“禁止影响生产”的保护。

### 1.2 重构后的目标

重构完成后，MS 仓库应达到以下状态：

- 一条命令可以完成静态检查、类型检查、核心测试和构建验证。
- 平台核心代码、部署脚本、外部连接器、测试和文档边界清楚。
- 所有环境相关配置集中管理，业务代码不再散落硬编码生产路径。
- 数据库和数据源有清晰的“谁写、谁读、真源、缓存、失败策略”说明。
- 生产/副本启动和停止有明确安全保护，不能误操作生产实例。
- 修改可以小步提交、独立回滚、独立验证。
- 副本可以独立部署，同时保留从 RPA 主仓库同步的明确流程。

---

## 2. 重构边界与禁止事项

### 2.1 本次纳入范围

- MS 副本仓库目录结构和文档结构。
- `my_app` 的质量门禁、配置层、数据访问层和测试分层。
- 门户、WhatsApp Bot、部署脚本的边界整理。
- CI、secret scanning、构建和发布检查。
- 主仓库到副本仓库的同步规则文档化。

### 2.2 本次暂不做

以下事项不要在第一轮重构中做：

- 不一次性把 SQLite 全部迁移到 PostgreSQL。
- 不同时更换 FastAPI、React、Vite、Node 或 Python 主版本。
- 不改生产端口和生产部署拓扑。
- 不在副本上直接反向同步回 RPA 主仓库。
- 不把所有业务服务强行改成微服务。
- 不批量自动删除“看起来没用”的脚本、文档和资源。
- 不在没有测试和回滚点的情况下重写告警、工单和同步核心链路。

### 2.3 工作方式

每个阶段都必须遵守：

1. 新建独立分支。
2. 改动尽量按单一主题提交。
3. 每个主题完成后运行对应验证。
4. 通过后打 Git tag 或建立明确的回滚提交点。
5. 未通过时只回滚当前主题，不回滚整个仓库。

建议分支：

```text
main                         稳定可部署版本
refactor/baseline             基线与审计
refactor/quality-gates        质量门禁与 CI
refactor/config-boundary      配置边界
refactor/data-access          数据访问层
refactor/module-boundaries    目录和模块整理
refactor/security-hardening   安全加固
```

---

## 3. 总体阶段和建议顺序

| 阶段 | 内容 | 风险 | 建议结果 |
|---|---|---:|---|
| 0 | 保护现场、备份、建立基线 | 低 | 可随时回滚 |
| 1 | 解决语法错误和可运行性问题 | 低 | 核心代码能检查 |
| 2 | 建立测试、静态检查和 CI 门禁 | 低 | 后续改动有保护网 |
| 3 | 安全清理和配置收口 | 中 | 减少凭据和环境风险 |
| 4 | 明确子系统边界和目录职责 | 中 | 降低认知复杂度 |
| 5 | 数据源与数据访问层治理 | 高 | 降低数据错读和迁移风险 |
| 6 | 核心业务服务渐进重构 | 高 | 提升可维护性 |
| 7 | 部署、同步和发布流程收口 | 中 | 副本可稳定交付 |
| 8 | 验收、文档更新和长期维护 | 低 | 形成可持续机制 |

建议不要跳过第 0、1、2 阶段。没有质量门禁直接进行目录搬迁和数据层改造，风险最高。

---

# 4. 阶段 0：保护现场并建立基线

## 4.1 创建本地备份和基线分支

在副本仓库目录执行：

```powershell
cd D:\01_olbr_portal

git status --short
git branch --show-current
git fetch origin

git checkout -b refactor/baseline

git tag refactor-baseline-2026-11-12

git push origin refactor/baseline
git push origin refactor-baseline-2026-11-12
```

如果工作区有未提交修改，先不要覆盖。先导出清单：

```powershell
git status --short > .\TMP\pre-refactor-status.txt
git diff > .\TMP\pre-refactor-working.diff
```

不要把 `TMP` 下的备份文件提交到 Git。

## 4.2 记录运行基线

至少记录以下信息：

- 当前副本版本号。
- 当前 Python、Node、npm 版本。
- 门户、my_app、WhatsApp Bot 的启动命令。
- 当前副本端口。
- 当前能正常打开的页面。
- 当前核心 API 的健康检查结果。
- 当前测试命令和失败项。
- 当前同步脚本的 dry-run 结果。

建立文件：

```text
ops/baseline/2026-11-12-runtime-baseline.md
```

内容至少包括：

```text
my_app backend: 7039
my_app frontend: 7091
portal: 9277
whatsapp bot: 9279
postgresql: 55432
nginx: 8443
```

实际端口以仓库现有配置为准，不要机械照抄。

## 4.3 生成文件清单

```powershell
git ls-files > .\TMP\tracked-files.txt
git count-objects -v > .\TMP\git-count.txt
```

把文件按下面几类标记：

- 生产核心代码
- 部署和运维代码
- 测试
- 文档
- 外部系统规则/模板
- 生成物或快照
- 疑似历史遗留
- 疑似敏感数据

第一阶段只分类，不删除。

### 阶段 0 验收

- [ ] 有可回滚 tag。
- [ ] 有当前工作区状态记录。
- [ ] 有启动端口和服务基线。
- [ ] 没有把临时审计文件提交进仓库。
- [ ] RPA 主仓库没有被修改。

---

# 5. 阶段 1：先修复可运行性问题

## 5.1 修复 Python 语法错误

优先处理：

```text
Task/my_app/email_reports/send_via_browser.py
```

错误原因是 Python f-string 中包含 JavaScript 花括号。修复时优先采用以下写法之一：

### 方案 A：普通模板字符串 + `.replace`

```python
script = """
editor.innerHTML = `__BODY__`;
""".replace("__BODY__", body)
```

### 方案 B：f-string 中转义 JavaScript 花括号

```python
script = f"""
function render() {{
    editor.innerHTML = `{body}`;
}}
"""
```

对包含 HTML、JavaScript、CSS 的大段字符串，优先使用方案 A，减少花括号转义错误。

## 5.2 对中文路径和审计脚本做路径安全处理

所有脚本应使用：

- Python `pathlib.Path`
- `subprocess` 的参数列表，不拼接 shell 字符串
- Git `-z` 输出或 Python Git 库处理路径
- UTF-8 编码

不要依赖 Git 输出经过 shell 转义后的路径文本。

## 5.3 进行基础语法检查

```powershell
python -m compileall -q Task scripts
```

如果项目环境中已有 ruff：

```powershell
python -m ruff check Task/my_app/backend --select E9,F821
```

目标：

- `SyntaxError` 为 0。
- `F821` 未定义名为 0。
- 只允许明确登记的历史债务继续存在。

### 阶段 1 验收

- [ ] Python 语法检查通过。
- [ ] `send_via_browser.py` 可以被编译。
- [ ] 中文路径不会被审计脚本误判。
- [ ] 修复内容有单独提交：`fix: restore python syntax validity`。

---

# 6. 阶段 2：建立质量门禁和 CI

这是后续所有重构的保护网，优先级很高。

## 6.1 统一根目录检查入口

新增：

```text
ops/quality/
  check_python.ps1
  check_frontend.ps1
  check_portal.ps1
  check_security.ps1
  check_all.ps1
```

根目录 `package.json` 或 `Makefile` 不是必须，但建议提供统一命令。Windows 环境可以先使用 PowerShell：

```powershell
.\ops\quality\check_all.ps1
```

建议检查顺序：

1. 路径和敏感文件检查。
2. Python 编译检查。
3. Ruff 必要规则。
4. my_app pytest。
5. my_app TypeScript 检查。
6. 门户 TypeScript 检查。
7. 门户测试和构建。
8. WhatsApp Bot 离线测试。

## 6.2 Python 检查规则

第一轮只阻断高价值问题：

```text
E9     语法和运行时错误
F821   未定义名称
B      常见 bug 模式，评估后逐步启用
```

以下存量问题先告警，不要一次性阻断全部历史代码：

```text
F401  未使用导入
F841  未使用变量
T20   print
S110  吞异常
```

逐步将 `services/` 和 `routers/` 中的 `print` 改为标准 logger；脚本和 worker 可以保留 stdout，但要有明确白名单。

## 6.3 测试分层

在 `Task/my_app/tests/` 中明确目录：

```text
tests/
  unit/
  integration/
  api/
  migrations/
  fixtures/
  live/
```

迁移规则：

- 纯函数测试放 `unit/`。
- 数据库测试放 `integration/`。
- FastAPI 路由测试放 `api/`。
- 会真实访问 UME、eFlow、邮箱、WhatsApp 的测试放 `live/`。
- `live/` 默认不进入普通 CI。

## 6.4 GitHub Actions

新增 `.github/workflows/quality.yml`，初期做以下检查：

```text
job: python
  - Python 3.12
  - pip install -r Task/my_app/requirements.txt
  - python -m compileall -q Task scripts
  - ruff check backend --select E9,F821
  - pytest tests/unit tests/api

job: portal
  - npm ci
  - npm run lint
  - npm test
  - npm run build

job: security
  - secret scanning
  - 检查 .env、session、cookie、私钥、抓包文件
```

暂时不要在 GitHub Actions 中访问真实内网地址。

### 阶段 2 验收

- [ ] 本地一条命令能完成主要检查。
- [ ] PR 能自动执行 Python、前端、门户和安全检查。
- [ ] live 测试不会默认连接生产系统。
- [ ] CI 失败能指出具体项目和命令。
- [ ] 在 CI 通过前禁止合并重构分支。

---

# 7. 阶段 3：安全清理和配置收口

## 7.1 全历史扫描

在本地使用经过审计的工具，例如 Gitleaks 或 GitHub Secret Scanning，对完整历史扫描：

```powershell
gitleaks detect --source . --log-opts="--all" --redact
```

不要把扫描结果中的真实 Token、Cookie、密码复制到聊天、Issue 或文档中。

## 7.2 立即轮换历史凭据

只要某个 Token、Cookie、密码、私钥或 session 曾经进入公开仓库历史，都应视为已泄露，执行：

1. 让对应系统废止旧凭据。
2. 创建新凭据。
3. 只通过环境变量或本机未跟踪文件注入。
4. 验证旧凭据确实不能继续使用。
5. 再处理 Git 历史清理。

代码清理不能替代凭据轮换。

## 7.3 建立配置目录

新增示例结构：

```text
config/
  README.md
  env.example
  env.dev.example
  env.copy.example
  ports.example.json
```

运行时真正配置仍放在仓库外或 `.env` 中。业务代码只读取配置对象，不直接到处读取环境变量。

建议逐步集中以下配置：

- 服务端口
- 数据库 URL
- 数据目录
- 内网主机
- 外部系统地址
- 邮件和 WhatsApp 配置
- 浏览器和 Python/Node 路径
- 调度开关
- 副本隔离标志

## 7.4 配置优先级

明确并固定：

```text
命令行参数 > .env.copy > .env > 安全默认值
```

生产地址不能通过普通默认值自动启用。副本启动时必须显式确认：

- 当前目录是 MS 副本。
- 使用的是副本端口。
- 不接管真实 WhatsApp session。
- 不连接生产数据库进行写入。

### 阶段 3 验收

- [ ] 历史敏感信息已经登记和处理。
- [ ] 旧凭据已轮换。
- [ ] 业务代码不再新增硬编码凭据。
- [ ] 配置示例不含真实值。
- [ ] 副本启动不会默认接管生产服务。

---

# 8. 阶段 4：目录边界整理

这一阶段只做“搬家和边界说明”，不要同时改业务逻辑。

## 8.1 建议的目标目录

可以先在当前仓库中采用如下逻辑结构：

```text
apps/
  my_app/
    backend/          # 可逐步从 Task/my_app/backend 迁入
    frontend/
    tests/
    migrations/
  portal/
    src/
    server/
    public/
    tests/
  whatsapp-bot/
    src/
    scripts/
    tests/

packages/
  config/
  data-access/
  logging/
  shared-types/

integrations/
  eflow/
  ume/
  email/
  whatsapp/
  remote-db/

ops/
  deploy/
  healthchecks/
  quality/
  sync/
  windows/

docs/
  architecture/
  operations/
  security/
  adr/

archive/
  reports/
  one-off-tools/
```

不要一开始就把所有文件物理搬迁到这个结构。推荐分两步：

### 第一步：保留旧路径，增加边界说明

在每个一级目录增加 `README.md`，说明：

- 目录所有者。
- 可被哪些系统依赖。
- 启动方式。
- 测试方式。
- 哪些文件禁止直接修改。

### 第二步：按子系统逐批迁移

优先迁移：

1. `Task/my_app` 内部已经明确属于核心平台的目录。
2. 独立的部署脚本。
3. 一次性报告和历史材料。

每批迁移后必须同步修改：

- import 路径。
- 启动脚本。
- CI 路径。
- 文档路径。
- 同步白名单。
- `.gitignore`。

## 8.2 历史材料处理

不要直接删除。按以下方式处理：

- 仍会用于部署的：保留在当前路径。
- 仍有业务参考价值的：移动到 `docs/`。
- 仅保留历史记录的：移动到 `archive/`。
- 已确认无用且不含审计价值的：先建立删除清单，再单独提交删除。

每次删除都要有：

```text
文件路径
删除理由
替代文档或代码
最后一次使用时间
负责人确认
```

### 阶段 4 验收

- [ ] 每个一级目录有职责说明。
- [ ] 核心应用、部署运维、外部连接器和历史资料边界清楚。
- [ ] 迁移后启动命令和测试路径仍然有效。
- [ ] 没有“为了整齐”而删除未确认文件。

---

# 9. 阶段 5：数据源和数据访问层治理

这是风险最高的技术阶段，建议在质量门禁和配置收口完成后再做。

## 9.1 建立数据源登记表

新增：

```text
docs/architecture/data-source-registry.md
```

每个数据源至少记录：

| 字段 | 示例 |
|---|---|
| 数据源名称 | `eflow_tt` |
| 实际位置 | `app.db` / 远端 API / PostgreSQL |
| 写入方 | 某同步任务 |
| 读取方 | 某服务/路由 |
| 是否真源 | 是/否 |
| 更新周期 | 5 分钟/1 小时 |
| 允许延迟 | 10 分钟 |
| 缓存策略 | 本地镜像/不缓存 |
| 失败策略 | 报错/降级/返回快照 |
| 迁移计划 | 保留/合并/废弃 |

## 9.2 建立统一连接入口

目标是逐步减少业务代码中的直接连接：

```text
backend/data_access/
  connection_factory.py
  sqlite.py
  postgres.py
  remote_reader.py
  transaction.py
  errors.py
```

第一轮不要强行重写所有调用点。先做到：

1. 统计所有裸 `sqlite3.connect`。
2. 给每个调用点标注业务用途。
3. 区分允许的本地脚本和必须迁移的服务代码。
4. 为关键服务补统一连接入口。
5. 每迁移一个模块就运行其对应测试。

## 9.3 处理多库和镜像关系

先解决“定义问题”，再解决“迁移问题”：

- 明确 `eflow_tt` 哪一份是主数据。
- 明确告警表哪一份是页面读取源。
- 明确远端数据库不可用时的降级行为。
- 明确同步任务失败时是否允许继续使用旧缓存。
- 为关键数据源增加 freshness 检查。

不要在第一轮直接删除旧库或旧表。先增加监控：

```text
数据源名称
最后成功同步时间
最后成功记录数
最近一次异常
当前是否过期
```

## 9.4 数据访问层迁移顺序

建议顺序：

1. 只读查询服务。
2. 门户 QC 查询。
3. KPI 和报表服务。
4. 工单和告警服务。
5. 同步写入任务。
6. 数据迁移和清理任务。

写入任务最后迁移，因为最容易造成数据损坏。

### 阶段 5 验收

- [ ] 关键数据源都有登记表。
- [ ] 关键页面知道自己读哪一个数据源。
- [ ] 关键同步任务有 freshness 检查。
- [ ] 重要服务不再新增裸数据库连接。
- [ ] 数据访问迁移有单测和回滚方案。

---

# 10. 阶段 6：核心业务代码渐进重构

## 10.1 统一日志

建立统一日志规范：

```text
时间
服务名
任务名
trace/request id
数据源
操作结果
耗时
异常类型
```

逐步替换核心服务中的 `print`：

- `routers/`：必须使用 logger。
- `services/`：必须使用 logger。
- worker：允许 stdout，但同时写结构化日志。
- 一次性脚本：可以保留 print。

## 10.2 统一错误处理

避免以下模式：

```python
except Exception:
    pass
```

替换为：

```python
except SpecificError as exc:
    logger.warning("operation failed", extra={"item_id": item_id}, exc_info=exc)
    return fallback
```

对确实需要吞掉的异常，必须说明：

- 为什么可以忽略。
- 是否有监控。
- 是否有降级结果。
- 是否会影响数据完整性。

## 10.3 统一外部系统客户端

建议每类外部系统都采用一致结构：

```text
integrations/eflow/
  client.py
  models.py
  errors.py
  retry.py
  health.py
  tests/
```

客户端只负责：

- 认证。
- 请求。
- 超时。
- 重试。
- 响应解析。
- 外部错误转换。

业务服务负责：

- 业务规则。
- 数据库写入。
- 调度。
- 告警和通知。

## 10.4 任务调度治理

所有定时任务必须具备：

- 唯一任务名。
- 所属 worker 分组。
- 运行周期。
- 超时。
- 重试次数。
- 幂等策略。
- 最近成功时间。
- 数据 freshness 影响。
- 失败通知策略。

新增任务必须同时更新：

- 任务注册表。
- 任务分组文档。
- 运行巡检文档。
- 测试或 dry-run。

### 阶段 6 验收

- [ ] 核心服务日志可检索。
- [ ] 关键异常不会静默吞掉。
- [ ] 外部系统调用有超时和重试边界。
- [ ] 任务注册、监控、文档保持一致。
- [ ] 核心告警和同步服务有幂等测试。

---

# 11. 阶段 7：部署、同步和发布流程收口

## 11.1 副本启动前强制检查

`start_all.ps1` 或等价入口在启动前必须检查：

- 当前目录存在副本隔离标志。
- 当前端口不是生产端口。
- 当前数据目录属于副本目录。
- 当前 `.env` 是副本配置。
- `START_WA_BOT=0` 或明确经过人工确认。
- 不会杀掉生产端口进程。

任何一项不满足都应该停止，而不是自动猜测。

## 11.2 健康检查

为每个服务提供：

```text
healthcheck
readiness
version
config-summary（不显示密钥）
```

健康检查至少返回：

- 服务是否启动。
- 版本号。
- 数据库是否可读。
- 关键外部系统是否配置。
- 最近一次同步状态。

## 11.3 同步策略

明确以下规则：

```text
RPA 主仓库 = 开发源
MS 仓库 = 便携部署副本
副本本地修复 = 先记录，再评估是否回传主仓库
生产运行数据 = 永不反向同步进 Git
凭据、session、数据库、日志、构建物 = 永不同步进 Git
```

副本同步前后都执行：

```text
同步前 dry-run
冲突检查
源码同步
依赖变化检测
前端构建
核心测试
健康检查
```

### 阶段 7 验收

- [ ] 副本不能误停生产服务。
- [ ] 启动前能检测端口和目录归属。
- [ ] 同步流程支持 dry-run 和回滚。
- [ ] 同步后能自动发现依赖和迁移变化。
- [ ] 发布记录包含版本、变更、测试和回滚方式。

---

# 12. 阶段 8：最终验收和长期维护

## 12.1 发布前验收清单

### 代码

- [ ] Python 编译通过。
- [ ] Ruff 必要规则通过。
- [ ] TypeScript 检查通过。
- [ ] 门户构建通过。
- [ ] 核心单元测试通过。
- [ ] 关键 API 测试通过。
- [ ] 不依赖真实生产系统的 CI 全绿。

### 数据

- [ ] 关键数据源登记完整。
- [ ] 数据库读写边界明确。
- [ ] 关键同步有 freshness 检查。
- [ ] 回滚不会破坏现有数据库。

### 安全

- [ ] secret scanning 通过。
- [ ] 历史凭据已轮换。
- [ ] `.env`、session、cookie、抓包数据没有进入提交。
- [ ] 日志和错误响应不泄漏凭据。

### 部署

- [ ] 副本端口正确。
- [ ] 副本隔离标志存在。
- [ ] 启停脚本 dry-run 通过。
- [ ] 健康检查通过。
- [ ] 生产服务未被影响。

### 文档

- [ ] README 与实际启动流程一致。
- [ ] 部署说明与端口一致。
- [ ] 架构图与实际链路一致。
- [ ] ADR 记录关键决策。
- [ ] 回滚说明可由其他人执行。

## 12.2 长期维护节奏

每次 PR：

- 必须通过 CI。
- 只处理一个主要主题。
- 不把临时调试文件提交进去。
- 需要更新相关文档。

每周：

- 检查失败任务。
- 检查数据 freshness。
- 检查依赖和安全扫描。
- 检查副本与主仓库是否有未登记分叉。

每月：

- 清理已确认无用的历史材料。
- 复盘慢查询、失败任务和重复代码。
- 更新架构决策记录。
- 验证恢复和回滚流程。

---

# 13. 第一批建议立即执行的任务

不要一开始就做大规模目录移动。建议第一批只做以下 10 件事：

1. 建立 `refactor/baseline` 分支和基线 tag。
2. 记录副本当前端口、启动命令和健康状态。
3. 修复 `send_via_browser.py` 的 f-string 语法错误。
4. 增加 Python 编译检查脚本。
5. 增加门户 TypeScript、构建和测试检查脚本。
6. 增加统一 `check_all.ps1`。
7. 增加 GitHub Actions 基础 CI。
8. 增加 Gitleaks/secret scanning。
9. 建立 `docs/architecture/data-source-registry.md`。
10. 建立 `docs/adr/`，记录副本隔离、数据源和同步规则。

第一批完成并稳定后，再进入配置收口和目录边界整理。

---

# 14. 推荐的提交粒度

不要使用一个巨大的“重构完成”提交。建议使用以下提交序列：

```text
chore: add refactor baseline and runtime inventory
fix: restore python syntax validity
chore: add unified local quality checks
ci: add python frontend portal and security gates
security: remove tracked sensitive artifacts and harden ignores
refactor: centralize copy environment configuration
chore: document application and ops boundaries
refactor: introduce data source registry
refactor: migrate read-only access to data access factory
refactor: normalize integration client errors and retries
refactor: harden copy deployment safety checks
docs: update architecture deployment and rollback guides
```

每个提交都应该满足：

- 可以单独理解。
- 可以单独回滚。
- 有对应检查结果。
- 不混入无关格式化。

---

# 15. 最终完成标准

只有满足以下条件，才可以认为 MS 副本完成第一轮重构：

1. 副本能够独立启动，并且不会触碰生产实例。
2. 核心 Python、前端、门户和测试检查可以自动执行。
3. CI 能阻止明显语法错误、类型错误、构建错误和敏感信息进入仓库。
4. 核心代码、部署代码、外部连接器和历史材料边界清楚。
5. 关键数据源和数据库读写关系有文档和监控。
6. 重要外部系统调用具有超时、重试、错误转换和可观测性。
7. 每项重构均有测试和回滚点。
8. README、部署文档、架构文档和实际运行方式一致。
9. 主仓库与副本之间的同步责任清晰，没有未经登记的双向漂移。

**推荐第一轮目标不是“把所有代码改漂亮”，而是把仓库变成一个可以安全持续改进的系统。**
