/**
 * ZIP 打包：组装、断言、扫描、规范化（build-zip.mjs 的实现层）。
 *
 * 设计依据：Phase 0 报告（docs/workspace/zip-delivery-phase0-review.md）§6.1–§6.6。
 * 清单（白名单/拒绝规则/回归文件）在 zip-manifest.mjs；改动清单须经评审。
 *
 * 零第三方依赖（Node 内置模块 + git CLI），与 agent-checks.mjs 同一约束。
 * 所有失败都带可定位的文件路径与原因；凭据扫描只报“文件:行:规则”，绝不打印命中内容。
 */

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync,
} from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import {
  APP_NAME, BASENAME_DENIED_DIRS, FULL_REQUIRED, INCLUDE_DIRS, INCLUDE_FILES,
  PACKAGE_FORBIDDEN_PATHS, PATH_DENIED_DIRS, REQUIRED_STAGING_FILES, RUNTIME_VERSIONS,
} from './zip-manifest.mjs'

export { APP_NAME, RUNTIME_VERSIONS }

/** 门禁类失败（排除断言 / 凭据扫描 / full 模式缺件）：调用方以退出码 2 区分。 */
export class GateError extends Error {
  constructor(message) {
    super(message)
    this.name = 'GateError'
    this.code = 2
  }
}

const posix = (p) => p.split('\\').join('/')

// ─────────────────────────────────────────────────────────────────────────────
// 路径包含判定（P0-2：跨平台正确；旧 startsWith(`${base}/`) 在 Windows 上恒为假）
// ─────────────────────────────────────────────────────────────────────────────

/**
 * target 是否在 base 内（含相等）。pathMod 可注入 path.win32/path.posix 做跨平台模拟。
 */
export function isPathInside(base, target, pathMod = null) {
  const resolveFn = pathMod?.resolve ?? resolve
  const relativeFn = pathMod?.relative ?? relative
  const isAbsoluteFn = pathMod?.isAbsolute ?? isAbsolute
  const separator = pathMod?.sep ?? sep
  const rel = relativeFn(resolveFn(base), resolveFn(target))
  return rel === '' || (rel !== '..' && !rel.startsWith(`..${separator}`) && !isAbsoluteFn(rel))
}

// ─────────────────────────────────────────────────────────────────────────────
// 文件名级拒绝（组装时过滤 + 断言时复核，双保险）。返回命中原因或 null。
// ─────────────────────────────────────────────────────────────────────────────

export function deniedFileReason(rel) {
  const base = rel.split('/').pop()
  if (base === 'PG_VERSION') return 'PG 数据目录标记'
  if (base === 'PRODUCTION.flag') return '生产角色标记（交付包只能是隔离副本）'
  if (base === 'AGENTS.md') return '仓库给 AI 的说明（不是产品内容）'
  if (/^\.env(\..+)?$/.test(base) && base !== '.env.example') return '.env 文件'
  if (/\.(key|pem)$/i.test(base)) return '密钥/证书文件（*.key/*.pem 一律不进包，含 CA 证书——外部 config 提供，待 P1-6 确认）'
  if (/session.*\.json$/i.test(base)) return '会话文件'
  if (base === 'auth_info.json') return '上游鉴权快照（含有效会话 cookie）'
  if (/^auth.*\.json$/i.test(base)) return '鉴权文件'
  // 凭据名源码放行（#192）：credential.py 等是 SQLAlchemy/React 源码，不是凭据数据；
  // 2026-11-20 最小化规则不分扩展名，把 credential.py、routers/credentials.py、
  // credential_store.py 等静默丢掉，Linux 包 alembic 建表直接失败。只拦非源码
  // 的凭据数据文件（credentials.json、*.credentials、无扩展名等）；源码里的真
  // 凭据内容由 scanCredentials 内容扫描兜底。
  if (/credential/i.test(base) && !/\.(py|js|mjs|cjs|ts|tsx|jsx|md)$/i.test(base)) return '凭据文件'
  if (rel.startsWith('apps/whatsapp-kb-bot/') && base === 'config.json') return 'bot 含密钥配置'
  if (/\.(log|pyc|pyo|sqlite3?|db|sql|dump)$/i.test(base)) return '日志/数据库/快照文件'
  if (/\.bak(_.*)?$|_bak_/i.test(base)) return '手工快照'
  if (/\.old(_\d+)?$/i.test(base)) return '旧版本残留'
  if (/^__wtest/i.test(base)) return '临时测试桩'
  if (base === '.DS_Store' || base === 'Thumbs.db') return '系统垃圾文件'
  if (/\.whl$/i.test(base) && !rel.startsWith('wheels/')) return 'wheels/ 之外的 wheel 包'
  if (rel.includes('/pgdata/') || rel.startsWith('pgdata/')) return 'pgdata 数据目录'
  return null
}

