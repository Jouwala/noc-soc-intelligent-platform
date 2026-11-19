#!/usr/bin/env node
/**
 * 通用目录打包：把一个目录原样打成 ZIP（供 runtime-build.yml 打运行时包）。
 *
 * 用法：node ops/deploy/lib/zip-dir.mjs <目录> <输出.zip> [--mtime <ISO 时间>]
 * 复用 zip-writer.mjs：固定时间戳、UTF-8 文件名、.sh 可执行位。目录本身不入包，
 * 只含其下的内容。
 *
 * 符号链接：按 ZIP 规范以链接条目入包（mode 0o120777，内容为目标路径），
 * 不解引用——运行时组件（node 的 npm/npx/corepack、python 的 bin/python3
 * 与 libpython3.12.so 等）天生带大量符号链接；解引用会破坏相对路径解析
 * （如 npm-cli.js 用 require('../lib/cli.js')，存成 bin/npm 后路径失效，
 * 2026-11-19 实测）。目标是目录或断链时仍报错。文件权限位按磁盘保留
 * （有任一执行位 → 0o755，否则 0o644；.sh 恒为 0o755；带 shebang（#!）
 * 的文件也按 0o755——Windows 没有 POSIX 执行位，chmod 755 落不了盘、
 * stat 的执行位恒为 0，不认 shebang 会把脚本打成 0o644，release-build
 * Run 44561485336 win-x64 即因此失败），否则解压出的
 * python/nginx/pg 二进制不可执行。注意：Python zipfile 解包不还原符号
 * 链接（会解成含目标路径的普通文件），须用 unzip / bsdtar 解包。
 */

import { lstatSync, mkdirSync, readFileSync, readdirSync, readlinkSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { writeZip } from './zip-writer.mjs'

function modeOf(st, rel, data) {
  if (rel.endsWith('.sh')) return 0o755
  if ((st.mode & 0o111) !== 0) return 0o755
  // Windows 没有 POSIX 执行位：chmod 755 落不了盘，stat 的执行位恒为 0。
  // 带 shebang（#!）的是可执行脚本，按 0o755 入包，否则 Windows 打出
  // 的包解压后脚本不可执行（release-build Run 44561485336 win-x64 失败）。
  if (data && data.length >= 2 && data[0] === 0x23 && data[1] === 0x21) return 0o755
  return 0o644
}

function main() {
  const args = process.argv.slice(2)
  const mtimeIdx = args.indexOf('--mtime')
  let mtimeMs = Date.UTC(1980, 0, 1)
  if (mtimeIdx !== -1) {
    const ms = Date.parse(args[mtimeIdx + 1])
    if (Number.isNaN(ms)) throw new Error(`--mtime 无法解析: ${args[mtimeIdx + 1]}`)
    mtimeMs = ms
    args.splice(mtimeIdx, 2)
  }
  const [dir, out] = args
  if (!dir || !out) throw new Error('用法: node ops/deploy/lib/zip-dir.mjs <目录> <输出.zip> [--mtime ISO]')
  const root = resolve(dir)
  const outPath = resolve(out)

  const entries = []
  const walk = (dirAbs, relBase) => {
    for (const ent of readdirSync(dirAbs, { withFileTypes: true })) {
      const rel = relBase ? `${relBase}/${ent.name}` : ent.name
      const abs = join(dirAbs, ent.name)
      const st = lstatSync(abs)
      if (st.isSymbolicLink()) {
        // 链接条目：目标必须是文件（断链/目录目标仍报错）。
        const target = statSync(abs)
        if (!target.isFile()) throw new Error(`拒绝符号链接（目标非文件）: ${rel}`)
        entries.push({ path: rel, data: Buffer.from(readlinkSync(abs), 'utf8'), mode: 0o120777, dir: false })
      } else if (st.isDirectory()) {
        entries.push({ path: `${rel}/`, data: Buffer.alloc(0), mode: 0o755, dir: true })
        walk(abs, rel)
      } else if (st.isFile()) {
        const data = readFileSync(abs)
        entries.push({ path: rel, data, mode: modeOf(st, rel, data), dir: false })
      }
    }
  }
  walk(root, '')
  mkdirSync(dirname(outPath), { recursive: true })
  const { entries: count, bytes } = writeZip(entries, outPath, { mtimeMs })
  console.log(`[zip-dir] ${outPath}：${count} 个条目，${(bytes / 1048576).toFixed(1)} MiB`)
}

try {
  main()
} catch (err) {
  console.error(`错误：${err.message}`)
  process.exit(1)
}
