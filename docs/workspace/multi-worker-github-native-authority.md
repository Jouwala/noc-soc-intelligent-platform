# GitHub 原生权威层设计（ADR-7）—— 多 VM Worker 的认领/租约/fencing/范围锁

> 状态：**设计冻结，待实现**（owner 2026-11-14 选择「GitHub 原生 ref-CAS，不搞中央服务」）
> 前置：ADR-2026-11-14（`multi-worker-scheduler-design.md`）—— 其中 SQLite 调度器是**单机**实现；
> 生产拓扑为**多台 VM 各自跑 Worker**，跨机认领/verifying 不互斥，部署门槛见该文第十三节。
> 本文件只描述「权威层换成 GitHub 原子原语」后的设计，Worker 其余修复（心跳/清理/取消/进程树/
> 分页/命令解析）全部保留，`scheduler/api.py` 的 `Scheduler` 协议**不变**。

## 一、问题

SQLite 调度器靠「同一数据库文件的 BEGIN IMMEDIATE + 唯一约束」实现互斥。多台 VM 上每台各有一个
DB 文件，于是两台 VM 可同时 `claim` 同一任务成功、各自执行——这违反任务书 §一.1（同一任务任何
时刻只有一个有效执行者）。

不能引入中央服务（owner 决定），因此需要把「互斥」建立在**GitHub 服务端本身提供的原子操作**上。

## 二、可用的原子原语

GitHub 没有事务，但有**服务端强制的唯一性**：

| 原语 | 语义 | 原子性 |
| --- | --- | --- |
| `POST /repos/{o}/{r}/git/refs` 创建 ref | ref 名在仓库内唯一；已存在返回 422 | ✅ 创建成功 = 全局唯一赢家（服务器裁决，不是检查后写入） |
| `GET /git/matching-refs/heads/<prefix>` | 列出前缀下所有 ref | 读到的已创建 ref 立即可见（同仓库强一致） |
| `DELETE /git/refs/heads/<name>` | 删除 ref；不存在返回 422/404 | ✅ 可幂等处理 |
| Issue 评论 / 标签 | 最终一致、无并发控制 | ❌ 只作状态展示与审计，不作锁 |

分派幂等（PR #52 的 `dispatch_registry.py`）已经用同一原语落地并验证，本方案与其一致。

## 三、Ref 命名与 fencing（核心设计）

### 3.1 单调代次（High-Water Mark）

每任务一条**只增不减**的 HWM ref：

```
refs/heads/agent-hwm/issue-<N>/<token:08d>
```

- `token` 从 1 开始，认领/续租都先创建 **下一个** token 的 HWM ref；
- 创建成功 = 本次是唯一赢家；422 = 已有更高代次，按冲突处理；
- HWM ref **永不删除**（保证 token 单调，不会因回收而重用）；Reaper 只允许删除 `< 当前最大 token`
  的旧 HWM（可选优化）。

### 3.2 租约

```
refs/heads/agent-lease/issue-<N>/<token:08d>/<epoch_ms>/<agent>/<instance>/<attempt>
```

- `epoch_ms`：认领/续租时刻（毫秒 UTC），**租约是否存活只看它**：
  `now - epoch_ms < lease_ttl`；
- `agent`/`instance`/`attempt` 必须是 ref 安全字符（agent `[a-z0-9-]`、instance/attempt 十六进制）；
- 同一 token 可能有多条租约 ref（续租产生新 token 后旧 ref 可删）；**有效租约 = token 最大且未过期的那条**。

### 3.3 认领（claim）

1. `list(agent-hwm/issue-N/)` → 当前最大 token `T`；
2. `list(agent-lease/issue-N/)` → 找最大 token 的租约，判断是否存活：
   - 存在存活租约且持有者不是本实例 → `LeaseHeld`；
   - 否则（无租约 / 已过期 / 同实例）→ 进入第 3 步；
3. `create(agent-hwm/issue-N/T+1)`：
   - 201：本实例是唯一赢家 → `create(lease/.../T+1/<now_ms>/...)` → 发认领评论（审计）→ 返回 `Lease(token=T+1)`；
   - 422：并发失败者 → 重新 list 一次（拿到新赢家的租约）→ 存活则 `LeaseHeld`，已过期则从 T+1 重试；
4. 重试上限（默认 3）后抛 `LeaseHeld`（让 Worker 下一轮再试）。

### 3.4 续租（renew）与代次推进

Worker 执行期续租 = **推进到下一个 token**：

1. 校验自己当前 token = HWM 最大值（否则 `StaleFencing`）；
2. `create(agent-hwm/issue-N/<token+1>)`（CAS；422 → `StaleFencing`，说明别人已推进）；
3. `create(agent-lease/.../<token+1>/<now_ms>/...)`，成功后删除自己的旧 lease ref（可选）；
4. 返回新 `Lease(token=token+1)`；**Worker 必须用返回值更新 `active.fencing_token`**
   （`worker.py`/`task_lifecycle.renew` 需做这一行小改动）。

这样租约存活判断始终只需读 ref，不需要读评论；评论只是审计与人类可读状态。

### 3.5 旧执行者防护（fencing）

任何写操作（续租、状态迁移、完成、释放、取消、释放范围锁）执行前：

```
max_token(list(agent-hwm/issue-N/)) == my_token  ? 继续 : StaleFencing
且 get(agent-lease/issue-N/my_token/...) 存在      ? 继续 : StaleFencing
```