// ─────────────────────────────────────────────────────────────────────────────
// 遍历
// ─────────────────────────────────────────────────────────────────────────────

function pathDenied(rel, list) {
  return list.some((p) => rel === p || rel.startsWith(`${p}/`))
}

/**
 * 递归列出 dirAbs 下的全部文件（相对路径，posix 分隔）。
 * opts.pathDenied：完整相对路径拒绝表（只拦应用根级数据目录）；
 * opts.symlinkSkip：true 时跳过符号链接并记入 opts.skipped（仅 node_modules 树内用，
 * .bin 等可再生链接不进包），false 时遇符号链接即失败（防 ZIP 跨平台逃逸）。
 */
export function walkFiles(dirAbs, relBase, deniedDirs, out, opts = {}) {
  const { pathDenied: pathList = [], symlinkSkip = false, skipped = null } = opts
  for (const ent of readdirSync(dirAbs, { withFileTypes: true })) {
    const rel = relBase ? `${relBase}/${ent.name}` : ent.name
    const abs = join(dirAbs, ent.name)
    const st = lstatSync(abs)
    if (st.isSymbolicLink()) {
      if (symlinkSkip) {
        skipped?.push(rel)
        continue
      }
      throw new Error(`拒绝符号链接（ZIP 跨平台不可靠）: ${rel}`)
    }
    if (st.isDirectory()) {
      if (pathDenied(rel, pathList)) continue
      if (deniedDirs.has(ent.name) || ent.name.startsWith('.')) continue
      walkFiles(abs, rel, deniedDirs, out, opts)
    } else if (st.isFile()) {
      out.push(rel)
    }
  }
  return out
}

// ─────────────────────────────────────────────────────────────────────────────
// 组装
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 按白名单把文件复制到暂存区。返回 { copied: [{rel, bytes}], warnings: [string] }。
 * mode: full（含 node_modules/dist/wheels）或 source（仅源码，跳过构建产物）。
 */
