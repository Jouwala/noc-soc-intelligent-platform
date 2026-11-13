// 事务式增量同步：源只读，副本唯一写入目标。冲突整体失败（fail-closed），
// 逐文件备份 + 原子改名，任一步失败整体回滚；状态最后原子落盘。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { GROUPS, ROOT_FILES, STATE, FIX_TARGETS, groupFor, allowedFile, allowedDirectory, relativeName, portableContent, deploymentNeeds, runtimeOwned } from './sync-policy.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const flag = n => argv.includes(n);
const opt = (n, d) => { const i = argv.indexOf(n); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };
const dryRun = flag('--dry-run');
const quiet = flag('--quiet');
const acceptSource = flag('--accept-source');
const target = path.resolve(opt('--target', path.join(here, '..')));
const source = path.resolve(opt('--source', 'D:/01_RPA'));
const log = (...a) => { if (!quiet) console.log(...a); };
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const readStrip = p => fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, '');

function fail(msg, code = 1) { console.error('[ABORT] ' + msg); process.exit(code); }

// ---- 边界校验 ----
// 2026-11-11 起副本已接管 80/443，ISOLATED_COPY.flag 按转正步骤删掉了。
// 防误写只靠下面的「源≠目标、互不嵌套」；不能再拿隔离旗当同步开关。
const tReal = fs.realpathSync.native(target);
const sReal = fs.realpathSync.native(source);
if (tReal.toLowerCase() === sReal.toLowerCase()) fail('源与目标相同', 2);
if (tReal.toLowerCase().startsWith(sReal.toLowerCase() + path.sep) || sReal.toLowerCase().startsWith(tReal.toLowerCase() + path.sep)) fail('源与目标互相嵌套', 2);
function underTarget(p) {
  const r = fs.realpathSync.native(p);
  return r.toLowerCase() === tReal.toLowerCase() || r.toLowerCase().startsWith(tReal.toLowerCase() + path.sep);
}
if (!underTarget(target)) fail('目标根是 junction/符号链接，拒绝同步', 2);

// ---- 载入旧状态（兼容大写哈希 / BOM / 正斜杠键）----
const statePath = path.join(target, STATE);
const state = {};
if (fs.existsSync(statePath)) {
  let raw;
  try { raw = JSON.parse(readStrip(statePath)); } catch { fail('同步状态损坏: ' + statePath, 2); }
  for (const [k, v] of Object.entries(raw)) state[relativeName(k)] = String(v).toLowerCase();
}

// ---- 副本 http 端口（供监控脚本路径改写）----
let httpPort = 8080;
const envPath = path.join(target, '.env');
if (fs.existsSync(envPath)) {
  const m = readStrip(envPath).split(/\r?\n/).find(l => /^\s*NGINX_HTTP_PORT\s*=/.test(l));
  if (m) { const v = parseInt(m.split('=')[1].trim(), 10); if (v > 0) httpPort = v; }
}

// ---- 遍历源（剪枝，避免扫 node_modules/runtime/data 等十万级无关文件）----
function walk(dir, relBase, out) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const rel = relBase ? relBase + '/' + e.name : e.name;
    if (e.isDirectory()) {
      const g = groupFor(rel + '/x');
      if (g && !allowedDirectory(rel, g)) continue;
      if (!g) continue;
      walk(path.join(dir, e.name), rel, out);
    } else if (e.isFile()) {
      if (allowedFile(rel)) out.push(rel);
    }
  }
}
const sourceRels = [];
for (const g of GROUPS) {
  const root = path.join(source, g.root);
  if (!fs.existsSync(root)) { log('[SKIP GROUP] 源缺 ' + g.root); continue; }
  walk(root, g.root, sourceRels);
}
// 根级文档（AGENTS.md 等）：显式点名，不靠遍历 —— 它们在副本根、不是任何 GROUP 的子目录。
for (const f of ROOT_FILES) {
  if (fs.existsSync(path.join(source, f))) sourceRels.push(f);
  else log('[SKIP ROOT FILE] 源缺 ' + f);
}
const currentSet = new Set(sourceRels);

