#!/usr/bin/env node
/**
 * 安装便携副本的 git 钩子：把 `core.hooksPath` 指向仓库内的 `.githooks/`。
 *
 * 为什么需要这个（2026-11-10）：
 *   本仓库的 `.githooks/post-checkout` 与 `.githooks/post-merge` 负责在检出 /
 *   合并之后把两个脚本里的**本机绝对路径**改成本机副本根目录：
 *       apps/my_app/monitor_fixes/nginx_monitor.ps1  (NginxExe/NginxDir/Port/Log/State)
 *       <门户>\rotate_nginx_logs.ps1                   ($Prefix)
 *
 *   这两个文件是**入库**的，而路径必须因机器而异 —— 所以换一台机器 clone 后，
 *   必须有个自动动作把它们改对，否则 nginx watchdog 会去探测一个不存在的目录。
 *
 *   `.git/hooks/` 不随仓库分发，Git 官方的分发方式是 `core.hooksPath`。
 *   它本身是**本地配置**（写在 .git/config，不入库），所以每台机器跑一次本脚本。
 *
 * 用法：
 *   node scripts/setup-git-hooks.mjs           # 安装
 *   node scripts/setup-git-hooks.mjs --check   # 只检查，不改配置
 *   node scripts/setup-git-hooks.mjs --uninstall
 *
 * 无第三方依赖（只用 Node 内置模块 + git CLI）。
 */

import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const HOOKS_DIR = '.githooks'

/** git 一律在调用者当前目录执行 —— worktree 里当前工作树根才是 Git 找钩子的地方 */
function git(args) {
  return spawnSync('git', args, { cwd: process.cwd(), encoding: 'utf8', shell: false })
}

function workTreeRoot() {
  const r = git(['rev-parse', '--show-toplevel'])
  return r.status === 0 ? r.stdout.trim() : ROOT
}

function currentHooksPath() {
  const r = git(['config', '--get', 'core.hooksPath'])
  return r.status === 0 ? r.stdout.trim() : ''
}

function hookFiles(root) {
  const dir = join(root, HOOKS_DIR)
  if (!existsSync(dir)) return []
  return readdirSync(dir).filter((f) => !f.endsWith('.sample'))
}

function main() {
  const args = process.argv.slice(2)
  const mode = args.includes('--uninstall')
    ? 'uninstall'
    : args.includes('--check')
      ? 'check'
      : 'install'

  const root = workTreeRoot()
  const hooksDir = join(root, HOOKS_DIR)
  const files = hookFiles(root)
  const current = currentHooksPath()

  if (mode === 'check') {
    const ok = current === HOOKS_DIR && files.length > 0
    if (ok) {
      console.log(`✓ 钩子已启用（core.hooksPath = ${HOOKS_DIR}）`)
      console.log(`  钩子文件 = ${relative(root, hooksDir)}: ${files.join(', ')}`)
      return 0
    }
    if (current !== HOOKS_DIR) {
      console.log(
        current
          ? `✗ core.hooksPath = ${current}，不是 ${HOOKS_DIR}`
          : '✗ core.hooksPath 未设置 —— 钩子未启用',
      )
    } else {
      console.log(`✗ core.hooksPath = ${HOOKS_DIR}，但工作树里没有 ${HOOKS_DIR}\\`)
    }
    console.log('  修复：node scripts/setup-git-hooks.mjs')
    return 1
  }

  if (mode === 'uninstall') {
    const r = git(['config', '--unset', 'core.hooksPath'])
    if (r.status !== 0) {
      console.log('core.hooksPath 本来就没设置，无需卸载。')
      return 0
    }
    console.log('✓ 已卸载 core.hooksPath')
    return 0
  }

  // install
  if (!files.length) {
    console.error(`✗ 找不到 ${relative(root, hooksDir)} —— 仓库不完整？`)
    return 2
  }

  const r = git(['config', 'core.hooksPath', HOOKS_DIR])
  if (r.status !== 0) {
    console.error(`✗ 设置 core.hooksPath 失败：${(r.stderr ?? '').trim()}`)
    return 1
  }

  // Git for Windows 靠 shebang 识别文件类型，+x 位仍尽量设置；
  // core.fileMode 为 false 时由 Git 自行处理，Windows 上没有 chmod 也不是错误。
  for (const f of files) {
    spawnSync('chmod', ['+x', join(hooksDir, f)], { shell: false })
  }

  console.log('✓ 钩子已启用')
  console.log(`  core.hooksPath = ${HOOKS_DIR}`)
  console.log(`  钩子文件       = ${relative(root, hooksDir)}: ${files.join(', ')}`)
  console.log('')
  console.log('  作用：checkout / merge 之后自动把两个脚本里的本机绝对路径')
  console.log('        改成本机副本根目录（逐字节改写，保持 BOM 与行尾不变）。')
  console.log('        手动执行：powershell -NoProfile -File scripts\\post_sync_fix_paths.ps1 -Force')
  return 0
}

process.exit(main())
