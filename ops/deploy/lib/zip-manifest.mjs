/**
 * ZIP 打包清单（纯数据）：白名单、拒绝规则、回归文件。
 *
 * 逻辑在 zip-package.mjs。改动这里 = 改动交付内容，须经评审。
 *
 * P0-1 教训（PR #151 评审）：拒绝规则不得按目录名一律生效——
 * apps/my_app/backend/services/db/、apps/my_app/backend/data/gis/、
 * apps/portal/src/data/ 是源码/运行时数据，曾被 db/data 目录名规则静默丢掉。
 * 数据目录只按完整相对路径拦（PATH_DENIED_DIRS，应用根级）；目录名规则
 * （BASENAME_DENIED_DIRS）只保留无歧义的缓存/垃圾/运行态名。
 *
 * ⭐ 2026-11-19 最小化原则（用户明确要求）：**非必要不进版本包**。
 * 判据只有一条 —— 「目标机解压后，为了把平台跑起来并登录使用，这个文件是否
 * 必须存在？」必须 = 进包；否则一律不进。因此下面这些一律排除：
 *   · 开发/CI/agent 元数据：.github、.claude、.githooks、.agent-work、
 *     .workbuddy-ai、AGENTS.md 所指的规则目录；
 *   · 测试、手册、构建脚本、代码生成器、冒烟工具（目标机不写代码）；
 *   · 运行期缓存/日志/数据库/快照（含 619MB 的 cn_ems/_pmcache，曾把
 *     source 模式从 5MB 撑到 84MB —— 见 test 的「最小化」用例）；
 *   · 与平台运行无关的旁路系统（agent-worker / control-plane / 浏览器扩展）。
 * 判断拿不准时宁可**不进**，在《安装与运行说明》里写清如何按需补装。
 */

export const APP_NAME = 'noc-soc-intelligent-platform'

// 运行时版本固定真相源：runtime/README.md 与 Phase 0 报告 §1.2。
export const RUNTIME_VERSIONS = { python: '3.12.10', node: '24.16.0', postgresql: '16', nginx: '1.28.0' }