// ---- 生成计划 ----
const writes = [], deletes = [], conflicts = [], localDeletions = [], accepted = [], keptRuntime = [];
const newState = {};
let unchanged = 0;
for (const rel of sourceRels) {
  const srcBytes = fs.readFileSync(path.join(source, rel));
  const desired = portableContent(rel, srcBytes, target, httpPort);
  const desiredSha = sha(desired);
  const dst = path.join(target, rel);
  // 运行时产物：副本任务会重写。已存在则保留今天的内容，不当冲突、也不用源快照覆盖。
  if (runtimeOwned(rel) && fs.existsSync(dst)) {
    const dstSha = sha(fs.readFileSync(dst));
    newState[rel] = dstSha;
    if (dstSha === desiredSha) unchanged++;
    else keptRuntime.push(rel);
    continue;
  }
  const derived = FIX_TARGETS.includes(rel); // 机器派生产物：始终按源+副本根重写，不受状态保护
  if (fs.existsSync(dst)) {
    const dstSha = sha(fs.readFileSync(dst));
    if (dstSha === desiredSha) { unchanged++; newState[rel] = desiredSha; continue; }
    if (derived || (state[rel] && dstSha === state[rel])) { writes.push({ rel, dst, desired }); newState[rel] = desiredSha; }
    else if (acceptSource) { accepted.push(rel); writes.push({ rel, dst, desired }); newState[rel] = desiredSha; }
    else conflicts.push({ rel, reason: state[rel] ? '副本本地已修改' : '副本存在未知本地文件' });
  } else if (state[rel] && !derived) {
    localDeletions.push(rel); // 尊重副本 intentional 删除，不重建
  } else {
    writes.push({ rel, dst, desired }); newState[rel] = desiredSha;
  }
}
// 源已删除的受管文件：副本未改则隔离，已改则冲突；非受管旧条目（产物）静默剪除不触碰文件。
for (const rel of Object.keys(state)) {
  if (currentSet.has(rel)) continue;
  const g = groupFor(rel);
  // 根级文档没有 GROUP，但同样要参与「源已删除」的处理（见 ROOT_FILES 注释）。
  if ((!g && !ROOT_FILES.includes(rel)) || !allowedFile(rel)) continue;
  if (runtimeOwned(rel)) continue; // 源快照消失也不隔离副本上的运行时文件
  const dst = path.join(target, rel);
  if (!fs.existsSync(dst)) continue;
  if (sha(fs.readFileSync(dst)) === state[rel]) deletes.push({ rel, dst });
  else if (acceptSource) { accepted.push(rel); deletes.push({ rel, dst }); }
  else conflicts.push({ rel, reason: '源已删除但副本本地已修改' });
}

