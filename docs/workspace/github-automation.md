# GitHub 自动化实现（TASK-07，2026-11-14）

> 适用：多 Agent 协作的 GitHub 机械动作——建分支、开 PR、CI 跟踪、分支
> 更新、合并回写、分支保护。**规范**看 `agent-protocol.md`（15 类消息、
> 标签状态机）与 `dispatch-rules.md`（拆分与汇总），**本文只写实现映射**：
> 哪个 workflow 在什么触发下做哪一步、安全阀在哪、出了问题去哪看。
> 标签定义以 `.github/labels.yml` 为准，本文不自造标签。
>
> **2026-11-14 TASK-01 更新**：`auto-dispatch.yml`、`agent-orchestrator.yml`、
> `dispatch-check.yml` 的业务逻辑已抽到 `apps/control-plane/backend/dispatch/`
> 可测模块（workflow 只负责触发与传参，不再内嵌大段 Python），分派幂等改为
> **git ref CAS**。实现映射见第六节、幂等机制见第七节、CI 作业见第八节、
> dispatch ref 清理与已知边界见第九、十节。第一至五节保留 TASK-07 的历史
> 口径（其中已被本轮回填的地方在相应位置标注）。

## 一、全景：一个任务的自动化生命周期

```
Issue 建好（模板 agent-task.md，带 agent:muse-2/3/4/5 + state:todo）
│ dispatch-check.yml：缺字段提醒、依赖未满足打 state:blocked
▼
Worker 认领：发认领评论 + 打 state:claimed
│ issue-to-pr.yml：自动建分支 + 脚手架提交 + 开草稿 PR，
│ Issue 下留自动化评论
▼
Worker 在分支上提交代码，草稿 PR 转正式
│ quality.yml：7 个 job 检查（原 5 个 + agent-tests + agent-linux-process-tests，
│ 见第八节；2026-11-14 TASK-01 起）
│ 失败 → ci-failure-notify.yml：到 PR 与 Issue 通知 builder
▼
quality 全绿 → D:\02 本机 smoke-watcher 轮询检出 PR head → 跑 ops\smoke\smoke.ps1
│ → 贴回评论（首行带 smoke-result 绑定标记）→ 通过才打 smoke-passed
│ 同时回写关联 Issue：开始发 QA_STARTED + state:qa-ready，
│ 通过发 QA_PASSED + state:qa-passed，失败发 QA_FAILED + state:qa-failed
│ （TASK-06-A #48；QA/修复闭环逐环映射见 qa-fix-loop.md）
│ 失败支路：auto-fix.yml 检出 state:qa-failed + QA_FAILED 双信号 →
│ 自动建修复 Issue 回派原开发 Agent + 发 FIX_REQUESTED，修复 PR 重新走
│ 本链；同一根任务第 4 次失败打 needs-replan 停手（TASK-06-B #49）
（smoke-watcher 是 D:\02 本机脚本，不是 Actions 工作流，见第二节；
原 agent-orchestrator.yml 自动 POST webhook 请 Grok 冒烟的路径已随
Grok 2026-11-14 退役摘除，该工作流待删除，见第二节与第十节）
▼
auto-merge.yml：quality 全绿 + smoke-passed + 无 do-not-merge → 自动合并
│ 审核门禁分级（2026-11-24）：低风险 PR（改动小、无受保护路径、无 risk:high）
│ 免独立审核并在 PR 留 review-waived 审计评论；其余仍须 review-result passed
│ 其中 update-branches 作业：2026-11-24 起默认不跑，只在人工 dispatch 且
│ 勾选 update_branches=true 时批量更新落后分支（更新会换 head、CI/冒烟/
│ 审核记录全部重跑，不再随 main 前进自动触发）
│ 合并成功后显式 POST workflow dispatch 触发下面两步（2026-11-14 修正）：
│ GITHUB_TOKEN 合并的事件不会再启动新工作流，pull_request closed
│ 触发路径被抑制，必须显式 dispatch（见第二节、第三节第 8 条）
▼
pr-merged-writeback.yml：给 Issue 打 state:merged、摘其余
state: 标签、发 PR_MERGED 评论、关闭 Issue
│（触发：PR 关闭事件，或 auto-merge 的 workflow_dispatch）
▼
auto-dispatch.yml：PR 合入后自动把下一个就绪任务派给空闲的 builder
（TASK_ASSIGNED 评论，from: github-actions），不再等每小时巡逻
（触发：PR 关闭事件，或 auto-merge 的 workflow_dispatch）
│
▼
Muse 1 按 dispatch-rules.md 第四节做最终汇总（人工/Agent 环节，不自动化）
```

## 二、各 workflow 的实现口径

