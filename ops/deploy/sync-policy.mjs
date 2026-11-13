import path from 'node:path';

export const PORTAL = 'apps/portal';
export const APP = 'apps/my_app';
export const EMS = `${APP}/backend/services/cn_ems`;
export const STATE = 'TMP/_sync_state.json';
export const FIX_TARGETS = [`${APP}/monitor_fixes/nginx_monitor.ps1`, `${PORTAL}/rotate_nginx_logs.ps1`];
// 工作区级文档（docs/workspace/）：副本的 AGENTS.md 曾在「建库初始提交」后冻结不动，
// 于是副本里的规则与编辑区分了叉（2026-11-11 实测：副本 AGENTS.md 停在 2026-11-10，
// 编辑区已到 2026-11-06 之后的多次修订）。docs/workspace/ 的 md 同步过来即可保持一致。
// 注意：只同步 .md —— 副本自己的脚本（ops\deploy\）由副本自己维护，见下方 ROOT_FILES 的用途说明。
export const ROOT_FILES = [
  'AGENTS.md',
  'docs/workspace/development.md',
  'docs/workspace/architecture.md',
  'docs/workspace/TODO.md',
  'docs/workspace/project-overview.md',
  'docs/workspace/user-guide.md',
  'docs/workspace/DESIGN.md',
  'docs/workspace/component-api.md',
];
// 只同步源码；构建产物（apps/_fe_dist、门户 dist）由副本自行构建，绝不从生产拷贝。
export const GROUPS = [
  { root: APP, dirs: ['backend', 'frontend', 'alembic', 'docs', 'tests', 'tools', 'email_reports', 'monitor_fixes', 'portal_login_extension', 'whatsapp_sender_extension'] },
  { root: 'apps/whatsapp-kb-bot', dirs: ['src', 'scripts', 'docs', 'knowledge', 'evals', 'certs'] },
  { root: 'apps/eflow_Q&A', dirs: null },
  { root: PORTAL, dirs: ['src', 'server', 'public', 'scripts', 'docs', 'tests', 'MsPortal', 'OlrPortal', 'olbrportal', 'v1', 'v2', 'v3'] },
];
const sourceExt = new Set('.py .pyi .pyw .js .jsx .mjs .cjs .ts .tsx .vue .svelte .html .htm .css .scss .less .json .geojson .md .yml .yaml .toml .ini .cfg .conf .sql .ps1 .bat .vbs .sh .proto .graphql .svg .png .jpg .jpeg .gif .ico .woff .woff2 .ttf .eot .map .webp'.split(' '));
const deniedDirs = new Set('node_modules __pycache__ .git .idea .vscode .claude .qoder .cursor .trae .pytest_cache .ruff_cache .mypy_cache auth_cache auth-data native_board_cache portal_profiles _pmcache session_bak outputs output backup backups runtime log logs tmp temp cache caches coverage test-results playwright-report .venv venv pyvenv db dist dist2 build whatsapp_session sessions credentials jab_setup'.split(' '));
const resources = new Set([
  `${EMS}/Query_Template.xml`,
  'apps/whatsapp-kb-bot/certs/liberia-noc-ca.pem',
  ...['eflow_TT_rules.xlsx', 'eflow_push_rules.xlsx', 'eflow_alarm_rules.xlsx'].map(n => `${APP}/backend/services/eflow_rules/${n}`),
].map(x => x.toLowerCase()));
// 副本定时任务会重写这些文件。源上那份只是上次落盘的快照：
//   - cn_ems/daily_raw.json、kpi_result*.json：EMS 日聚合 / 查询落盘
//   - eflow_*_rules.xlsx：收集器从 eFlow 拉取（TT 每天 04:20，告警/推单每 3 天）
//   - *_rules_check_latest.json、MATCHING_REPORT.md：检查任务的摘要与报告
// 副本上已经有就保留（不覆盖、不报冲突）；还没有才从源播种，换机第一次同步仍能起步。
export const RUNTIME_OWNED = new Set([
  `${EMS}/daily_raw.json`,
  `${EMS}/kpi_result.json`,
  `${EMS}/kpi_result_raw.json`,
  `${APP}/backend/services/eflow_rules/eflow_TT_rules.xlsx`,
  `${APP}/backend/services/eflow_rules/eflow_alarm_rules.xlsx`,
  `${APP}/backend/services/eflow_rules/eflow_push_rules.xlsx`,
  `${APP}/backend/services/eflow_rules/eflow_tt_rules_check_latest.json`,
  `${APP}/backend/services/eflow_rules/eflow_push_rules_check_latest.json`,
  `${APP}/backend/services/eflow_rules/MATCHING_REPORT.md`,
].map(x => x.toLowerCase()));
export function runtimeOwned(rel) {
  return RUNTIME_OWNED.has(String(rel).toLowerCase());
}

