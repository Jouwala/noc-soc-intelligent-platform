#!/usr/bin/env node
/**
 * check-ps1-bom.mjs —— 保证所有 PowerShell 脚本是「UTF-8 + BOM」
 * ============================================================================
 *
 * 为什么需要它（2026-11-20，PR #182 实测）：
 *   .ps1 在仓库里长期是 UTF-8 **无 BOM**。PowerShell 7 能识别 UTF-8，但生产/冒烟
 *   实际跑的是 Windows PowerShell 5.1 —— 它**没有 BOM 就按系统 ANSI 代码页解码**
 *   （本机 cp936）。UTF-8 的中文注释被拆成乱码后，可能吞掉紧随其后的一个 ASCII
 *   字符（引号、#、括号），导致整份脚本语法崩掉，报错却指向几百行之外的注释。
 *
 *   2026-11-19 冒烟 PR #182 就踩了两次：
 *     1) 编辑工具把 start_all.ps1 的 BOM 剥掉 → 5.1 解析直接失败；
 *     2) 补回 BOM 后又忘了这是 PS 5.1 的硬要求。
 *   肉眼看不出来（编辑器、git、PS 7 都正常），必须在提交与 CI 两处用机器挡。
 *
 * 判定：
 *   · 每个被 git 跟踪的 *.ps1 / *.psm1 / *.psd1 必须是 **合法 UTF-8**；
 *   · 且文件开头必须是 UTF-8 BOM（EF BB BF）。
 *
 * 用法：
 *   node ops/deploy/check-ps1-bom.mjs              # 检查（CI / pre-commit）
 *   node ops/deploy/check-ps1-bom.mjs --fix        # 给合法的 UTF-8 文件补 BOM
 *   node ops/deploy/check-ps1-bom.mjs --paths a.ps1 b.ps1   # 只查指定文件（测试用）
 *
 * 退出码：0 = 全部合规；1 = 有不合规文件；2 = 脚本自身出错。
 *
 * 零第三方依赖（只用 node 内置模块），Windows / Linux 都能跑。
 */

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const BOM = Buffer.from([0xef, 0xbb, 0xbf])
const UTF8_FATAL = new TextDecoder('utf-8', { fatal: true })

const args = process.argv.slice(2)
const fix = args.includes('--fix')
const pathsAt = args.indexOf('--paths')

function listFiles() {
  // --paths 之后是文件清单；顺手滤掉 --fix 之类的开关，允许两个参数任意顺序。
  if (pathsAt >= 0) return args.slice(pathsAt + 1).filter((a) => !a.startsWith('--'))
  // -- 之后的 pathspec 交给 git；index 里已暂存的新文件也会被列出（pre-commit 用）。
  const out = execFileSync(
    'git',
    ['ls-files', '--', '*.ps1', '*.psm1', '*.psd1'],
    { encoding: 'utf8' }
  )
  return out.split(/\r?\n/).filter(Boolean)
}

let files
try {
  files = listFiles()
} catch (err) {
  console.error(`[FAIL] 取文件清单失败（不在 git 仓库里？）：${err.message}`)
  process.exit(2)
}

if (pathsAt >= 0 && files.length === 0) {
  console.error('[FAIL] --paths 后面没有给文件')
  process.exit(2)
}

const missing = []
const notUtf8 = []
const fixed = []

for (const file of files) {
  let buf
  try {
    buf = readFileSync(file)
  } catch (err) {
    console.error(`[FAIL] 读不了 ${file}：${err.message}`)
    process.exit(2)
  }

  const hasBom = buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf
  const body = hasBom ? buf.subarray(3) : buf

  try {
    UTF8_FATAL.decode(body)
  } catch {
    // 已经是非 UTF-8（GBK/ANSI）就必须人工转码：盲目加 BOM 会把原内容变成两层乱码。
    notUtf8.push(file)
    continue
  }

  if (hasBom) continue

  if (fix) {
    writeFileSync(file, Buffer.concat([BOM, buf]))
    fixed.push(file)
  } else {
    missing.push(file)
  }
}

for (const f of fixed) console.log(`[FIX] 已补 UTF-8 BOM：${f}`)

if (notUtf8.length) {
  console.error('')
  console.error(`[FAIL] ${notUtf8.length} 个 PowerShell 文件不是合法 UTF-8（多为 GBK/ANSI 旧文件）：`)
  for (const f of notUtf8) console.error(`  - ${f}`)
  console.error('       请先按 UTF-8 重新保存（保持中文内容不变），再加 BOM；--fix 不会猜编码。')
}

if (missing.length) {
  console.error('')
  console.error(`[FAIL] ${missing.length} 个 PowerShell 文件缺 UTF-8 BOM（PS 5.1 会按 ANSI 解码）：`)
  for (const f of missing) console.error(`  - ${f}`)
  console.error('')
  console.error('       修复：node ops/deploy/check-ps1-bom.mjs --fix')
  console.error('       原因见本脚本头部注释（2026-11-19 / PR #182 两次实测）。')
}

if (notUtf8.length || missing.length) process.exit(1)

console.log(`[ OK ] ${files.length} 个 PowerShell 文件均为 UTF-8 + BOM`)