// 目录：递归复制（受拒绝规则过滤）。requiredIn: full 仅在 full 模式要求存在。
//
// 只列「运行必需」的目录（最小化原则，见文件头）。被刻意排除、且**不是 bug**
// 的目录：apps/my_app/{tests,docs,tools,monitor_fixes,nginx}、apps/portal/docs、
// apps/whatsapp-kb-bot/{docs,evals}、apps/agent-worker*、apps/control-plane、
// extensions、docs、ops/smoke、desktop_shortcuts。
// 前端源码（apps/my_app/frontend/src、apps/portal/src）**进包**（#192）：目标机
// 只跑预构建产物时不需要，但别人下载自包含运行（入其他国家）要在目标机重新构建
// 前端；门户 prestart（npm run build）也需要 src。两树合计约 7.5 MiB。
// vite preview 需要的是 frontend/package.json + vite.config.ts + vite 本身。
export const INCLUDE_DIRS = [
  { src: 'apps/my_app/backend', requiredIn: 'any' },
  { src: 'apps/my_app/alembic', requiredIn: 'any' },
  // 邮件报表模板与 owa_mail_report.py 由任务在运行时调用（见 services/mtn_mou_chart_service.py）。
  { src: 'apps/my_app/email_reports', requiredIn: 'any' },
  { src: 'apps/portal/server', requiredIn: 'any' },
  { src: 'apps/portal/assets', requiredIn: 'any' },
  // 手写静态壳：nginx.conf.template 直接 alias 发它们，不是构建产物。
  { src: 'apps/portal/MsPortal', requiredIn: 'any' },
  { src: 'apps/portal/OlrPortal', requiredIn: 'any' },
  { src: 'apps/portal/olbrportal', requiredIn: 'any' },
  { src: 'apps/portal/v1', requiredIn: 'any' },
  { src: 'apps/portal/v2', requiredIn: 'any' },
  { src: 'apps/portal/v3', requiredIn: 'any' },
  // 问答助手：bot 的 src + 知识库 + 运行时用到的 Python 工具（tt-analyze 等）。
  { src: 'apps/whatsapp-kb-bot/src', requiredIn: 'any' },
  { src: 'apps/whatsapp-kb-bot/knowledge', requiredIn: 'any' },
  { src: 'apps/whatsapp-kb-bot/scripts', requiredIn: 'any' },
  // eFlow 知识库：my_app 的 EflowQa 页与 TT 分析直接读它（routers/eflow_qa.py）。
  { src: 'apps/eflow_Q&A', requiredIn: 'any' },
  // WhatsApp 分析页的伴生服务。默认不启动（START_WA_ANALYSIS=0），但它是页面功能，
  // 属于「运行必需」；其 node_modules 必须作为显式条目，否则会被通用遍历丢掉。
  { src: 'apps/portal/scripts/whatsapp_analysis', requiredIn: 'any' },
  // 前端源码（#192）：别人下载后可在目标机重新构建前端；门户 prestart 也需要 src。
  { src: 'apps/my_app/frontend/src', requiredIn: 'any' },
  { src: 'apps/portal/src', requiredIn: 'any' },
  // 构建产物：仅 full 模式（工作流先 npm ci / npm run build / pip download）。
  // node_modules 只走显式条目（通用遍历里一律跳过，避免父目录是否入白名单造成的不一致）。
  { src: 'apps/my_app/frontend/node_modules', requiredIn: 'full' },
  { src: 'apps/portal/node_modules', requiredIn: 'full' },
  { src: 'apps/whatsapp-kb-bot/node_modules', requiredIn: 'full' },
  { src: 'apps/portal/scripts/whatsapp_analysis/node_modules', requiredIn: 'full' },
  { src: 'apps/_fe_dist', requiredIn: 'full' },
  { src: 'apps/portal/dist', requiredIn: 'full' },
  { src: 'wheels', requiredIn: 'full' },
]

// 单文件：required=false 的缺失只警告（P1-4 交付物、部署文档子集）。
export const INCLUDE_FILES = [
  { src: 'apps/my_app/requirements.txt', required: true },
  { src: 'apps/my_app/requirements.lock.txt', required: true },
  { src: 'apps/my_app/pyproject.toml', required: true },
  { src: 'apps/my_app/alembic.ini', required: true },
  // vite preview 的运行前提：package.json（type/base）+ vite.config.ts（outDir=../../_fe_dist）。
  // 前端**源码**不进包，所以这两个文件必须单独列（以前它们靠 frontend/ 整目录带进来）。
  { src: 'apps/my_app/frontend/package.json', required: true },
  { src: 'apps/my_app/frontend/package-lock.json', required: true },
  { src: 'apps/my_app/frontend/vite.config.ts', required: true },
  { src: 'apps/portal/package.json', required: true },
  { src: 'apps/portal/package-lock.json', required: true },
  { src: 'apps/portal/scripts/whatsapp_analysis/package.json', required: true },
  { src: 'apps/portal/scripts/whatsapp_analysis/package-lock.json', required: true },
  { src: 'apps/whatsapp-kb-bot/package.json', required: true },
  { src: 'apps/whatsapp-kb-bot/package-lock.json', required: true },
  // ops/deploy 只发运行 / 首次启动 / 数据迁移 / 证书需要的脚本。
  // 构建脚本（build-zip.mjs、lib/zip-*）、开发自检（agent-done-check）与冒烟工具不进包。
  { src: 'ops/deploy/start_all.ps1', required: true },
  { src: 'ops/deploy/stop_all.ps1', required: true },
  { src: 'ops/deploy/noc-paths.ps1', required: true },
  { src: 'ops/deploy/start.sh', required: true },
  { src: 'ops/deploy/stop.sh', required: true },
  { src: 'ops/deploy/noc-paths.sh', required: true },
  { src: 'ops/deploy/write_nginx_conf.ps1', required: true },
  { src: 'ops/deploy/make-selfsigned-cert.py', required: true },
  { src: 'ops/deploy/env_template.txt', required: true },
  { src: 'ops/deploy/bundle_data.ps1', required: true },
  { src: 'ops/deploy/local_authority_tools.py', required: true },
  // 根入口与模板。
  { src: 'nginx.conf.template', required: true },
  { src: 'start.bat', required: true },
  { src: 'stop.bat', required: true },
  { src: 'start.sh', required: true },
  { src: 'stop.sh', required: true },
  // 目标机唯一需要的文档：安装 / 首次登录 / 升级 / 排障。其余手册不进包。
  { src: '安装与运行说明.md', required: true },
]