| workflow | 触发 | 动作 | 关键安全阀 |
| --- | --- | --- | --- |
| `issue-to-pr.yml` | Issue 打 `state:claimed` 标签；或手动 dispatch（填 Issue 号） | 解析分支名 → 建分支（脚手架提交）→ 开草稿 PR（描述自动生成、含 `Closes #N`）→ Issue 留评论 | 只处理 open 且带 `agent:muse-2/3/4/5` 的 Issue；分支名正则校验；全程幂等（完成标记 + 存在性检查） |
| `auto-merge.yml` 的 `update-branches` 作业 | **仅手动 dispatch 且勾选 `update_branches=true`**（2026-11-24 起默认不跑） | 对落后的 `agent/*` 分支 PR 调 `PUT /pulls/{n}/update-branch` | 只动 `agent/*` 分支、非草稿、`mergeable_state == behind` 才更新；冲突只记录不处理（开发 Agent 自行 rebase）。**不再随 main 前进自动触发**：每次更新都会换 head，令 CI、冒烟、审核记录全部作废重跑 |
| `run-watchdog.yml`（2026-11-24 新增） | 每 15 分钟定时；或手动 dispatch（`dry_run`） | 调 `run_watchdog.py`：`queued`/`in_progress` 超 30 分钟的 quality 运行 → cancel；`completed + failure` 且 0 个 job → rerun（最多 2 次尝试） | 只碰 `quality` 工作流；判定是纯函数（有单测）；时间不可解析时不动作（不误杀）；单轮最多 10 个动作 |
| `ci-failure-notify.yml`（2026-11-18 加收件箱同步，#143） | `quality` 工作流跑完且结论 failure / timed_out | 按 head SHA 定位 open PR，评论通知 builder（失败 job、运行链接、SHA），关联 Issue 同步一份；再调 `alert_inbox.py ci-failure` 同步一份到 Muse 1 告警收件箱 | 同一 run 只通知一次（`ci-failure-notice` 标记去重）；收件箱按 PR 聚合（`muse1-alert: ci-failure:pr-<N>` 就地更新）；只通知不改代码/标签 |
| `pr-merged-writeback.yml` | PR 关闭且 `merged == true`、base 为 main；或 auto-merge 合并成功后 `workflow_dispatch`（填 PR 号） | 解析关联 Issue → 打 `state:merged`、摘其余 `state:` 标签 → 发 PR_MERGED 评论 → 关闭 Issue | 解析不到关联 Issue 就跳过；已回写过的重复触发跳过；dispatch 路径从 API 取 PR 并校验已合入 main |
| `auto-merge.yml` 的 `auto-merge` 作业 | 合并成功后（本作业内） | 向 `auto-dispatch.yml` 与 `pr-merged-writeback.yml` 各 POST 一次 `workflow dispatch`（`ref: main`，`inputs: pr_number`） | dispatch 失败只记日志，不让合并作业变红（合并已发生）；下游工作流自身幂等，重复触发不重复评论/分派 |
| `agent-orchestrator.yml`（2026-11-14 扩展；TASK-01 改实现，现 Actions 层面 disabled） | 仅手动 dispatch（自动触发路径已从 `on:` 摘除；工作流 `disabled_manually`） | **已停用，待删除**：Grok 已于 2026-11-14 退役（PR #46），此路径不再使用。原动作（历史）：调 `qa_dispatch.py` 按 head SHA 定位 open PR → `POST /git/refs` 登记 qa-trigger ref → POST webhook 请 Grok 冒烟。其文件头注释已写明「重新启用前先确认 Grok 是否真的回来；不回来就删掉本文件」——删除动作不在 TASK-06-C 范围，待 Muse 1 与用户决定。现行冒烟链路见下一行 smoke-watcher | 已停用，不再执行；原安全阀口径（只认 success、ref CAS 去重等）仅作历史记录 |
| `smoke-watcher`（D:\02 本机脚本，**非 Actions 工作流**） | 轮询检出 `quality` 已绿的 open PR head | 在 D:\02 本机跑 `ops\smoke\smoke.ps1`（§8.1–8.4）→ 在 PR 贴回结果评论（首行 `smoke-result` 标记绑定 head SHA）→ 通过才打 `smoke-passed`；同时回写关联 Issue 的 QA 三类消息与 qa 状态标签（开始 QA_STARTED + `state:qa-ready`、通过 QA_PASSED + `state:qa-passed`、失败 QA_FAILED + `state:qa-failed`，TASK-06-A #48），绑定旧 head 的 `smoke-passed` 先摘除再重跑 | 只对 quality 结论 success 的 head 执行；D:\02 被占用、MSNOC1 掉线等中断的结果作废重跑，不记通过也不记失败（协议第四节）；QA 回写带 (PR, SHA) 去重标记，逐环映射见 `qa-fix-loop.md` |
| `auto-fix.yml`（TASK-06-B #49） | Issue 被打 `state:qa-failed`（`issues: labeled`）；或人工 `workflow_dispatch` 重放 | 复核双信号（标签 + 最新 QA_FAILED 评论）后自动建修复 Issue（模板八节、回派原开发 Agent、失败项转验收项），在原 Issue 发 FIX_REQUESTED；轮次按根任务计数（修复 Issue 正文的 `auto-fix:` 标记），第 4 次失败只打 `needs-replan` + 说明评论 | 只建 Issue、发评论、打 `needs-replan`，不改代码、不合并、不关原 Issue；同一根任务 + 同一失败 HEAD 只建一个修复 Issue；取不到原开发 Agent 就不派任何人、注明请 Muse 1 指派；逐环映射见 `qa-fix-loop.md` |
| `auto-dispatch.yml`（2026-11-14 新增；TASK-01 改实现；2026-11-16 加定时兜底） | PR 关闭且 `merged == true`、base 为 main；或 auto-merge 合并成功后 `workflow_dispatch`（填 PR 号）；或每小时 :17 的 `schedule` 定时巡检（不看 PR，按 main HEAD 分派） | 调 `auto_dispatch.py`：校验 PR（定时路径跳过）→ 触发级对账（已登记则只补状态）→ 全量分页找就绪任务（`state:todo` + 注册表白名单内的 agent 标签 + 依赖全关 + 无未过期的认领/分派评论）→ `POST /git/refs` 做 CAS 登记（合入路径 `dispatch/pr-merged/`、定时路径 `dispatch/cron-sweep/`）→ 发带 `dispatch_id` 的 TASK_ASSIGNED 评论 → 幂等补 `state:todo` / 摘 `state:blocked` | 优先派给本次合并空闲的 builder；依赖未满足的绝不分派；auto-fix 修复任务对原 Issue 的自依赖自动忽略（否则死锁）；已认领 / 24 小时内已分派的任务不动；无就绪任务只记日志；每次只派一个任务；并发 / 重复投递只登记一次（第七节） |
| `lease-reaper.yml`（2026-11-14 新增；2026-11-16 加认领超时；2026-11-18 加收件箱，#143） | 每 5 分钟定时 + 手动 dispatch | 调 `lease_reaper.py`：回收过期 lease ref、清理孤儿 scope ref；`--claim-expiry` 再扫 `state:claimed`，认领后 4 小时无进展的发 `CLAIM_EXPIRED` 评论并把标签收敛回 `state:todo`（日志末尾 `[claim-expiry] 巡检 N 个 Issue`），同时往收件箱发 `no-progress` 告警；`--alert-blocked` 再扫 `state:blocked`，按优先级/时长升级到收件箱 | 只删过期 ref、只改状态标签，不合并 / 不关 Issue；HWM 永不删；幂等；失败显式（脚本非零 → workflow 变红 + failure() 步调 `alert_inbox.py reaper-failure` 路由到收件箱，只留最新一次） |
| `dispatch-check.yml`（2026-11-14 修 404；TASK-01 改实现） | Issue 新建 / 编辑 / 关闭 / 标签变化；每小时定时巡检 | 调 `dispatch_check.py`：全量分页读评论 / Issue 列表 / 依赖方搜索结果 → 缺字段提醒、依赖阻塞打 `state:blocked`、依赖解除幂等摘标签；**只检查不分派** | 只做提醒与标签，不合并、不关闭 Issue；评论 marker 分页扫描 + 超时对账（重复执行不产生第二条评论）；标签操作幂等（加标签去重、摘不存在的标签当成功）；查询 / 分页失败显式报错让运行变红 |
| `refs-census.yml`（2026-11-15 新增，#76） | 每周一 03:17 UTC 定时 + 手动 dispatch | 调 `refs_census.py`：分页全量统计各调度 ref 家族数量（HWM 再按 issue 统计代次）→ 写 Step Summary → 任一阈值突破则退出码 2 让运行变红告警 | **只读**（`contents: read`），不删任何 ref；清理走 lease-reaper（自动）与第十一节人工策略；阈值是预警线非硬配额，可经仓库变量调整 |

