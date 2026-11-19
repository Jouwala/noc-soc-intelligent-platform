/**
 * AuthStore 播种初始管理员（2026-11-19 发布包交付）回归测试。
 *
 * 覆盖三件事：
 *  1. 全新数据目录 → 播种 admin，密码是 DEFAULT_INITIAL_ADMIN_PASSWORD，强制改密；
 *  2. PORTAL_INITIAL_ADMIN_PASSWORD 覆盖默认值（交付方自定义初始密码）；
 *  3. 已有账号库 → 绝不重新播种（默认密码不覆盖既有账号）。
 *
 * 运行：npm test（已登记到 apps/portal/package.json）。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test';
import { fileURLToPath } from 'node:url'
import { AuthStore, DEFAULT_INITIAL_ADMIN_PASSWORD } from './store'

function tempDir(_t: { after: (fn: () => void) => void }): string {
  // 不删：node:sqlite 的连接在进程内没有显式 close()，Windows 上会占着 db/WAL
  // 句柄，rmSync 抛 EPERM；而且 authDb 有一个延迟备份定时器，目录先被删会打
  // 「备份失败」噪声。目录名唯一、落在系统临时目录，交给 OS 清理即可。
  return mkdtempSync(join(tmpdir(), 'portal-auth-store-'))
}

test('全新数据目录播种 admin：默认密码 + 强制改密 + 密码文件', (t) => {
  const dir = tempDir(t)
  delete process.env.PORTAL_INITIAL_ADMIN_PASSWORD
  const store = new AuthStore(dir)
  store.ensureLoaded()

  const users = store.listUsers()
  assert.equal(users.length, 1)
  assert.equal(users[0].username, 'admin')
  assert.equal(users[0].role, 'admin')
  assert.equal(users[0].mustChangePassword, true)

  const admin = store.verifyCredentials('admin', DEFAULT_INITIAL_ADMIN_PASSWORD)
  assert.ok(admin, '默认密码必须能登录（首次进入的跳板）')
  assert.equal(store.verifyCredentials('admin', 'wrong-password'), null)

  const pwdFile = join(dir, 'initial-admin-password.txt')
  assert.ok(existsSync(pwdFile), '应写出初始密码文件，供离线部署核对')
  assert.match(readFileSync(pwdFile, 'utf8'), new RegExp(DEFAULT_INITIAL_ADMIN_PASSWORD.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
})

test('PORTAL_INITIAL_ADMIN_PASSWORD 覆盖默认值', (t) => {
  const dir = tempDir(t)
  const custom = 'Deliver-Only-2026'
  process.env.PORTAL_INITIAL_ADMIN_PASSWORD = custom
  t.after(() => { delete process.env.PORTAL_INITIAL_ADMIN_PASSWORD })

  const store = new AuthStore(dir)
  store.ensureLoaded()
  assert.ok(store.verifyCredentials('admin', custom), '应使用 .env 指定的初始密码')
  assert.equal(store.verifyCredentials('admin', DEFAULT_INITIAL_ADMIN_PASSWORD), null)
})

test('过短的覆盖值被忽略，回落默认密码', (t) => {
  const dir = tempDir(t)
  process.env.PORTAL_INITIAL_ADMIN_PASSWORD = '123'
  t.after(() => { delete process.env.PORTAL_INITIAL_ADMIN_PASSWORD })

  const store = new AuthStore(dir)
  store.ensureLoaded()
  assert.ok(store.verifyCredentials('admin', DEFAULT_INITIAL_ADMIN_PASSWORD))
})

test('已有账号库不重新播种：默认密码不会覆盖既有账号', (t) => {
  const dir = tempDir(t)
  delete process.env.PORTAL_INITIAL_ADMIN_PASSWORD
  const first = new AuthStore(dir)
  first.ensureLoaded()
  first.setPassword(first.listUsers()[0].id, 'Changed-By-Operator-1', false)

  // 同一数据目录再起一个 store（模拟重启）：不再播种，旧密码仍生效
  process.env.PORTAL_INITIAL_ADMIN_PASSWORD = 'ShouldNotApply-2026'
  t.after(() => { delete process.env.PORTAL_INITIAL_ADMIN_PASSWORD })
  const second = new AuthStore(dir)
  second.ensureLoaded()
  assert.equal(second.listUsers().length, 1)
  assert.ok(second.verifyCredentials('admin', 'Changed-By-Operator-1'))
  assert.equal(second.verifyCredentials('admin', 'ShouldNotApply-2026'), null)
  assert.equal(second.verifyCredentials('admin', DEFAULT_INITIAL_ADMIN_PASSWORD), null)
})

test('交付文档与代码的默认密码保持一致', () => {
  // 文档在仓库根、随应用包放在 ZIP 根；代码改了密码却忘了改文档 = 用户按文档登不进去。
  const doc = fileURLToPath(new URL('../../../../安装与运行说明.md', import.meta.url))
  assert.ok(existsSync(doc), `找不到安装说明: ${doc}`)
  assert.ok(
    readFileSync(doc, 'utf8').includes(DEFAULT_INITIAL_ADMIN_PASSWORD),
    `《安装与运行说明.md》未写出默认密码 ${DEFAULT_INITIAL_ADMIN_PASSWORD}`
  )
})