// full 模式下暂存区内必须存在的路径（工作流须先装好依赖、建好产物、下载好 wheels）。
export const FULL_REQUIRED = [
  { rel: 'apps/my_app/frontend/node_modules', kind: 'dir' },
  { rel: 'apps/portal/node_modules', kind: 'dir' },
  { rel: 'apps/whatsapp-kb-bot/node_modules', kind: 'dir' },
  { rel: 'apps/portal/scripts/whatsapp_analysis/node_modules', kind: 'dir' },
  { rel: 'apps/_fe_dist/index.html', kind: 'file' },
  { rel: 'apps/portal/dist/index.html', kind: 'file' },
  { rel: 'wheels', kind: 'wheels' },
]

// 按目录名跳过：只保留无歧义的缓存/垃圾/运行态名。db/data 不在此列（见文件头 P0-1 教训）。
// node_modules/dist 由 assemble() 另行加入拒绝集（显式条目除外，见 P1-1）。
// 2026-11-19 新增三项 —— 它们都是 gitignore 的运行时缓存，却曾整目录进包：
//   _pmcache（cn_ems PM 下载缓存，实测 27,072 文件 / 618.5 MB）、
//   native_board_cache（原生看板磁盘缓存）、tt_timeline_cache（TT 时间线缓存）。
export const BASENAME_DENIED_DIRS = new Set([
  '__pycache__', '.git', '.idea', '.vscode', '.venv', 'venv', 'coverage',
  '.pytest_cache', '.ruff_cache', '.mypy_cache', 'auth-data', 'auth_cache',
  'log', 'logs', 'tmp', 'temp', 'TMP', 'runtime', '.cache',
  'backup', 'backups', 'outputs', 'output', 'sessions', 'whatsapp_session',
  'credentials', 'playwright-report', 'test-results', '.agent-work', '.workbuddy-ai',
  '_pmcache', 'native_board_cache', 'tt_timeline_cache', 'portal_profiles',
])

// 按完整相对路径拦：仅应用根级数据目录。嵌套的同名源码目录（如 backend/data/gis）不受影响。
// gp_ran 是上游导出的 CSV 落盘目录（*已 gitignore*，单文件可达 17MB），不是源码。
export const PATH_DENIED_DIRS = [
  'apps/my_app/db',
  'apps/my_app/data',
  'apps/my_app/backend/data/gp_ran',
  'apps/whatsapp-kb-bot/data',
  'apps/portal/auth-data',
]