既有 workflow 不在 TASK-07 改动范围：`quality.yml`（检查本体）；
`auto-merge.yml` 只加了 `update-branches` 作业与 push / dispatch 触发，
原合并评估逻辑未动（push 触发时合并评估作业等更新作业跑完再执行）。
> 2026-11-24 流程提速批修订：`update-branches` 改为仅人工 dispatch 触发；
> `quality.yml` 的 push 触发收窄到 `main`（agent 分支只由 pull_request
> 事件跑，不再同 head 跑两套）；每个 job 加 `timeout-minutes: 25`；
> 新增 `run-watchdog.yml` 兜住卡死运行。
`agent-orchestrator.yml`、`dispatch-check.yml` 与新增的
`auto-dispatch.yml` 是 2026-11-14 的后续自动化（见上表），不在 TASK-07
的原始范围里。
2026-11-14 TASK-01：上表三个后续自动化 workflow（agent-orchestrator /
auto-dispatch / dispatch-check）的内嵌 Python 已抽成
`apps/control-plane/backend/dispatch/` 下的可测模块，workflow 只保留触发、
权限与传参；具体映射、并发幂等与 CI 作业见第六、七、八节。

### 告警收件箱（2026-11-18，#143）

五类告警统一路由给 Muse 1 的固定收件箱 Issue（标题 `[告警收件箱] Muse 1`，
首次使用时由 `alert_inbox.py` 自动创建）。实现：`apps/control-plane/backend/dispatch/alert_inbox.py`
（标准库 only）+ `github_client.py` 新增的 `create_issue` / `update_issue_comment` /
`list_issue_events`。

| 类别 | 触发点 | key（去重粒度） | 升级规则 |
| --- | --- | --- | --- |
| `ci-failure` | `ci-failure-notify.yml`（quality 失败时） | `pr-<N>` | 每次失败更新同一条评论（附最新运行） |
| `reaper-failure` | `lease-reaper.yml` 的 `failure()` 步 | `latest` | 每次失败更新同一条评论，只留最新一次 |
| `no-progress` | `lease_reaper.py --claim-expiry` 过期时 | `issue-<N>` | 认领后 4 小时无进展被过期时发 |
| `blocked-escalation` | `lease_reaper.py --alert-blocked` 每轮扫描 | `issue-<N>` | 高优先级（P0/P1）立即；普通（P2/未注明）满 30 分钟 |
| `ownerless-task` | `lease-reaper.yml` 每轮扫描（`alert_inbox.py ownerless-scan`，#176） | `current` | 整份无负责人清单只留一条，清单变化才更新，不变零写操作 |

口径：同一（类别，key）只留一条评论，就地 PATCH 更新；正文完全相同时直接
跳过，不产生无意义的 PATCH。正文只含任务/PR 编号、原因、事件时间、已采取的
自动动作、下一步建议，不含敏感值。只通知，不改代码、不改标签、不重跑。
`no-progress`、阻塞升级与无负责人扫描的告警发送失败只记日志、不让 Reaper
整轮变红（认领过期的 CLAIM_EXPIRED 评论与标签收敛已经完成，信息仍在任务
Issue 上；两类扫描下一轮都会重新推导）。
Reaper 失败告警的 `failure()` 步依赖检出：检出失败时退化为直接往收件箱追加
一条最小告警（同 marker），下一轮 CLI 成功时会就地更新它。

无负责人任务告警已于 #176 并入收件箱（类别 `ownerless-task`，见上表）：
旧实现用 bash 单独维护同名告警 Issue，marker 用每轮唯一的时间戳、查找靠
Search 标题模糊匹配，间歇落空即新建，先后攒出 #154/#164/#165/#167 四个
内容相同的 Issue；自 #176 起只往收件箱发一条滚动评论，旧告警 Issue 不再
更新。

### 状态视图（2026-11-18，#142）

