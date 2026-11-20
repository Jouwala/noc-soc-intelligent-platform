#!/usr/bin/env node
/**
 * ZIP 构建脚本（Phase 1 P1-3）：把仓库源码 + 构建产物组装成可重复构建的绿色部署包。
 *
 * 用法：
 *   node ops/deploy/build-zip.mjs [--mode full|source] [--platform win-x64|linux-x64]
 *     [--version vX.Y.Z] [--root <仓库根>] [--staging <暂存目录>] [--out <输出目录>]
 *     [--mtime <ISO 时间>] [--dry-run]
 *
 *   full 模式（默认）：要求 node_modules、前端构建产物、wheels/ 齐全（CI 先装好）；
 *   source 模式：仅源码，用于不装依赖时验证组装/断言/扫描逻辑。
 *   产物：<out>/noc-soc-platform-<版本>-<平台>.zip + 同名 .sha256。
 *   退出码：0 成功；1 用法/IO 错误；2 门禁失败（排除断言、凭据扫描、full 缺件）。
 *
 * 依据：Phase 0 报告 §6.1–§6.6；实现层见 ops/deploy/lib/zip-package.mjs，
 * ZIP 写出见 ops/deploy/lib/zip-writer.mjs。
 */

import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { writeZip } from './lib/zip-writer.mjs'
import {
  GateError, assemble, assertExclusions, assertPackageMinimal, collectEntries, gitInfo,
  isPathInside, makeBuildInfo, resolveAlembicHead, resolveMtimeMs, resolveVersion,
  scanCredentials, sha256Hex, writeSha256Sums, zipFileName, normalizeStaging,
} from './lib/zip-package.mjs'

const USAGE = `用法: node ops/deploy/build-zip.mjs [--mode full|source] [--platform win-x64|linux-x64] [--version vX.Y.Z] [--root DIR] [--staging DIR] [--out DIR] [--mtime ISO] [--dry-run]`

function parseArgs(argv) {
  const opts = { mode: 'full', dryRun: false }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--help' || arg === '-h') { console.log(USAGE); process.exit(0) }
    if (arg === '--dry-run') { opts.dryRun = true; continue }
    const m = arg.match(/^--([a-z-]+)(?:=(.*))?$/)
    if (!m) throw new Error(`未知参数: ${arg}\n${USAGE}`)
    const key = m[1]
    const value = m[2] ?? argv[++i]
    if (value === undefined) throw new Error(`参数 --${key} 缺值\n${USAGE}`)
    if (!['mode', 'platform', 'version', 'root', 'staging', 'out', 'mtime'].includes(key)) {
      throw new Error(`未知参数: --${key}\n${USAGE}`)
    }
    opts[key] = value
  }
  return opts
}

function main() {
  const opts = parseArgs(process.argv.slice(2))
  const scriptDir = dirname(fileURLToPath(import.meta.url))
  const root = resolve(opts.root ?? join(scriptDir, '..', '..'))
  if (!existsSync(join(root, 'apps/my_app/backend/main.py'))) {
    throw new Error(`仓库根无效（缺 apps/my_app/backend/main.py）: ${root}`)
  }
  const mode = opts.mode
  if (mode !== 'full' && mode !== 'source') throw new Error(`--mode 须为 full 或 source: ${mode}`)
  const platform = opts.platform ?? (process.platform === 'win32' ? 'win-x64' : 'linux-x64')
  if (platform !== 'win-x64' && platform !== 'linux-x64') throw new Error(`--platform 须为 win-x64 或 linux-x64: ${platform}`)

  const staging = resolve(opts.staging ?? join(root, 'TMP/zip-build'))
  const outDir = resolve(opts.out ?? join(root, 'TMP/zip-dist'))
  // 安全：暂存区只能在仓库内或系统临时目录内，rm -rf 绝不碰别处。
  // P0-2：用 isPathInside（path.relative 判定），旧 startsWith(`${base}/`) 在 Windows 上恒为假。
  const allowed = [root, tmpdir()]
  if (!allowed.some((base) => isPathInside(base, staging))) {
    throw new Error(`--staging 须在仓库内或系统临时目录内: ${staging}`)
  }

  const info = gitInfo(root)
  const version = resolveVersion({ version: opts.version, gitTag: info.tag, sha: info.sha })
  const mtimeMs = resolveMtimeMs({ mtime: opts.mtime, gitDate: info.date })
  const zipName = zipFileName(version, platform)

  console.log(`[build-zip] 模式=${mode} 平台=${platform} 版本=${version} 提交=${(info.sha ?? 'unknown').slice(0, 7)}`)
  rmSync(staging, { recursive: true, force: true })
  mkdirSync(staging, { recursive: true })

  const { copied, warnings } = assemble({ root, staging, mode })
  console.log(`[build-zip] 组装: ${copied.length} 个文件`)
  for (const w of warnings) console.log(`[build-zip] 警告: ${w}`)

  assertExclusions(staging)
  console.log('[build-zip] 排除断言通过')
  assertPackageMinimal(staging)
  console.log('[build-zip] 最小化门禁通过（无开发资产 / 已写 ISOLATED_COPY.flag）')
  scanCredentials(staging)
  console.log('[build-zip] 凭据扫描通过')
  const norm = normalizeStaging(staging)
  console.log(`[build-zip] 行尾规范化: ${norm.normalized} 个文件改写（.ps1 原样保留）`)

  const { head: alembicHead, warning: alembicWarning } = resolveAlembicHead(root)
  if (alembicWarning) console.log(`[build-zip] 警告: ${alembicWarning}`)
  const buildInfo = makeBuildInfo({ version, platform, sha: info.sha, mtimeMs, alembicHead, zipName })
  writeFileSync(join(staging, 'build-info.json'), `${JSON.stringify(buildInfo, null, 2)}\n`, 'utf8')
  const keyCount = writeSha256Sums(staging)
  console.log(`[build-zip] build-info.json + SHA256SUMS（${keyCount} 个关键文件）已生成`)

  if (opts.dryRun) {
    console.log(`[build-zip] 干跑：暂存区保留在 ${staging}，未生成 ZIP`)
    return
  }

  const entries = collectEntries(staging)
  mkdirSync(outDir, { recursive: true })
  const zipPath = join(outDir, zipName)
  const { bytes } = writeZip(entries, zipPath, { mtimeMs })
  const hex = sha256Hex(readFileSync(zipPath))
  writeFileSync(`${zipPath}.sha256`, `${hex}  ${zipName}\n`, 'utf8')
  console.log(`[build-zip] 产物: ${zipPath}`)
  console.log(`[build-zip] 条目=${entries.length} 大小=${(bytes / 1048576).toFixed(1)} MiB SHA256=${hex}`)
}

try {
  main()
} catch (err) {
  console.error(`错误：${err.message}`)
  process.exit(err instanceof GateError ? 2 : 1)
}