export function assemble({ root, staging, mode }) {
  // 通用遍历拒绝集：node_modules/dist 只走显式条目（P1-1：显式 node_modules 树内
  // 不应用这两条，否则嵌套 node_modules 与包内 dist/ 会被丢掉）。
  const baseDenied = new Set(BASENAME_DENIED_DIRS)
  baseDenied.add('node_modules')
  baseDenied.add('dist')
  const nodeModulesDenied = new Set(baseDenied)
  nodeModulesDenied.delete('node_modules')
  nodeModulesDenied.delete('dist')
  const skippedSymlinks = []
  const copied = []
  const warnings = []
  const copyOne = (rel) => {
    const src = join(root, rel)
    const dst = join(staging, rel)
    const reason = deniedFileReason(rel)
    if (reason) throw new Error(`白名单命中拒绝规则（${reason}）: ${rel}——请从白名单移除或修正规则`)
    mkdirSync(dirname(dst), { recursive: true })
    copyFileSync(src, dst)
    copied.push({ rel, bytes: statSync(src).size })
  }

  for (const d of INCLUDE_DIRS) {
    if (d.requiredIn === 'full' && mode !== 'full') continue
    const abs = join(root, d.src)
    if (!existsSync(abs)) {
      if (d.requiredIn === 'full') throw new GateError(`full 模式缺构建产物目录: ${d.src}（工作流须先构建）`)
      throw new Error(`白名单目录不存在: ${d.src}`)
    }
    const isNodeModules = d.src.endsWith('/node_modules')
    const files = walkFiles(abs, d.src, isNodeModules ? nodeModulesDenied : baseDenied, [], {
      pathDenied: PATH_DENIED_DIRS,
      symlinkSkip: isNodeModules,
      skipped: skippedSymlinks,
    })
    for (const rel of files) {
      if (deniedFileReason(rel)) continue
      copyOne(rel)
    }
  }
  for (const f of INCLUDE_FILES) {
    const abs = join(root, f.src)
    if (!existsSync(abs)) {
      if (f.required) throw new Error(`白名单文件不存在: ${f.src}`)
      warnings.push(`可选文件缺失，已跳过: ${f.src}${f.note ? `（${f.note}）` : ''}`)
      continue
    }
    copyOne(f.src)
  }
  if (skippedSymlinks.length) {
    warnings.push(`跳过 node_modules 内符号链接 ${skippedSymlinks.length} 个（.bin 等可再生链接，不进包）：${skippedSymlinks.slice(0, 5).join(', ')}${skippedSymlinks.length > 5 ? '…' : ''}`)
  }

  // 回归断言（P0-1）：8 个曾被静默丢掉的运行时文件必须在暂存区存在。
  const missingRequired = REQUIRED_STAGING_FILES.filter((rel) => !existsSync(join(staging, rel)))
  if (missingRequired.length) {
    throw new GateError(`回归断言失败（白名单丢了运行时必需文件）:\n  - ${missingRequired.join('\n  - ')}`)
  }

  if (mode === 'full') {
    const missing = []
    for (const req of FULL_REQUIRED) {
      const abs = join(staging, req.rel)
      if (req.kind === 'dir' && !(existsSync(abs) && statSync(abs).isDirectory())) missing.push(`${req.rel}（目录）`)
      if (req.kind === 'file' && !existsSync(abs)) missing.push(`${req.rel}（文件）`)
      if (req.kind === 'wheels') {
        const wheels = existsSync(abs) ? readdirSync(abs).filter((n) => n.endsWith('.whl')) : []
        if (wheels.length === 0) missing.push('wheels/（须含至少一个 .whl）')
      }
    }
    if (missing.length) {
      throw new GateError(`full 模式缺件（工作流须先装依赖/构建/下载 wheels）:\n  - ${missing.join('\n  - ')}`)
    }
  }

  // ZIP 是本项目定义里的「隔离副本」：由发布包解压而来，不是生产树。
  // start.bat / start_all.ps1 靠这个标记才肯跑（两个标记都没有时一律拒绝）。
  // 以前这个标记要人工新建，漏了就「解压后双击 start.bat 报缺标志」——现在随包写。
  writeCopyMarker(staging)
  return { copied, warnings }
}

/** 交付包的隔离副本标记（固定字节，便于可重复构建）。
 * 刻意用 ASCII：PS 5.1 的 Get-Content 默认按 ANSI 读无 BOM 的 UTF-8，
 * 中文会显示成乱码；cmd 同样不保证 UTF-8。这个文件只被 Test-Path 判断，
 * 但运维会打开看，所以写成英文最稳。
 */
export const COPY_MARKER_FILE = 'ISOLATED_COPY.flag'
const COPY_MARKER_TEXT = [
  'ISOLATED COPY - written by ops/deploy/build-zip.mjs, do not edit by hand.',
  '',
  'This directory is a released package, not the production tree.',
  'start.bat / stop.bat run ops/deploy/start_all.ps1 with isolated ports',
  '(8080/8443/7039/7091/9277/55432) and never touch production 80/443.',
  'A production tree uses PRODUCTION.flag instead. If both or neither',
  'marker exists, the launchers refuse to start.',
  '',
].join('\r\n')


// ─────────────────────────────────────────────────────────────────────────────
// 排除断言（组装后复核：白名单过滤失效时也能拦住）
// ─────────────────────────────────────────────────────────────────────────────

/** 写隔离副本标记；已存在时保留原内容（可重复构建要求固定字节，故内容不含时间）。 */
export function writeCopyMarker(staging) {
  const abs = join(staging, COPY_MARKER_FILE)
  if (!existsSync(abs)) writeFileSync(abs, COPY_MARKER_TEXT, 'utf8')
}

/**
 * 最小化门禁：包内不得出现开发资产（CI、agent 笔记、测试、手册、源码树、
 * 运行期缓存……）。白名单写错或有人图省事把整棵工作区塞进来时，这里直接失败。
 * 与 assertExclusions 的分工见 zip-manifest.mjs 的 PACKAGE_FORBIDDEN_PATHS 注释。
 */