统一的多 Muse 状态视图：固定状态 Issue（标题 `[状态视图] Muse 1`，
首次使用时由 `status_view.py` 自动创建），由 `status-view.yml` 机器生成
正文并就地 PATCH 更新。实现：`apps/control-plane/backend/dispatch/status_view.py`
（标准库 only）+ `github_client.py` 新增的 `update_issue` / `list_pulls(state)` /
`list_issues(state, labels)`。

载体选固定 Issue 而不用 `docs/workspace/agent-status.md` 文件：往 main
直接提交文件违反“完工走 PR、不直接推 main”；固定 Issue 就地 PATCH，与
告警收件箱同一模式，单页常驻、无 PR 噪音。

| 内容 | 口径（数据来源） |
| --- | --- |
| 在线 Muse | 心跳 refs（90 秒 TTL）+ 租约 refs（10 分钟 TTL），`derive_worker_state` 推导七态，与 `list_workers` 同源；无心跳 beat 的 Worker 不列出 |
| 任务总览 | 带 `agent:muse-*` 标签的 Issue；等待 = 创建→认领，执行 = 认领→关闭（未认领/执行中的算到现在），认领时间取时间线 `state:claimed` 打标事件 |
| QA 失败率 | 曾贴 `state:qa-failed` 的任务数 / 曾认领过的任务数；分母为 0 时写 `—`，不是 0% |
| 审核退回率 | open PR + 近 30 天内合入的 PR，最新审核结论为 changes-requested/blocked 的占比；无审核标记的 PR 不计入分母 |
| 自动重试次数 | 已取评论中 `auto-fix` 轮次标记的不同（任务，轮次）对数 |
| 自动合并数 / 人工合入数 | merged_by 是否为 github-actions[bot] |
| 每任务行 | 负责人（agent 标签）、状态、审核（关联 PR 的最新 review-result 标记，Closes #N / task: #N / 分支名关联）、QA（关联 PR 的最新 smoke-result 标记）、故障步、等待/执行/恢复用时 |

触发（事件驱动，不定时轮询 PR 状态）：`pull_request` opened/closed、
`issues` opened/closed/labeled/unlabeled、`issue_comment` created
（认领与审核/冒烟标记都是评论；labeled 事件可能被 GITHUB_TOKEN 抑制时
评论事件兜底）、`workflow_dispatch`（手动刷新；auto-merge 合入后显式
dispatch，GITHUB_TOKEN 合并不会触发 pull_request closed 事件）。
正文完全相同时跳过 PATCH。只读 + 更新本 Issue，不改代码、不改标签、
不重跑、不合并。

## 三、几个容易误解的口径

1. **「自动 Commit」指什么**：PR 至少要领先 base 一个提交才能创建，所以
`issue-to-pr` 用 Git Data API 在新分支上造一个**脚手架提交**——只新增
`.agent-work/tasks/active/task-<编号>.md` 进度文件（根 AGENTS.md 第五
节第 3 条要求的进度文件）。业务提交仍由开发 Agent 在自己的 VM 上做，
工作流不替 Agent 写代码。
2. **分支名从哪来**：优先 Issue 正文「所属分支」里的 `agent/<任务>`
（解析前先剥掉 HTML 注释——模板注释里有示例分支名，不剥会被误抓）；
没有时用 `agent/task-<编号>-<标题 ASCII slug>` 生成，标题无 ASCII
字符时退化为 `agent/task-<编号>`。
3. **PR_MERGED 评论的 from 是 `github-actions`**：协议第二节第 12 类消息
原写「由 Muse 1 发」，TASK-07 把这步机械回写自动化后，执行者如实记
为 `github-actions`；Muse 1 的职责收敛为最终汇总（dispatch-rules.md
第四节），汇总时引用回写评论即可，不再逐个手工发 PR_MERGED。
4. **Issue 自动关闭有两条路**：PR 描述里的 `Closes #N` 让 GitHub 在合并
瞬间原生关闭；`pr-merged-writeback` 补的是原生关闭没有的标签回写与
PR_MERGED 评论。解析不到 `Closes` 时回写工作流按消息头/分支名解析并
负责关闭。
5. **CI 失败通知不是 QA 失败**：`state:qa-failed` 是 D:\02 本机 smoke-watcher 冒烟失败的标签（Grok 已于 2026-11-14 退役），
CI 失败只发通知评论、不打任何标签（标签只用 labels.yml 已定义的，
不为 CI 失败新造标签）。
6. **CI 变绿后的冒烟执行者已更换（2026-11-14）**：原「CI 变绿自动
触发 Grok」的 `agent-orchestrator.yml` 路径已摘除——Grok 退役
（PR #46），该工作流在 Actions 层面 disabled、只剩手动 dispatch，
**待删除**（见第十节）。现行链路：D:\02 本机 smoke-watcher 轮询
检出 quality 已绿的 PR head → 本机跑 `ops\smoke\smoke.ps1` →
贴回评论 → 通过才打 `smoke-passed`。旧路径的口径（只认 success
结论、git ref CAS 去重、同一 head SHA 只触发一次）随其停用，
仅作历史记录保留在第七节。
7. **合入后自动分派只派一个任务**：`auto-dispatch.yml` 每次合并只找
编号最小的就绪任务，优先给本次空闲的 builder；没有就绪任务时只记
日志，不报错、不派错。已被分派过（有 TASK_ASSIGNED 评论）的任务不
重复分派；48 小时无人认领的走协议升级流程，不由本工作流重派。并发 /
重复投递 / workflow 重试由 ref CAS 保证“同一任务只登记一次”（第七节）。
8. **GITHUB_TOKEN 合并不会触发下游工作流**：auto-merge 用默认
GITHUB_TOKEN 合并 PR，GitHub 不会为该 token 触发的事件再启动新工作
流，所以 `pull_request closed` 事件被抑制，auto-dispatch 与
pr-merged-writeback 收不到合入事件（2026-11-14 实测：PR #31 合入后
两者均为 0 次运行）。修法是 auto-merge 合并成功后显式 POST
workflow dispatch 触发它俩（workflow_dispatch 明确豁免这条限制），
不需要换 PAT、不需要新 secret。手动在网页上合并（用人 token）时
pull_request closed 事件正常触发，两条路径都保留。