const changed = [...writes.map(w => w.rel), ...deletes.map(d => d.rel)];
log('=== 源同步（增量 / 白名单 / 事务） ===');
log(`源(只读): ${source}`);
log(`副本    : ${target}`);
log(`干跑    : ${dryRun}   接受源版本: ${acceptSource}   受管源文件: ${sourceRels.length}   旧状态: ${Object.keys(state).length}`);
log(`不变 ${unchanged} | 写入 ${writes.length} | 隔离删除 ${deletes.length} | 尊重本地删除 ${localDeletions.length} | 保留运行时 ${keptRuntime.length} | 冲突 ${conflicts.length} | 显式接受 ${accepted.length}`);
if (keptRuntime.length) { log('--- 保留副本运行时产物（源快照不覆盖） ---'); keptRuntime.forEach(r => log('  [KEEP] ' + r)); }
if (localDeletions.length) { log('--- 尊重副本本地删除（不从源重建） ---'); localDeletions.forEach(r => log('  [KEEP-DELETED] ' + r)); }
if (accepted.length) { log('--- 显式接受源版本（已备份，可回滚） ---'); accepted.forEach(r => log('  [ACCEPT] ' + r)); }
if (conflicts.length) {
  log('--- 冲突（整体停止，未写任何文件） ---');
  for (const c of conflicts.slice(0, 200)) log(`  [${c.reason}] ${c.rel}`);
  if (conflicts.length > 200) log(`  … 其余 ${conflicts.length - 200} 条`);
}
if (dryRun) {
  for (const w of writes.slice(0, 50)) log('  [WRITE] ' + w.rel);
  for (const d of deletes.slice(0, 50)) log('  [REMOVE] ' + d.rel);
  const needs = deploymentNeeds(changed);
  if (needs.length) { log('--- 部署提示 ---'); needs.forEach(n => log('  · ' + n)); }
  process.exit(conflicts.length ? 2 : 0);
}
if (conflicts.length) fail(`存在 ${conflicts.length} 处本地冲突，已整体停止。请先处理上述文件（保留本地改动或显式接受源版本）再同步。`, 2);
if (!writes.length && !deletes.length) { log('无变更，副本已是最新。'); process.exit(0); }

// ---- 事务应用 ----
const lockPath = path.join(target, 'TMP', '_sync.lock');
fs.mkdirSync(path.join(target, 'TMP'), { recursive: true });
let lockFd;
try { lockFd = fs.openSync(lockPath, 'wx'); } catch { fail('另一同步正在进行（TMP/_sync.lock 存在）', 2); }
const ts = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14) + '_' + process.pid;
const backupDir = path.join(target, 'TMP', '_pre_sync_' + ts);
const quarantineDir = path.join(target, 'TMP', '_sync_removed_' + ts);
const applied = [];
function atomicWrite(p, buf) {
  const tmp = p + '.tmp_' + process.pid;
  fs.writeFileSync(tmp, buf);
  fs.renameSync(tmp, p);
}
try {
  for (const w of writes) {
    const parent = path.dirname(w.dst);
    fs.mkdirSync(parent, { recursive: true });
    if (!underTarget(parent)) throw new Error('写入路径越界（junction）: ' + w.rel);
    let backupPath = null;
    if (fs.existsSync(w.dst)) {
      backupPath = path.join(backupDir, w.rel);
      fs.mkdirSync(path.dirname(backupPath), { recursive: true });
      fs.copyFileSync(w.dst, backupPath);
    }
    atomicWrite(w.dst, w.desired);
    applied.push({ dst: w.dst, backupPath });
  }
  for (const d of deletes) {
    const q = path.join(quarantineDir, d.rel);
    fs.mkdirSync(path.dirname(q), { recursive: true });
    fs.renameSync(d.dst, q);
    applied.push({ dst: d.dst, quarantine: q });
  }
  atomicWrite(statePath, Buffer.from(JSON.stringify(newState, null, 0)));
} catch (e) {
  for (const a of applied.reverse()) {
    try {
      if (a.quarantine) fs.renameSync(a.quarantine, a.dst);
      else if (a.backupPath) fs.copyFileSync(a.backupPath, a.dst);
      else fs.rmSync(a.dst, { force: true });
    } catch { /* 回滚尽力而为 */ }
  }
  fail('同步失败已回滚: ' + e.message, 1);
} finally {
  try { fs.closeSync(lockFd); fs.rmSync(lockPath, { force: true }); } catch { /* ignore */ }
}
log(`[OK] 已写入 ${writes.length}，隔离删除 ${deletes.length}。备份: ${backupDir}`);
if (deletes.length) log(`     被删文件隔离于: ${quarantineDir}（确认无误后可清理）`);
const needs = deploymentNeeds(changed);
if (needs.length) { log('--- 部署提示（同步 ≠ 部署完成） ---'); needs.forEach(n => log('  · ' + n)); }
log('下一步：在副本构建前端/门户并按需启动（start.bat）。');