// 包内**绝不允许出现**的路径（最小化原则的机器守卫）。白名单写错、从上游
// 加了目录、或有人图省事把整棵工作区塞进 ZIP 时，这里会直接让构建失败。
// 与 deniedFileReason 的分工：那边管「单个文件像不像凭据/垃圾」，这里管
// 「整类开发资产是否混进了交付包」。
export const PACKAGE_FORBIDDEN_PATHS = [
  { prefix: '.git/', reason: '版本库' },
  { prefix: '.gitignore', reason: '仓库元数据（用户要求不进版本包）', rootOnly: true },
  { prefix: '.gitattributes', reason: '仓库元数据（用户要求不进版本包）', rootOnly: true },
  { prefix: '.github/', reason: 'CI/Issue 模板（开发用）' },
  { prefix: '.claude/', reason: 'AI 工具配置' },
  { prefix: '.githooks/', reason: 'git 钩子（开发用）' },
  { prefix: '.agent-work/', reason: 'agent 任务笔记' },
  { prefix: '.agents/', reason: 'agent worktree' },
  { prefix: '.workbuddy-ai/', reason: 'AI 会话记忆' },
  { prefix: 'AGENTS.md', reason: '仓库给 AI 的说明（不是产品内容）' },
  { prefix: 'README.md', reason: '仓库首页说明（目标机只看《安装与运行说明.md》）', rootOnly: true },
  { prefix: 'docs/', reason: '开发手册（目标机只保留《安装与运行说明.md》）' },
  { prefix: 'desktop_shortcuts/', reason: '桌面快捷方式生成器' },
  { prefix: 'extensions/', reason: '浏览器扩展（与平台服务无关）' },
  { prefix: 'apps/my_app/tests/', reason: '测试' },
  { prefix: 'apps/my_app/docs/', reason: '开发手册' },
  { prefix: 'apps/my_app/tools/', reason: '代码生成器' },
  { prefix: 'apps/my_app/nginx/', reason: '生产 nginx 配置（交付包用 nginx.conf.template）' },
  { prefix: 'apps/my_app/monitor_fixes/', reason: '生产看门狗脚本（不随交付包）' },
  { prefix: 'apps/my_app/frontend/public/', reason: '前端源码（已打进 _fe_dist）' },
  { prefix: 'apps/portal/docs/', reason: '开发手册' },
  { prefix: 'apps/whatsapp-kb-bot/docs/', reason: '开发手册' },
  { prefix: 'apps/whatsapp-kb-bot/evals/', reason: '评测集（开发用）' },
  { prefix: 'apps/agent-worker/', reason: '协作底座（不在交付包）' },
  { prefix: 'apps/agent-worker-linux/', reason: '协作底座（不在交付包）' },
  { prefix: 'apps/control-plane/', reason: '协作底座（不在交付包）' },
  { prefix: 'ops/smoke/', reason: '冒烟工具（开发用）' },
  { prefix: 'apps/my_app/backend/services/cn_ems/_pmcache/', reason: 'PM 下载缓存（实测 618MB）' },
  { prefix: 'apps/my_app/backend/data/native_board_cache/', reason: '看板磁盘缓存' },
  { prefix: 'apps/my_app/backend/data/tt_timeline_cache/', reason: 'TT 时间线缓存' },
  { prefix: 'apps/my_app/backend/data/gp_ran/', reason: '上游 CSV 落盘数据' },
]

// 回归断言（P0-1）：组装后暂存区内必须存在的文件。缺失即门禁失败，防白名单规则回退。
// 注：portal/src 的两个文件曾在 2026-11 的 P0-1 事故里被误丢，当时它们随
// apps/portal/src 进包。最小化后前端源码一度不再进包（目标机只跑 dist），那两个
// 断言随条目一起移除；#192（2026-11-20）恢复前端源码进包（别人下载自包含运行，
// 要在目标机重新构建），断言随之恢复；另加 credential.py 两件（#192 根因：
// deniedFileReason 不分扩展名静默丢凭据名源码，Linux 包 alembic 建表失败）。
export const REQUIRED_STAGING_FILES = [
  'apps/my_app/backend/services/db/__init__.py',
  'apps/my_app/backend/services/db/db_freshness_check.py',
  'apps/my_app/backend/services/db/db_writer_client.py',
  'apps/my_app/backend/services/db/pg_schema_freshness.py',
  'apps/my_app/backend/data/gis/liberia_counties.geojson',
  'apps/my_app/backend/data/gis/liberia_country.geojson',
  'apps/my_app/backend/models/credential.py',
  'apps/my_app/backend/routers/credentials.py',
  'apps/my_app/frontend/src/App.tsx',
  'apps/portal/src/App.tsx',
  'apps/my_app/frontend/vite.config.ts',
  '安装与运行说明.md',
]