## 四、分支保护（main）核实清单

应然状态（与根 AGENTS.md 第五节、agent-protocol.md 第六节一致）：

- [] 存在针对 `main` 的分支保护规则，**禁止直推**（必须走 PR）。
- [] PR 合并要求审批数为 **0**（2026-11-13 起的既定策略；门禁靠 CI +
`smoke-passed` 标签，不靠人工审批）。
- [] 必需状态检查包含 quality 的全部 job：`compileall（apps + ops/deploy）`、
`ruff E9,F821（my_app）`、`门户 lint / test / 文档守卫`、
`my_app 文档一致性守卫`、`pytest 离线集（-m "not live"）`。
- [] 禁止 force push；管理员也不绕过（或至少不绕过必需检查）。
- [] `smoke-passed` 门禁由 auto-merge 逻辑保证（保护规则本身没有标签
概念，这条不在保护清单里打勾，靠 workflow 实现，已上线）。

核实记录：

- 2026-11-14 08:05 UTC（TASK-07 核实，账号 Jouwala 经浏览器只读核查）：
**main 当前没有任何分支保护**——Settings → Branches 下经典保护规则
0 条，Settings → Rulesets 下规则集 0 条。即上表清单全部未生效：直推
main、force push 在 GitHub 侧均不被拦截，此前的合并纪律全靠流程自觉
与 auto-merge 的标签门禁。
- 2026-11-14 08:27 UTC（用户确认后经浏览器新建并回读核对）：经典保护
规则已创建（分支模式 `main`）——必须走 PR 合并；「Require approvals」
子项未勾选即审批数 0；必需状态检查为 quality 五个 job（原名逐字
勾选）；「Require branches to be up to date」未开；禁止 force push；
「Do not allow bypassing the above settings」已开，管理员同样受约束。
自此直推 main 被 GitHub 侧拦截，本节清单全部生效。
- 2026-11-14（TASK-01 补记）：`quality.yml` 作业由 5 个增至 7 个——新增
`agent-tests`（windows-latest）与 `agent-linux-process-tests`（ubuntu-latest），
见第八节。**分支保护规则本轮未改**：既有必需状态检查仍为上面核实的五个
job；是否把两个新作业加入必需检查由用户/负责人决定。未加入前它们失败会
在 CI 页面显示、但不阻塞合并。

## 五、运维备忘

- 四个自动化 workflow 都只用 `GITHUB_TOKEN` + REST API，不引入第三方
Action；权限按最小化声明在各文件头 `permissions:`（各 workflow 的具体权限
与传参见第六节）。（`dispatch-check.yml`、`auto-dispatch.yml` 与
`agent-orchestrator.yml` 的内嵌 Python 自 2026-11-14 TASK-01 起已移到
`apps/control-plane/backend/dispatch/` 下的模块，只用标准库 urllib，不引入
第三方依赖，与守卫的零依赖要求一致；其余 workflow 仍用 curl/jq。）
- 分派模块可离线自测（内存假 GitHub API，含并发 / 分页 / 超时故障注入）：
`python -m pytest apps/control-plane/backend/dispatch/tests -q`
（2026-11-14 本机结果 71 passed；CI 里由 `agent-tests` 作业的三路径命令
一起跑，见第八节）。
- 手动补救入口：`issue-to-pr`、`auto-merge` 均支持 `workflow_dispatch`，
在 Actions 页选对应 workflow 填参数即可重跑（幂等，不会重复建）。
`agent-orchestrator` 也支持 `workflow_dispatch`（填 PR 号）手动补触发。
`auto-dispatch` 与 `pr-merged-writeback` 同样支持 `workflow_dispatch`
（填 PR 号）手动补触发——合入事件被抑制时就用这条路。
- 派任务链断链与修复（2026-11-14）：auto-merge 用 GITHUB_TOKEN 合并后，
`pull_request closed` 事件被 GitHub 抑制，auto-dispatch 与
pr-merged-writeback 均为 0 次运行。已改为 auto-merge 合并成功后显式
POST workflow dispatch（`ref: main`，`inputs: pr_number`）触发它俩；
dispatch 失败只记日志，不影响已发生的合并。
- 出问题先看对应 workflow 的运行日志：每个跳过分支都写了原因
（无标签 / 非 behind / 解析不到 Issue 等），按日志定位即可。
- `dispatch-check.yml` 的依赖方搜索（2026-11-14 修）：原先用
`gh api search/issues -f q=...`，在 runner 里两次报 HTTP 404
（#18/#22 关闭触发的运行），依赖方因此未被解封。已改走 REST
Search API（`GET /search/issues?q=...`，q 参数 urlencode 编码），
同一查询实测返回 HTTP 200；其余 `gh api repos/...` 调用未动。

## 六、2026-11-14 TASK-01：三个 workflow 的模块化实现映射

背景：原 `auto-dispatch.yml` / `dispatch-check.yml` / `agent-orchestrator.yml`
把两百多行 Python 内嵌在 YAML heredoc 里，无法单测，并且有 check-then-act
重复分派与“只读第一页”的真实缺陷。现在业务逻辑在
`apps/control-plane/backend/dispatch/`（每文件 <500 行、中文注释、零第三方
依赖），workflow 只保留触发、权限、环境变量与一行 `python ...` 调用。