旧执行者的 token 必然小于新赢家，因此**永久**无法通过校验；即使它绕过 Worker 直接调 API，
其写出的评论/标签也不带最高 token，读者可按「token 必须等于当前最高 token」判为无效
（对账/reaper 会修正展示标签）。

### 3.6 完成幂等（complete）

1. 执行 3.5 的代次校验；
2. 在 Issue 发一条完成评论，正文包含唯一标记：
   `<!-- ref-completion: issue=<N>, token=<T>, attempt=<A>, status=<terminal>, idempotency=<key> -->`
   发之前先分页扫评论，已有同 `idempotency` 标记 → 视为重放，不重复发；
3. 标签收敛（`state:in-review` / `state:blocked` / `state:todo`）由 Worker 的 mirror 逻辑在
   **代次校验通过后**写入；并发窗口导致的标签错误由 Reaper/对账修正（展示层最终一致）。

### 3.7 租约过期回收（Reaper）

GitHub Actions **cron workflow**（每 5 分钟，`workflow_dispatch` 可手动）运行
`apps/control-plane/backend/dispatch/lease_reaper.py`：

- `list(agent-lease/)` → 按 ref 名解析 epoch → 过期则删除 lease ref（HWM 保留，token 不回退）；
- 删除孤儿 scope ref（所属任务无存活租约且无活动）；
- 可选：按最高 token 修正 `state:*` 标签；
- 幂等、可重复执行；失败显式报错，不静默。

## 四、范围锁（scope）

命名（每个 scope 路径一条 ref，路径按层级展开，保证前缀 list 能发现祖先/后代重叠）：

```
refs/heads/agent-scope/<encoded-path>/<task_id>-<token:08d>
```

- 路径规范化沿用 `scheduler/scopes.py`（反斜杠、`.`/`..`、越界/绝对路径拒绝、大小写策略）；
- 每段路径用 ref 安全字符编码（不安全字符 → `_<hex>`），保留 `/` 层级；
- **获取**（创建后校验，冲突即回退重试）：
  1. 代次校验（3.5）；
  2. 为每个 scope 创建自己的 ref（名字含 task+token，天然不与他人冲突）；
  3. `list` 自己的 path 前缀 + 所有祖先前缀，找出所有其它 ref；
  4. 对每个其它 ref：解析 task/token，读取其租约（refs list）判断是否存活；
     有任何一个存活的重叠持有者 → **删除自己刚创建的全部 ref**，抛 `ScopeConflict`（Worker 等待重试）；
  5. 无冲突 → 获取成功。安全性由「创建后校验」保证：两个并发者要么互相看见（至少一个退让），
     要么时间错开（后到者看见先到者），不会同时保留有效锁；
- **释放**：代次校验通过后删除自己的 scope ref；旧 token 无法删除新 token 的 ref（名字不同）。
- 不重叠的 scope 可并发（不同前缀，互不可见）。

## 五、Worker / 后端切换

- `scheduler/api.py` 协议不变；新增实现 `GitHubRefScheduler`（`scheduler/ref_service.py`）；
- Worker 通过 `AGENT_SCHEDULER_BACKEND` 选择：`github`（多 VM 生产，默认）/ `sqlite`（单机/dev）；
- 构造：`make_scheduler(backend, transport=..., repo=..., clock=..., ttl=...)`；
- `register_worker` / `heartbeat_worker` / `list_workers`：GitHub 后端用注册表 Issue 评论实现
  （与现有协议一致；无 registry issue 时本地 no-op）；
- `upsert_task` / `get_task`：状态从 refs+评论派生（终态标记 → terminal；存活租约 → claimed/running；
  否则按标签 → queued）；`upsert_task` 幂等 no-op（不写权威状态）。

## 六、测试策略（真实并发，不 Mock 原子性）

- **Fake GitHub 服务**：本地 HTTP server 进程，实现 refs 创建/查询/删除/前缀列出（强制唯一性、
  422 语义、分页、故障注入），Issue 评论/标签读写；**两个 Worker 子进程连同一个 fake server**
  = 两台 VM 连同一个 GitHub，真实跨进程竞争；
- 必测：并发 claim 只一个成功（线程 + 2 子进程）、旧 token 的 renew/complete/release 全被拒、
  续租推进 token 后旧 token 失效、过期租约被 reaper 回收并可重新认领、scope 并发只一个成功、
  不重叠 scope 并发、complete 幂等、reaper 幂等、分页/422/失败注入；
- 真实 GitHub API 多 VM 并发：本机无凭据 → **NOT TESTED/BLOCKED**，在验收矩阵如实标注；
- 迁移/回滚：`AGENT_SCHEDULER_BACKEND=sqlite` 可退回单机实现；refs 权威层可整体 revert。

## 七、已知限制（需在验收文档写明）

1. GitHub refs API 有速率限制（5000 次/小时/身份）；每次 claim/renew 约 2-4 次调用，
   Worker 轮询间隔需按此调大（默认 30s 轮询 → 空闲时可 60-120s）；
2. refs 无法在「评论+标签+ref」之间做事务：完成/标签是「代次校验 + 多次写」，并发窗口内的
   标签错误由 Reaper/对账收敛（展示层最终一致，权威执行互斥不受影响）；
3. 范围锁是「创建后校验」：冲突时退让重试，极端并发下可能出现等待（有超时→blocked，与现状一致）；
4. 真实跨机验证需要至少两台隔离环境 + 仓库写权限，本轮只能在本机用多进程 + fake server 证明协议正确性。