export function assertPackageMinimal(staging) {
  const violations = []
  // ⚠ 不能用 walkFiles：它会跳过 . 开头的目录，而最该拦的 .github / .claude /
  // .githooks / .agent-work 恰好都是隐藏目录。目录级前缀直接 existsSync 判。
  for (const rule of PACKAGE_FORBIDDEN_PATHS) {
    const abs = join(staging, rule.prefix.replace(/\/$/, ''))
    if (existsSync(abs)) violations.push(`${rule.prefix}（${rule.reason}）`)
  }
  // 文件级兜底：非目录前缀（目前没有）与未来新增的条目仍走一遍遍历。
  const all = walkFiles(staging, '', new Set(), [])
  for (const rel of all) {
    for (const rule of PACKAGE_FORBIDDEN_PATHS) {
      if (rule.prefix.endsWith('/') || rule.rootOnly) continue // 目录/根级文件规则已由上面的 existsSync 覆盖
      // 文件规则同时匹配根目录与任意嵌套（如 apps/my_app/AGENTS.md）。
      if (rel === rule.prefix || rel.endsWith(`/${rule.prefix}`) || rel.startsWith(`${rule.prefix}/`)) {
        violations.push(`${rel}（${rule.reason}）`)
        break
      }
    }
  }
  // 角色标记必须恰好是「隔离副本」：生产标记出现即说明打错了树。
  if (existsSync(join(staging, 'PRODUCTION.flag'))) violations.push('PRODUCTION.flag（生产标记不许进交付包）')
  if (!existsSync(join(staging, COPY_MARKER_FILE))) violations.push(`${COPY_MARKER_FILE}（缺失，解压后 start.bat 会拒绝运行）`)
  if (violations.length) {
    const shown = violations.slice(0, 20)
    throw new GateError(`最小化门禁失败（${violations.length} 项不该进包）：\n  - ${shown.join('\n  - ')}${violations.length > shown.length ? '\n  …' : ''}`)
  }
}