| workflow | 调用的脚本 | workflow 传入的环境变量 | `permissions:`（YAML 原文） |
| --- | --- | --- | --- |
| `auto-dispatch.yml` | `python apps/control-plane/backend/dispatch/auto_dispatch.py` | `GH_TOKEN`、`REPO`、`EVENT`、`PR_NUMBER_EVENT`、`PR_NUMBER_INPUT`、`MERGE_SHA_EVENT` | `contents: write`（创建 dispatch ref）、`pull-requests: read`（读 PR）、`issues: write`（评论 / 标签） |
| `agent-orchestrator.yml` | `python apps/control-plane/backend/dispatch/qa_dispatch.py` | `GH_TOKEN`、`REPO`、`EVENT`、`PR_NUMBER_EVENT`、`PR_NUMBER_INPUT`、`ISSUE_NUMBER_INPUT`、`RUN_HEAD_SHA`、`RUN_ID`、`RUN_URL`、`WEBHOOK_URL`、`WEBHOOK_KEY` | `contents: write`（创建 QA 登记 ref）、`pull-requests: read`（按 head SHA 找 PR） |
| `dispatch-check.yml` | `python apps/control-plane/backend/dispatch/dispatch_check.py` | `GH_TOKEN`、`REPO`、`ISSUE_NUMBER`、`EVENT_NAME`、`EVENT_ACTION` | `contents: read`（checkout）、`issues: write`（评论 / 标签） |

补充：

- 三个作业都加了 `actions/checkout@v4` + `actions/setup-python@v5`（3.12）：
  没有 checkout，runner 上就没有模块文件；`contents: write` 只给需要
  `POST /git/refs` 的两个 workflow。
- `EVENT` / `EVENT_NAME` 只用于日志；`ISSUE_NUMBER` 在定时巡检（`schedule`）
  时为空字符串，脚本据此走“全量巡检”分支。
- 跳过原因全部打印在运行日志里（未合入 main / 草稿 / 已有 `state:qa-ready` /
  已有认领或分派评论 / 已登记等），排查先看日志。
- 模块清单：`github_client.py`（REST 客户端 + Link 分页 + 重试 + 显式失败）、
  `dispatch_registry.py`（git ref CAS）、`auto_dispatch.py`、`qa_dispatch.py`、
  `dispatch_check.py`、`comment_ledger.py`（评论只发一次）、`protocol.py`
  （消息头 / 依赖解析纯函数）。测试在 `dispatch/tests/`。
- 分页统一规则（TASK-01 修复点）：`per_page=100`、跟随 `Link: rel="next"`、
  按 id 去重、空页停止、超上限显式报错；查询失败不再被 `except` 兜成
  “没有记录”，直接让运行失败，避免用不完整数据做分派 / 打标签决定。

## 七、分派幂等：git ref CAS（TASK-07，2026-11-14 实现）

**问题**：GitHub 评论接口没有“不存在才创建”的语义。旧实现先读评论判断
“没分派过”，再 POST TASK_ASSIGNED——两个 workflow 并发运行时都能通过检查，
同一任务被分派两次。

**做法**：用 `POST /git/refs` 创建 ref 作为原子 CAS。ref 名全局唯一，后到的
请求拿到 422，天然互斥。

### 7.1 dispatch_key / dispatch_id 构成

- 逻辑来源 `source`：auto-dispatch 固定为 `pr-merged`；QA 触发固定为
  `qa-trigger`。**注意记的是逻辑事件，不是 workflow 事件名**——
  `pull_request closed` 与 auto-merge 的 `workflow_dispatch` 是同一次
  “PR 合入 main”的两条投递路径，用事件名会让同一合并算出两个 key、被派两次。
- 规范化字符串：`repo|kind|number|sha|source`；`digest` = 其 sha256 的
  前 10 位 hex。
- `dispatch_id` = `<kind>-<number>-<sha前12位>-<digest10>`：
  - auto-dispatch：`kind=issue`，`number`=目标任务 Issue 号，
    `sha`=触发 PR 的 `merge_commit_sha`；
  - QA 触发：`kind=pr`，`number`=PR 号，`sha`=PR 的 head SHA。
- ref = `refs/heads/dispatch/<source>/<dispatch_id>`，例如
  `refs/heads/dispatch/pr-merged/issue-7-222222222222-78a27590e9`。
- **稳定性**：同一 repo + 同一目标 + 同一 sha + 同一逻辑来源必然算出同一个
  dispatch_id；repo 也在 hash 里，跨仓库不串。

### 7.2 登记、发评论与对账

1. `POST /git/refs`（payload 带完整 `refs/heads/...`，`sha` 锚到
   merge_commit_sha / head_sha 这类仓库里已存在的 commit）：
   - **201**：本次是唯一赢家 → 发 TASK_ASSIGNED 评论；
   - **422**：再用 `GET /git/ref/{ref}` 确认——ref 存在 = 已登记（并发输家 /
     上次运行已登记），走对账；ref 不存在 = 锚点 SHA 无效等真实错误，
     **显式抛错**，不当成“已登记”静默丢分派。
2. 评论机器可读头带 `dispatch_id`，例如
   `<!-- agent-msg: TASK_ASSIGNED, task: #7, from: github-actions, to: muse-2,
   at: ..., dispatch_id: issue-7-... -->`；对账时**分页扫全量评论**按
   `dispatch_id` 判重，而不是只看第一页。
3. ref 存在但评论缺失（上次登记后进程崩溃 / 评论写入失败）→ 补发一次评论。
   并发场景下先等一个 grace 窗口（生产默认 2 秒，测试用 0.05–0.5 秒）观察
   赢家是否已经发出来，避免两个并发运行各补一条。
4. **POST 不自动重试**（非幂等）：发评论遇到传输层超时（结果未知）时，先重扫
   评论对账，确实没有才补发一次；补发后仍读不到则显式失败。宁可失败重跑，
   也不盲目再发一条。