export function relativeName(value) {
  if (typeof value !== 'string' || !value || /[\x00-\x1f:*?"<>|]/.test(value)) throw new Error(`非法相对路径: ${value}`);
  const rel = value.replaceAll('\\', '/');
  if (rel.split('/').some(p => !p || p === '.' || p === '..' || /[. ]$/.test(p) || /^(con|prn|aux|nul|com\d|lpt\d)(\.|$)/i.test(p))) throw new Error(`非法相对路径: ${value}`);
  return rel;
}
export function groupFor(rel) {
  return GROUPS.find(g => rel.toLowerCase().startsWith(`${g.root.toLowerCase()}/`));
}
// 只拦「运行时凭据/会话/本地配置」与临时产物；credentials.py / credential_store.py 等真源码放行。
function deniedName(name) {
  return /^\./.test(name)
    || /(?:\.bak(?:[_.-]|$)|\.log(?:\.|$)|\.pid$|\.jsonl$|\.tmpdel_|\.tsbuildinfo$|url\.txt$|pyvenv\.cfg$)/i.test(name)
    || /^(?:_bak_|_tmp_|tmp_|backup_|_quarantine_|_myapp_trash_|_local[-_]credentials|CREDENTIALS_LOCAL_REFERENCE)/i.test(name)
    || /(?:^|[._-])(?:local[-_]credentials|credentials[-_]local)(?:[._-]|$)/i.test(name)
    || /^(?:auth(?:_info|-switch)?|portal-default|config|credentials?|secrets?|sessions?|cookies?|tokens?)(?:[._-].*)?\.(?:json|txt|ya?ml|ini|cfg)$/i.test(name)
    || /(?:session|cookie|token|credential|secret)s?\.(?:json|txt|ya?ml|ini|cfg|db|sqlite3?)$/i.test(name)
    || /(?:^|[._-])env(?:[._-]|$)/i.test(name);
}
export function allowedDirectory(rel, group) {
  const parts = rel.slice(group.root.length + 1).split('/');
  if (group.dirs && !group.dirs.some(d => d.toLowerCase() === parts[0].toLowerCase())) return false;
  if (parts.some(p => deniedDirs.has(p.toLowerCase()) || deniedName(p))) return false;
  // data 仅放行前端编译时数据，绝不遍历运行时 data/db。
  if (parts.some(p => p.toLowerCase() === 'data') && ![`${APP}/frontend/src/data`, `${PORTAL}/src/data`].some(p => rel === p || rel.startsWith(p + '/'))) return false;
  return true;
}
export function allowedFile(rel, group = groupFor(rel)) {
  // 工作区级文档：显式白名单放行（它们不属于任何 GROUP，但必须跟着同步，见 ROOT_FILES 注释）。
  if (ROOT_FILES.includes(rel)) return true;
  if (!group) return false;
  if (resources.has(rel.toLowerCase())) return true; // 显式业务输入（规则表/模板/公共CA）优先放行
  const local = rel.slice(group.root.length + 1);
  const name = path.posix.basename(rel);
  if (deniedName(name)) return false;
  if (local.includes('/') && !allowedDirectory(rel.slice(0, rel.lastIndexOf('/')), group)) return false;
  const ext = path.posix.extname(name).toLowerCase();
  // 组根目录下的脚本/下划线文件多为一次性探针，除两个需改路径的监控脚本外不同步。
  if (!local.includes('/') && (name.startsWith('_') || ['.bat', '.ps1', '.vbs'].includes(ext)) && !FIX_TARGETS.includes(rel)) return false;
  if (rel.startsWith(`${EMS}/`) && !rel.slice(EMS.length + 1).includes('/') && ['.java', '.class', '.jar'].includes(ext)) return true;
  if (rel.startsWith(`${EMS}/samples/`) && !rel.slice(`${EMS}/samples/`.length).includes('/') && ext === '.xlsx') return true;
  if (rel.startsWith(`${APP}/email_reports/`)) return ['.py', '.pyi', '.pyw', '.js', '.mjs', '.ts', '.html', '.css'].includes(ext);
  if (ext === '.txt') return /^(requirements(?:[.-][\w-]+)?\.txt|CMakeLists\.txt|robots\.txt)$/i.test(name);
  return sourceExt.has(ext);
}
// 两个监控脚本的默认路径在同步时确定性地改写为副本路径；匹配数必须恰为 1，否则报错停止。
export function portableContent(rel, bytes, root, httpPort) {
  if (!FIX_TARGETS.includes(rel)) return bytes;
  let text = bytes.toString('utf8');
  const defaults = rel === FIX_TARGETS[0] ? {
    NginxExe: path.join(root, 'nginx-1.28.0', 'nginx.exe'),
    NginxDir: path.join(root, 'nginx-1.28.0'), NginxPort: httpPort,
    LogPath: path.join(root, 'apps', 'nginx_monitor.log'),
    StatePath: path.join(root, 'apps', 'nginx_monitor.state'),
  } : { Prefix: path.join(root, 'nginx-1.28.0') + path.sep };
  for (const [key, value] of Object.entries(defaults)) {
    const pattern = new RegExp(`(\\[(?:string|int)\\]\\s*\\$${key}\\s*=\\s*)(?:'[^']*'|"[^"]*"|\\d+)`, 'gi');
    let count = 0;
    text = text.replace(pattern, (_, prefix) => {
      count++;
      return prefix + (typeof value === 'number' ? value : `'${String(value).replaceAll("'", "''")}'`);
    });
    if (count !== 1) throw new Error(`${rel}: 参数 ${key} 默认值匹配 ${count} 次（应为 1），停止同步以免写坏`);
  }
  return Buffer.from(text);
}
export function deploymentNeeds(changed) {
  const reasons = new Set();
  for (const rel of changed) {
    if (/(^|\/)(package(?:-lock)?\.json|requirements[^/]*\.txt|pyproject\.toml|poetry\.lock)$/.test(rel)) reasons.add('依赖清单变化：需在副本独立核对并安装依赖');
    if (rel.startsWith(`${APP}/frontend/`) || rel.startsWith(`${PORTAL}/src/`) || /(?:vite|tsconfig)[^/]*\./.test(rel)) reasons.add('前端输入变化：需在副本重新构建前端/门户产物');
    if (rel.startsWith(`${APP}/alembic/`)) reasons.add('迁移源码变化：需人工评估副本数据库迁移（同步器不操作数据库）');
  }
  return [...reasons];
}
