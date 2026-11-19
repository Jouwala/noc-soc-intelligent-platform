/**
 * zip-dir.mjs 回归测试（Linux 运行时 ZIP，2026-11-19；Windows 语义补齐同日）。
 *
 * 零第三方依赖，Node 内置 test runner：node --test ops/deploy/lib/zip-dir.test.mjs
 * 覆盖：符号链接按链接条目入包（mode 0o120777，内容为目标路径；解引用会破坏
 * npm-cli.js 这类相对路径解析）、可执行位保留（解压出的 python/nginx/pg
 * 二进制必须可执行）、断链仍报错、两次打包字节一致。
 * Windows 注意：chmod 755 落不了执行位（只影响只读属性），fixture 里
 * bin/tool 的 0o755 靠 shebang 规则（见 zip-dir.mjs modeOf）；系统 unzip
 * 不还原符号链接，故「unzip 解包还原」一条在 win32 下跳过，链接条目本身
 * 的正确性由第一条用例覆盖。
 */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { chmodSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ZIP_DIR = join(HERE, 'zip-dir.mjs')

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'zip-dir-test-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  mkdirSync(join(dir, 'bin'), { recursive: true })
  writeFileSync(join(dir, 'bin', 'tool'), '#!/bin/sh\necho hi\n')
  chmodSync(join(dir, 'bin', 'tool'), 0o755) // Windows 上此 chmod 落不了执行位；0o755 靠 shebang 规则
  symlinkSync('tool', join(dir, 'bin', 'tool-link'))
  writeFileSync(join(dir, 'bin', 'run.sh'), '#!/bin/sh\necho run\n')
  chmodSync(join(dir, 'bin', 'run.sh'), 0o644)
  writeFileSync(join(dir, 'plain.txt'), 'hello\n')
  return dir
}

function pack(dir, out) {
  return spawnSync('node', [ZIP_DIR, dir, out, '--mtime', '2026-11-19T00:46:33+00:00'], { encoding: 'utf8' })
}

function inspectZip(out) {
  const script = [
    'import zipfile,sys,json',
    'z=zipfile.ZipFile(sys.argv[1])',
    'assert z.testzip() is None',
    'info={i.filename: ((i.external_attr>>16) & 0o177777) for i in z.infolist() if not i.is_dir()}',
    'print(json.dumps({"names": sorted(info), "modes": info, "link": z.read("bin/tool-link").decode(), "tool": z.read("bin/tool").decode()}))',
  ].join(';')
  const r = spawnSync('python3', ['-c', script, out], { encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
  return JSON.parse(r.stdout)
}

test('符号链接按链接条目入包，可执行位保留', (t) => {
  const dir = fixture(t)
  const out = join(dir, 'out.zip')
  const r = pack(dir, out)
  assert.equal(r.status, 0, r.stderr)
  const got = inspectZip(out)
  assert.deepEqual(got.names, ['bin/run.sh', 'bin/tool', 'bin/tool-link', 'plain.txt'])
  // 链接条目：内容是目标路径，mode 为 S_IFLNK|0777。
  assert.equal(got.link, 'tool')
  assert.equal(got.tool, '#!/bin/sh\necho hi\n')
  assert.equal(got.modes['bin/tool'], 0o755)
  assert.equal(got.modes['bin/tool-link'], 0o120777)
  assert.equal(got.modes['bin/run.sh'], 0o755)
  assert.equal(got.modes['plain.txt'], 0o644)
})

test('shebang 文件按可执行入包（无执行位时仍为 0o755）', (t) => {
  const dir = fixture(t)
  // 无后缀、无执行位、带 shebang：磁盘 mode 0o644，也须按 0o755 入包。
  // 这是 Windows 上的唯一路径（chmod 755 落不了盘），Linux 上同步验证。
  writeFileSync(join(dir, 'script'), '#!/usr/bin/env python3\nprint(1)\n')
  chmodSync(join(dir, 'script'), 0o644)
  const out = join(dir, 'out.zip')
  const r = pack(dir, out)
  assert.equal(r.status, 0, r.stderr)
  const got = inspectZip(out)
  assert.equal(got.modes['script'], 0o755)
})

// Windows 自带 unzip 不把链接条目还原成符号链接（解成普通文件），
// 还原能力不在本包控制范围；链接条目本身的正确性（mode 0o120777、
// 内容为目标路径）已由第一条用例覆盖，故本条在 win32 下跳过。
const SKIP_UNZIP_RESTORE = process.platform === 'win32'
  ? 'Windows 自带 unzip 不还原符号链接，条目元数据由第一条用例覆盖'
  : false
test('unzip 解包还原符号链接，链接可执行', { skip: SKIP_UNZIP_RESTORE }, (t) => {
  const dir = fixture(t)
  const outDir = mkdtempSync(join(tmpdir(), 'zip-dir-test-out-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  const out = join(outDir, 'out.zip')
  assert.equal(pack(dir, out).status, 0)
  const dest = join(outDir, 'extracted')
  const u = spawnSync('unzip', ['-q', out, '-d', dest], { encoding: 'utf8' })
  assert.equal(u.status, 0, u.stderr)
  const link = join(dest, 'bin', 'tool-link')
  assert.equal(lstatSync(link).isSymbolicLink(), true)
  assert.equal(readlinkSync(link), 'tool')
  assert.equal(readFileSync(link, 'utf8'), '#!/bin/sh\necho hi\n')
  const run = spawnSync(link, { encoding: 'utf8' })
  assert.equal(run.status, 0, run.stderr)
  assert.equal(run.stdout, 'hi\n')
})

test('断链符号链接仍报错', (t) => {
  const dir = fixture(t)
  symlinkSync('missing-target', join(dir, 'broken'))
  const r = pack(dir, join(dir, 'out.zip'))
  assert.notEqual(r.status, 0)
  assert.match(r.stderr, /符号链接|ENOENT/)
})

test('两次打包字节一致（确定性）', (t) => {
  const dir = fixture(t)
  const outDir = mkdtempSync(join(tmpdir(), 'zip-dir-test-out-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  const out1 = join(outDir, 'a.zip')
  const out2 = join(outDir, 'b.zip')
  assert.equal(pack(dir, out1).status, 0)
  assert.equal(pack(dir, out2).status, 0)
  const r = spawnSync('sha256sum', [out1, out2], { encoding: 'utf8' })
  const [h1, h2] = r.stdout.trim().split(/\s+/).filter((s, i) => i % 2 === 0)
  assert.equal(h1, h2)
})
