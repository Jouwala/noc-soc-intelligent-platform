/**
 * 构建脚本回归测试（P2-1，PR #151 评审意见）。
 *
 * 零第三方依赖，Node 内置 test runner：node --test ops/deploy/lib/zip-package.test.mjs
 * release-build.yml 在构建前跑本文件。集成用例需在仓库检出内运行，否则跳过。
 */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path, { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  COPY_MARKER_FILE, GateError, assemble, assertExclusions, assertPackageMinimal,
  deniedFileReason, isPathInside, scanCredentials, walkFiles,
} from './zip-package.mjs'
import { BASENAME_DENIED_DIRS, PACKAGE_FORBIDDEN_PATHS, PATH_DENIED_DIRS, REQUIRED_STAGING_FILES } from './zip-manifest.mjs'
import { writeZip } from './zip-writer.mjs'

function fixture(t, files) {
  const dir = mkdtempSync(join(tmpdir(), 'zip-test-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  for (const [rel, content] of Object.entries(files)) {
    const abs = join(dir, rel)
    mkdirSync(dirname(abs), { recursive: true })
    writeFileSync(abs, content)
  }
  return dir
}

/**
 * 创建符号链接；Windows 未开开发者模式且非管理员时 symlink 抛 EPERM。
 * 这是**测试环境**限制（CI 的 Windows runner 以管理员运行，能建），
 * 不该让整个套件在普通开发机上变红：失败时标记 skip 并让调用方 return。
 */
function symlinkOrSkip(t, target, path) {
  try {
    symlinkSync(target, path)
    return true
  } catch (e) {
    if (e.code === 'EPERM' || e.code === 'EACCES') {
      t.skip('Windows 无符号链接权限（需开发者模式或管理员），本机跳过')
      return false
    }
    throw e
  }
}

function findRepoRoot() {  let dir = dirname(fileURLToPath(import.meta.url))
  for (let i = 0; i < 6; i += 1) {
    if (existsSync(join(dir, 'apps/my_app/backend/main.py'))) return dir
    const parent = dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
  return null
}

// ─────────────────────────────────────────────────────────────────────────────
// P0-2：路径包含判定（含 Windows 模拟）
// ─────────────────────────────────────────────────────────────────────────────

test('isPathInside：posix 与 win32', () => {
  assert.equal(isPathInside('/a/b', '/a/b/c/d'), true)
  assert.equal(isPathInside('/a/b', '/a/b'), true)
  assert.equal(isPathInside('/a/b', '/a/c'), false)
  assert.equal(isPathInside('/a/b', '/a/bc/d'), false) // 前缀相同但不在目录内
  assert.equal(isPathInside('/a/b', '/etc/passwd'), false)
  // Windows 模拟（评审 P0-2 的反例：旧 startsWith(`${base}/`) 在此恒为假）
  assert.equal(isPathInside('D:\\02_olbr_portal', 'D:\\02_olbr_portal\\TMP\\zip-build', path.win32), true)
  assert.equal(isPathInside('D:\\02_olbr_portal', 'D:\\01_olbr_portal\\TMP', path.win32), false)
  assert.equal(isPathInside('D:\\02_olbr_portal', 'C:\\Temp\\x', path.win32), false)
})

// ─────────────────────────────────────────────────────────────────────────────
// P0-1：拒绝规则按完整路径生效，嵌套源码目录不被丢
// ─────────────────────────────────────────────────────────────────────────────

test('walkFiles：嵌套 db/data 源码目录保留，应用根级数据目录拦住', (t) => {
  const root = fixture(t, {
    'apps/my_app/backend/services/db/a.py': 'x',
    'apps/my_app/backend/data/gis/x.geojson': '{}',
    'apps/portal/src/data/d.ts': 'x',
    'apps/my_app/db/app.db': 'x',
    'apps/my_app/data/f.txt': 'x',
    'apps/my_app/backend/__pycache__/c.pyc': 'x',
    'apps/my_app/backend/.hidden/s.txt': 'x',
  })
  const denied = new Set(BASENAME_DENIED_DIRS)
  const files = walkFiles(root, '', denied, [], { pathDenied: PATH_DENIED_DIRS })
  assert.ok(files.includes('apps/my_app/backend/services/db/a.py'))
  assert.ok(files.includes('apps/my_app/backend/data/gis/x.geojson'))
  assert.ok(files.includes('apps/portal/src/data/d.ts'))
  assert.ok(!files.some((f) => f.startsWith('apps/my_app/db/')))
  assert.ok(!files.some((f) => f.startsWith('apps/my_app/data/')))
  assert.ok(!files.some((f) => f.includes('__pycache__')))
  assert.ok(!files.some((f) => f.includes('.hidden')))
})

// ─────────────────────────────────────────────────────────────────────────────
// P1-1：显式 node_modules 树内不丢嵌套 node_modules 与 dist
// ─────────────────────────────────────────────────────────────────────────────

test('walkFiles：node_modules 树内嵌套目录与 dist 保留，符号链接跳过并记数', (t) => {
  const root = fixture(t, {
    'apps/portal/node_modules/foo/node_modules/bar/index.js': 'x',
    'apps/portal/node_modules/foo/dist/index.js': 'x',
    'apps/portal/node_modules/foo/package.json': '{}',
  })
  if (!symlinkOrSkip(t, join(root, 'apps/portal/node_modules/foo/package.json'), join(root, 'apps/portal/node_modules/foo/link.js'))) return
  const nmRoot = join(root, 'apps/portal/node_modules')
  // 通用拒绝集会丢嵌套目录（旧行为，复现 P1-1）
  const baseDenied = new Set(BASENAME_DENIED_DIRS)
  baseDenied.add('node_modules')
  baseDenied.add('dist')
  assert.throws(() => walkFiles(nmRoot, 'apps/portal/node_modules', baseDenied, []), /符号链接/)
  // 显式条目拒绝集：去掉 node_modules/dist，符号链接跳过
  const nmDenied = new Set(baseDenied)
  nmDenied.delete('node_modules')
  nmDenied.delete('dist')
  const skipped = []
  const files = walkFiles(nmRoot, 'apps/portal/node_modules', nmDenied, [], { symlinkSkip: true, skipped })
  assert.ok(files.includes('apps/portal/node_modules/foo/node_modules/bar/index.js'))
  assert.ok(files.includes('apps/portal/node_modules/foo/dist/index.js'))
  assert.equal(skipped.length, 1)
  assert.ok(skipped[0].endsWith('link.js'))
})

test('walkFiles：普通树内符号链接失败关闭', (t) => {
  const root = fixture(t, { 'a.py': 'x' })
  if (!symlinkOrSkip(t, join(root, 'a.py'), join(root, 'link.py'))) return
  assert.throws(() => walkFiles(root, '', new Set(), []), /符号链接/)
})

// ─────────────────────────────────────────────────────────────────────────────
// 文件名拒绝规则与门禁植入测试
// ─────────────────────────────────────────────────────────────────────────────

test('deniedFileReason：规则表', () => {
  assert.match(deniedFileReason('.env'), /\.env/)
  assert.match(deniedFileReason('a/b/.env.local'), /\.env/)
  assert.equal(deniedFileReason('a/.env.example'), null)
  assert.match(deniedFileReason('x/cert.pem'), /pem/)
  assert.match(deniedFileReason('x/id.key'), /key/)
  assert.match(deniedFileReason('x/PG_VERSION'), /PG/)
  assert.match(deniedFileReason('apps/whatsapp-kb-bot/config.json'), /bot/)
  assert.equal(deniedFileReason('apps/portal/config.json'), null)
  assert.match(deniedFileReason('x/data.sql'), /快照/)
  // 2026-11-19 最小化：下列三类曾真实进过包 / 会泄露会话
  assert.match(deniedFileReason('PRODUCTION.flag'), /生产/)
  assert.match(deniedFileReason('apps/my_app/backend/services/battary_kpi_fetch/auth_info.json'), /鉴权快照/)
  assert.match(deniedFileReason('scripts/eflow-rule-lookup.py.old_20261028'), /旧版本/)
  assert.equal(deniedFileReason('apps/my_app/backend/services/db/a.py'), null)
})

test('最小化门禁：开发资产/生产标记/缺隔离标记都会失败', (t) => {
  const clean = fixture(t, { 'ok.py': 'print(1)\n', [COPY_MARKER_FILE]: 'copy\r\n' })
  assertPackageMinimal(clean)

  for (const rel of [
    '.github/workflows/quality.yml',
    '.claude/settings.json',
    '.githooks/pre-commit',
    '.agent-work/tasks/active/task-1.md',
    '.gitignore',
    '.gitattributes',
    'README.md',
    'AGENTS.md',
    'apps/my_app/AGENTS.md',
    '.workbuddy-ai/memory/MEMORY.md',
    'apps/my_app/tests/test_x.py',
    'apps/portal/src/App.tsx',
    'apps/control-plane/backend/main.py',
    'apps/my_app/backend/services/cn_ems/_pmcache/PM_1.zip',
    'docs/workspace/TODO.md',
  ]) {
    const bad = fixture(t, { 'ok.py': 'x', [COPY_MARKER_FILE]: 'copy\r\n', [rel]: 'x' })
    assert.throws(() => assertPackageMinimal(bad), GateError, `应拦下: ${rel}`)
  }

  // 生产标记混进来 = 打错了树
  const prod = fixture(t, { 'ok.py': 'x', [COPY_MARKER_FILE]: 'copy\r\n', 'PRODUCTION.flag': 'x' })
  assert.throws(() => assertPackageMinimal(prod), /生产标记/)
  // 缺隔离标记 = 解压后 start.bat 会拒绝运行
  const noMarker = fixture(t, { 'ok.py': 'x' })
  assert.throws(() => assertPackageMinimal(noMarker), new RegExp(COPY_MARKER_FILE))
  // 禁止清单本身不能是空表（防止有人「修绿」把它清空）
  assert.ok(PACKAGE_FORBIDDEN_PATHS.length >= 15)
})

test('门禁植入：凭据/.env/pgdata 都会被拦截', (t) => {
  const staging = fixture(t, {
    'ok.py': 'print(1)\n',
    'leak.py': '-----BEGIN PRIVATE KEY-----\n',
    'sub/.env': 'A=1\n',
    'pgdata/PG_VERSION': '16\n',
  })
  assert.throws(() => scanCredentials(staging), GateError)
  assert.throws(() => assertExclusions(staging), GateError)
  const clean = fixture(t, { 'ok.py': 'print(1)\n' })
  assertExclusions(clean)
  scanCredentials(clean)
})

test('凭据扫描：node_modules 内示例密钥不误报，自有文件仍拦截', (t) => {
  const staging = fixture(t, {
    'apps/portal/node_modules/dotenv/README.md': '-----BEGIN PRIVATE KEY-----\n示例密钥\n',
    'apps/portal/src/ok.ts': 'export {}\n',
  })
  scanCredentials(staging) // 不抛：第三方包文档示例不是泄漏
  const leak = fixture(t, { 'apps/portal/src/leak.ts': '-----BEGIN PRIVATE KEY-----\n' })
  assert.throws(() => scanCredentials(leak), GateError)
})

// ─────────────────────────────────────────────────────────────────────────────
// 集成：真实仓库组装 + 回归文件 + 最小化 + 确定性
// ─────────────────────────────────────────────────────────────────────────────

test('集成：source 组装含回归文件，不含开发资产与运行期缓存', (t) => {
  const root = findRepoRoot()
  if (!root) t.skip('不在仓库检出内，跳过集成用例')
  const staging = mkdtempSync(join(tmpdir(), 'zip-staging-'))
  t.after(() => rmSync(staging, { recursive: true, force: true }))
  const { copied } = assemble({ root, staging, mode: 'source' })
  // 2026-11-19 最小化后的合理量级：源码约数百个文件（此前含 _pmcache 时是 2.8 万）。
  assert.ok(copied.length > 200, `组装文件过少: ${copied.length}`)
  assert.ok(copied.length < 5000, `组装文件异常偏多（疑似运行期缓存/源码树回潮）: ${copied.length}`)
  for (const rel of REQUIRED_STAGING_FILES) assert.ok(existsSync(join(staging, rel)), `缺回归文件: ${rel}`)
  assert.ok(!existsSync(join(staging, 'apps/my_app/db')))
  assert.ok(!existsSync(join(staging, 'apps/my_app/data')))
  // 最小化门禁本身也要在真实仓库上跑通（含 ISOLATED_COPY.flag 自动写入）
  assertPackageMinimal(staging)
  const files = walkFiles(staging, '', new Set(), [])
  for (const rel of ['apps/portal/src/App.tsx', 'apps/my_app/tests', 'apps/control-plane', '.github', 'docs/workspace']) {
    assert.ok(!files.some((f) => f === rel || f.startsWith(`${rel}/`)), `不该进包: ${rel}`)
  }
  assert.ok(!files.some((f) => f.includes('/_pmcache/')), '_pmcache 不该进包')
  assert.ok(!files.some((f) => f.includes('/native_board_cache/')), 'native_board_cache 不该进包')
  assert.ok(!files.some((f) => f.includes('/node_modules/')), 'source 模式不该含 node_modules')
})

test('仓库不得跟踪「已忽略」的过程态文件（.agent-work / .workbuddy-ai / outputs）', (t) => {
  const root = findRepoRoot()
  if (!root) t.skip('不在仓库检出内，跳过')
  // 这批文件是 2026-11-19 用户实测「不该进版本包」的直接来源：
  //   .agent-work/ 16 个进度档、apps/my_app/outputs/auth_health/ 4 个运行期报告。
  // 它们都在 .gitignore 里，却因为「先提交、后加规则」仍在库中，于是必然进入
  // GitHub 自动生成的 Source code ZIP（export-ignore 只能事后补救）。
  // 这条守卫盯住根因：只要出现「被忽略却仍跟踪」的文件，release-build 就失败。
  const r = spawnSync('git', ['ls-files', '-ci', '--exclude-standard'], { cwd: root, encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
  const tracked = r.stdout.split('\n').map((s) => s.trim()).filter(Boolean)
  assert.deepEqual(tracked, [], `这些文件已被 .gitignore 覆盖却仍在版本库里：\n  - ${tracked.join('\n  - ')}`)
})

test('zip-writer：同一输入两次写出字节相同', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'zip-det-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const entries = [
    { path: 'a.txt', data: Buffer.from('hello\n'), mode: 0o644, dir: false },
    { path: 'run.sh', data: Buffer.from('#!/bin/sh\n'), mode: 0o755, dir: false },
    { path: 'sub/', data: Buffer.alloc(0), mode: 0o755, dir: true },
  ]
  const out1 = join(dir, '1.zip')
  const out2 = join(dir, '2.zip')
  writeZip(entries, out1, { mtimeMs: Date.UTC(2026, 0, 1) })
  writeZip(entries, out2, { mtimeMs: Date.UTC(2026, 0, 1) })
  assert.deepEqual(readFileSync(out1), readFileSync(out2))
  // 产物可被系统 unzip 校验（zip 结构合法）。Windows 上没有 python3 命令，
  // 用 `python`；ubuntu runner 两者都有。此前写死 python3 会让本机测试恒失败。
  const py = process.platform === 'win32' ? 'python' : 'python3'
  const r = spawnSync(py, ['-c', 'import zipfile,sys; assert zipfile.ZipFile(sys.argv[1]).testzip() is None', out1], { encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
})