5. **workflow 重试对齐**：同一 merge sha 的重复投递 / 重试先做触发级对账
   （`GET /git/matching-refs/heads/dispatch/pr-merged` 找名字里含该 sha 的
   ref）→ 找到目标 Issue 就只补评论 / 标签，不重新选任务。因此“分派成功但
   标签回写失败”后重试不会派第二个任务、也不会丢状态。
6. **QA 触发去重**：同一 PR 同一 head SHA 用同一个 ref。webhook 明确返回
   非 2xx → 回滚登记 ref 并失败（允许下次重试，与原“失败运行不算已触发”
   一致）；webhook 传输超时（结果未知）→ **保留**登记，宁可少重试一次也
   不重复触发同一 SHA。
7. **已知边界（2026-11-14 负责人确认可接受）**：并发双投递如果在“选定目标
   任务之后”才错开，两次运行可能各派一个不同 Issue；**同一任务仍然只会被
   分派一次**（ref CAS 保证）。要彻底消除需再加一层触发级 ref CAS，本轮
   不做，如实记录为已知边界。

## 八、quality.yml：作业从 5 个增至 7 个（2026-11-14 TASK-01）

新增两个作业（既有 5 个作业未改动）：

| # | 作业 | runner | 内容 |
| --- | --- | --- | --- |
| 1 | `compileall（apps + ops/deploy）` | windows-latest | 既有 |
| 2 | `ruff E9,F821（my_app）` | windows-latest | 既有 |
| 3 | `门户 lint / test / 文档守卫` | windows-latest | 既有 |
| 4 | `my_app 文档一致性守卫` | windows-latest | 既有 |
| 5 | `pytest 离线集（-m "not live"）` | windows-latest | 既有 |
| 6 | `agent-worker + control-plane 调度/分派测试`（`agent-tests`） | windows-latest | **新增** |
| 7 | `agent-worker Linux 进程/信号测试`（`agent-linux-process-tests`） | ubuntu-latest | **新增** |

两条命令原文（与 `quality.yml` 逐字一致）：

```bash
# 6：agent-worker + control-plane（调度器 + 分派模块）离线测试
python -m pytest apps/agent-worker/tests apps/control-plane/backend/tests apps/control-plane/backend/dispatch/tests -q -p no:cacheprovider
```

```bash
# 7：在 Linux 上真实执行 POSIX 进程组 / SIGTERM / SIGKILL 测试
python -m pytest apps/agent-worker/tests -q -p no:cacheprovider
```

说明：

- `-p no:cacheprovider`：runner 工作区不留 `.pytest_cache`，避免脏文件。
- 三个目录按目录跑、不依赖 rootdir 自动发现；`dispatch/tests` 必须显式列出，
  否则 TASK-07 的分派幂等测试不进 CI（`apps/control-plane/backend/tests` 是
  调度器测试的目录，`dispatch/tests` 是分派模块自己的目录）。
- Windows 作业里 Python 进程 / 信号测试只能走模拟分支；单独的 ubuntu 作业
  才让 `os.killpg` / SIGTERM / SIGKILL 语义真实执行。
- 分支保护是否把两个新作业加为“必需检查”，见第四节的 TASK-01 补记（本轮
  未改保护规则）。

## 九、`refs/heads/dispatch/**` 累积与清理策略（2026-11-14）

会产生什么：每次成功分派新增一个 ref（在仓库分支列表里表现为
`dispatch/pr-merged/...` 或 `dispatch/qa-trigger/...` 分支）——auto-dispatch
每次合并 1 个；QA 每个 PR 的每个 head SHA 1 个。长期会累积。

**本轮不做自动清理**，原因：

1. 删除 ref 会破坏 CAS 幂等：同一事件被重试 / 手动 rerun 时，已删的 ref 会被
   当成“未登记”再分派一次——这正是 TASK-07 要消除的重复分派。
2. git ref 没有时间戳元数据，自动判断“这条记录已经安全过期”不可靠。

**人工清理（保守，只删确认不会再重跑的旧记录）**：

1. 列出：`gh api "repos/{owner}/{repo}/git/matching-refs/heads/dispatch/pr-merged" --paginate`
   （QA 的换成 `heads/dispatch/qa-trigger`）。
2. 只删“对应 PR 已合并、对应 Issue 已关闭”的 ref：
   `gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/dispatch/pr-merged/<dispatch_id>"`。
3. 保守保留近 N 天（建议 30 天）：ref 本身没时间戳，用对应 PR 的合并日期判断。
4. `qa-trigger` 的 ref 在 PR 尚未合并时**不要删**：删了同一 head SHA 会重新
   触发 QA。PR 关闭 / 合并后可按同法清理。
5. 删除已分派 Issue 的 `pr-merged` ref 后，如果同一 merge 事件再被投递
   （典型是手动 rerun 旧 workflow），理论上会重复分派——所以清理只针对
   “确认不会再重跑”的记录。

本轮未提供自动清理脚本（避免误删导致重复分派）；后续如需自动化，应做成
带 TTL / 白名单（跳过最近 N 天与未合并 PR）的独立作业。
（2026-11-15 #76 起，全部 ref 家族的生命周期与监控见第十一节；本节只管
`dispatch/**` 这一家的人工清理口径，与第十一节冲突时以第十一节为准。）