export function assertExclusions(staging) {
  const violations = []
  const all = walkFiles(staging, '', new Set(), [])
  for (const rel of all) {
    const reason = deniedFileReason(rel)
    if (reason) violations.push(`${rel}（${reason}）`)
    if (rel.split('/').includes('pgdata')) violations.push(`${rel}（pgdata 路径）`)
  }
  // 目录级：pgdata 目录即使为空也不许出现。
  const stack = [staging]
  while (stack.length) {
    const dir = stack.pop()
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      if (!ent.isDirectory()) continue
      if (ent.name === 'pgdata') violations.push(`${posix(join(dir, ent.name)).slice(staging.length + 1)}/（pgdata 目录）`)
      stack.push(join(dir, ent.name))
    }
  }
  if (violations.length) {
    throw new GateError(`排除断言失败（${violations.length} 项）：\n  - ${violations.slice(0, 20).join('\n  - ')}`)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 凭据扫描（只报文件:行:规则，绝不打印命中内容）
// ─────────────────────────────────────────────────────────────────────────────

const PLACEHOLDER_VALUES = new Set(['', 'xxx', 'xxxx', 'changeme', 'example', 'placeholder', 'test', 'dummy', 'todo'])
const isPlaceholder = (v) => {
  const t = v.toLowerCase()
  return PLACEHOLDER_VALUES.has(t) || t.startsWith('your-') || t.startsWith('your_') || t.startsWith('<') || t.includes('example')
}

const SCAN_RULES_STRICT = [
  { name: '私钥块', re: /-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/ },
  { name: 'JSESSIONID 会话值', re: /JSESSIONID\s*=\s*[A-Za-z0-9]{16,}/ },
  { name: 'API 密钥赋值', re: /\b(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['"]([A-Za-z0-9_\-./+=]{16,})['"]/i, valueGroup: 2 },
  { name: '口令赋值', re: /\bpassword\s*[:=]\s*['"]([^\s'"]{8,})['"]/i, valueGroup: 1 },
]
const SCAN_RULES_LOOSE = SCAN_RULES_STRICT.slice(0, 2) // dist/_fe_dist 只查前两条（避免压缩产物误报）；node_modules 不扫，见 scanCredentials

function isBinary(buf) {
  return buf.subarray(0, 8192).includes(0)
}

export function scanCredentials(staging) {
  const hits = []
  const all = walkFiles(staging, '', new Set(), [])
  for (const rel of all) {
    // node_modules 是第三方包（由 lockfile 复现），其文档里的示例密钥块
    // （如 dotenv README）不是项目泄漏；扫它只会误报。供应链安全靠
    // lockfile + npm audit，不靠内容扫描。P1-2 称重实测发现。
    if (rel.includes('/node_modules/') || rel.startsWith('node_modules/')) continue
    const loose = rel.includes('/dist/') || rel.startsWith('apps/_fe_dist/')
    const buf = readFileSync(join(staging, rel))
    if (isBinary(buf)) continue
    const rules = loose ? SCAN_RULES_LOOSE : SCAN_RULES_STRICT
    const lines = buf.toString('utf8').split('\n')
    lines.forEach((line, idx) => {
      for (const rule of rules) {
        const m = line.match(rule.re)
        if (!m) continue
        if (rule.valueGroup && isPlaceholder(m[rule.valueGroup])) continue
        hits.push(`${rel}:${idx + 1}（${rule.name}）`)
        break
      }
    })
    if (hits.length >= 20) break
  }
  if (hits.length) {
    throw new GateError(`凭据扫描命中 ${hits.length} 处（只列位置，内容已隐去；误报请人工复核后收窄规则）：\n  - ${hits.join('\n  - ')}`)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 行尾规范化（.ps1 原样保留——字节级要求，见 .gitattributes 头注释）
// ─────────────────────────────────────────────────────────────────────────────

const TEXT_EXTS = new Set(['.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.json', '.md', '.yml', '.yaml', '.toml', '.txt', '.py', '.pyi', '.html', '.htm', '.css', '.scss', '.xml', '.conf', '.template', '.sh', '.bat', '.cmd', '.ini', '.cfg', '.csv'])

export function normalizeStaging(staging) {
  let normalized = 0
  let skipped = 0
  const all = walkFiles(staging, '', new Set(), [])
  for (const rel of all) {
    const ext = rel.slice(rel.lastIndexOf('.')).toLowerCase()
    if (ext === '.ps1' || ext === '.psm1') { skipped += 1; continue } // 字节保留
    if (!TEXT_EXTS.has(ext)) { skipped += 1; continue }
    const abs = join(staging, rel)
    const buf = readFileSync(abs)
    if (isBinary(buf)) { skipped += 1; continue }
    let body = buf.toString('utf8')
    let bom = ''
    if (body.charCodeAt(0) === 0xfeff) { bom = '\ufeff'; body = body.slice(1) }
    const lf = body.replace(/\r\n?/g, '\n')
    const out = ext === '.bat' || ext === '.cmd' ? lf.replace(/\n/g, '\r\n') : lf
    if (bom + out !== buf.toString('utf8')) {
      writeFileSync(abs, bom + out, 'utf8')
      normalized += 1
    }
  }
  return { normalized, skipped }
}

// ─────────────────────────────────────────────────────────────────────────────
// Git 信息与 build-info
// ─────────────────────────────────────────────────────────────────────────────

function git(root, args) {
  const r = spawnSync('git', args, { cwd: root, encoding: 'utf8' })
  return r.status === 0 ? r.stdout.trim() : null
}

export function gitInfo(root) {
  const sha = git(root, ['rev-parse', 'HEAD'])
  const date = git(root, ['log', '-1', '--format=%cI'])
  const tag = git(root, ['describe', '--tags', '--exact-match', 'HEAD'])
  return { sha, date, tag }
}

export function resolveVersion({ version, gitTag, sha }) {
  if (version) return version
  if (gitTag) return gitTag
  return `0.0.0-dev-${(sha ?? 'unknown').slice(0, 7)}`
}

export function resolveMtimeMs({ mtime, gitDate }) {
  if (mtime) {
    const ms = Date.parse(mtime)
    if (Number.isNaN(ms)) throw new Error(`--mtime 无法解析: ${mtime}`)
    return ms
  }
  if (gitDate) {
    const ms = Date.parse(gitDate)
    if (!Number.isNaN(ms)) return ms
  }
  return Date.UTC(1980, 0, 1) // ZIP 时间戳下限；无 git 信息时保证可重复
}

/** 解析 alembic 版本链，找“从未当过 down_revision”的头（多头/无头返回 null 并警告）。 */
export function resolveAlembicHead(root) {
  const dir = join(root, 'apps/my_app/alembic/versions')
  if (!existsSync(dir)) return { head: null, warning: 'alembic versions 目录不存在' }
  const revisions = new Set()
  const downRevisions = new Set()
  for (const name of readdirSync(dir).filter((n) => n.endsWith('.py'))) {
    const text = readFileSync(join(dir, name), 'utf8')
    for (const line of text.split('\n')) {
      const rev = line.match(/^\s*revision\s*(?::[^=]*)?=\s*['"]([^'"]+)['"]/)
      if (rev) revisions.add(rev[1])
      const down = line.match(/^\s*down_revision\s*(?::[^=]*)?=\s*(.+)$/)
      if (down) {
        for (const m of down[1].matchAll(/['"]([^'"]+)['"]/g)) downRevisions.add(m[1])
      }
    }
  }
  const heads = [...revisions].filter((r) => !downRevisions.has(r))
  if (heads.length === 1) return { head: heads[0], warning: null }
  return { head: null, warning: `alembic 头不唯一（${heads.length} 个：${heads.join(', ') || '无'}），build-info 置空` }
}

export function zipFileName(version, platform) {
  if (!/^[A-Za-z0-9._-]+$/.test(version)) throw new Error(`版本号含非法字符: ${version}`)
  return `noc-soc-platform-${version}-${platform}.zip`
}

/**
 * build-info.json（Phase 0 报告 §6.2 模式）。
 * built_at 取源码提交时间（即统一 mtime），而非墙上时钟——否则两次构建字节不同，
 * “可重复构建”不成立。此语义已在 zip-build-release.md 中写明。
 */
export function makeBuildInfo({ version, platform, sha, mtimeMs, alembicHead, zipName }) {
  return {
    name: APP_NAME,
    version,
    commit: sha ?? 'unknown',
    commit_short: (sha ?? 'unknown').slice(0, 7),
    built_at: new Date(mtimeMs).toISOString(),
    platform,
    copy_role: 'isolated',
    install_doc: '安装与运行说明.md',
    runtime: RUNTIME_VERSIONS,
    alembic_head: alembicHead,
    artifacts: [zipName],
  }
}

const KEY_FILES = [
  'build-info.json', 'ISOLATED_COPY.flag', '安装与运行说明.md',
  'start.bat', 'stop.bat', 'start.sh', 'stop.sh',
  'nginx.conf.template', 'ops/deploy/start_all.ps1', 'ops/deploy/stop_all.ps1',
  'apps/my_app/frontend/vite.config.ts',
  'apps/_fe_dist/index.html', 'apps/portal/dist/index.html',
]

export function writeSha256Sums(staging) {
  const lines = []
  for (const rel of KEY_FILES) {
    const abs = join(staging, rel)
    if (!existsSync(abs)) continue
    const hex = createHash('sha256').update(readFileSync(abs)).digest('hex')
    lines.push(`${hex}  ${rel}`)
  }
  const content = `${lines.join('\n')}\n`
  writeFileSync(join(staging, 'SHA256SUMS'), content, 'utf8')
  return lines.length
}

// ─────────────────────────────────────────────────────────────────────────────
// 收集 ZIP 条目（排序 + 权限位；目录也要入条目，保证空目录结构）
// ─────────────────────────────────────────────────────────────────────────────

export function collectEntries(staging) {
  const entries = []
  const walk = (dirAbs, relBase) => {
    for (const ent of readdirSync(dirAbs, { withFileTypes: true })) {
      const rel = relBase ? `${relBase}/${ent.name}` : ent.name
      const abs = join(dirAbs, ent.name)
      const st = lstatSync(abs)
      if (st.isSymbolicLink()) throw new Error(`暂存区出现符号链接: ${rel}`)
      if (st.isDirectory()) {
        entries.push({ path: `${rel}/`, data: Buffer.alloc(0), mode: 0o755, dir: true })
        walk(abs, rel)
      } else if (st.isFile()) {
        entries.push({ path: rel, data: readFileSync(abs), mode: rel.endsWith('.sh') ? 0o755 : 0o644, dir: false })
      }
    }
  }
  walk(staging, '')
  entries.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
  return entries
}

export function sha256Hex(buf) {
  return createHash('sha256').update(buf).digest('hex')
}