## 十、已知边界与 NOT TESTED（2026-11-14 TASK-01）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 真实 GitHub Actions 上的并发执行（两 workflow 同时跑） | **已验证（2026-11-15，#80）** | 隔离仓 `ref-cas-concurrency-probe` 两条 workflow 同发令时刻并发 5 轮，每轮恰好一个赢家；报告见 `docs/workspace/ref-cas-concurrency-verification.md`。本机多线程假 API 测试（`dispatch/tests/`）仍保留为离线回归 |
| 真实 GitHub API 的分页 Link / 422 / ref 路径在线行为 | **NOT TESTED** | 本机无仓库 token；实现按官方 REST 文档。单 ref 的 `GET /git/ref` 与 `DELETE /git/refs` 用 `heads/<branch>` 形式，`POST /git/refs` 的 payload 用完整 `refs/heads/...` |
| ubuntu-latest 上的 POSIX 进程 / 信号行为 | **NOT TESTED** | 本机 Windows；由新增的 `agent-linux-process-tests` 作业在 CI 真实执行 |
| `agent-orchestrator.yml` 重新启用 | **BLOCKED（等决策）** | 该工作流在仓库 Actions 配置里仍是 `disabled_manually`（`gh workflow disable`），本轮只改实现未改开关；且 `on:` 目前只剩 `workflow_dispatch`（Grok 已退役）。重新启用前先确认 Grok 是否恢复 |
| fork PR 用 head SHA 作 ref 锚点 | **已知风险** | 对象可能不在 base repo，`POST /git/refs` 会 422；实现会显式报错（不会静默丢触发，也不会重复触发） |
| 并发双投递“各派不同 Issue”的极端窗口 | **已知边界** | 见第七节第 7 条；同一任务不会被分派两次，负责人已确认可接受 |

本地验证记录（2026-11-14，实现方在本机跑）：

- `python -m pytest apps/control-plane/backend/dispatch/tests -q` → 71 passed；
  重复多轮无 flake。
- CI 第 6 个作业的三路径组合命令本机 → 258 passed, 6 skipped（含另两位成员的
  agent-worker / scheduler 测试）。
- workflow YAML 结构校验（本机无 act，只验证可解析）：
  `python -c "import yaml, pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github/workflows').glob('*.yml')]"`
  → 8 个 workflow 全部通过。

## 十一、refs 生命周期与配额监控（2026-11-15，#76）

权威层（ADR-7）与 TASK-07 分派把状态落在 refs 上，家族如下。共同原则：
**删除 ref 会破坏 CAS 幂等**（已删的登记会被当成「未发生」而重复执行），
所以任何清理都以「确认对应事件不会再被重投递」为前提；不确定就留着，
ref 本身很便宜，误删的代价（重复分派/重复认领）远高于多留几百条。

| 家族 | 产生方式 | 谁来删 | 保留口径 |
| --- | --- | --- | --- |
| `agent-hwm/issue-<N>/<代次>` | 每次认领/续租由 ref-CAS 递增 | **永不自动删**（高水位，删了代次会回退、fencing 失效） | 永久保留；仅当 issue 已关闭且该任务线彻底终结、经人工确认并在 issue 留记录后，才可人工删除该 issue 的全部 HWM |
| `agent-lease/issue-<N>/...` | 认领时创建 | `lease-reaper.yml` 每 5 分钟删过期租约 | TTL 到期即删（自动，无需人工） |
| `agent-scope/...` | 任务声明范围锁时创建 | `lease-reaper.yml` 删「无存活租约」的孤儿 scope | 随租约生命周期（自动） |
| `agent-dispatch/...` | 调度器分派登记（CAS） | 无自动清理 | 对应 issue 关闭后保留 30 天，再按下方人工命令清理；未关闭 issue 的一条不删 |
| `agent-reap/issue-<N>/<token>` | Reaper 抢回收权时创建 | 无自动清理 | 回收审计记录，保留 90 天后可人工清理 |
| `dispatch/pr-merged/...`、`dispatch/qa-trigger/...` | TASK-07 分派/QA 登记 | 无自动清理 | 按第九节口径：对应 PR 已合并、issue 已关闭且保留满 30 天才可删；qa-trigger 在 PR 未合并前绝不删 |
| `agent/task-*`（任务分支） | issue-to-pr 建分支 | PR 合并后由仓库设置/人工删分支 | 不是调度 ref，不参与配额告警；合并后及时删分支即可 |

**监控**：`refs-census.yml` 每周一 03:17 UTC 跑 `refs_census.py` 只读普查
（也可手动 dispatch 立即跑一次），结果写 Step Summary；预警线（默认值，
仓库变量 `AGENT_REFS_WARN_TOTAL` / `AGENT_REFS_WARN_FAMILY` /
`AGENT_REFS_WARN_HWM_PER_ISSUE` 可调）：调度 ref 合计 1000、单家族 500、
单 issue 的 HWM 代次 100。突破任一线脚本退出码 2、运行变红即告警——
它们是**预警线，不是 GitHub 硬配额**（GitHub 未公布单仓 ref 上限），
目的是在列表变慢/分支噪音显著之前提醒按本节清理。手动立即普查：

```bash
REPO=Jouwala/noc-soc-intelligent-platform GH_TOKEN=<只读 token> \
  python apps/control-plane/backend/dispatch/refs_census.py
```

2026-11-15 首次普查（本仓现状）：heads 49 条，调度 ref 合计仅 1 条
（`dispatch/**` 1 条），HWM/lease/scope/dispatch 登记均为 0——权威层尚未
在本仓跑过认领，本节治理的是**启用后的预期增长**，不是存量清理。

**人工清理命令**（以 agent-dispatch 为例，其余家族换前缀同理）：

```bash
# 1) 先分页列出核对（--paginate 必带，默认只回第一页会漏）
gh api "repos/{owner}/{repo}/git/matching-refs/heads/agent-dispatch" --paginate
# 2) 逐条确认对应 issue 已关闭且超过保留期后，再逐条删除
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/agent-dispatch/<记录名>"
```

`matching-refs` 的分页缺口已在 #76 修复：`github_client.list_matching_refs`
改走全量分页（此前只取第一页，GitHub 默认 per_page=30，ref 一多对账与
普查都会静默漏数）；`gh api` 侧则必须显式带 `--paginate`。
