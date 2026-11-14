# GitHub 自动化实现（TASK-07，2026-11-14）

> 适用：多 Agent 协作的 GitHub 机械动作——建分支、开 PR、CI 跟踪、分支
> 更新、合并回写、分支保护。**规范**看 `agent-protocol.md`（12 类消息、
> 标签状态机）与 `dispatch-rules.md`（拆分与汇总），**本文只写实现映射**：
> 哪个 workflow 在什么触发下做哪一步、安全阀在哪、出了问题去哪看。
> 标签定义以 `.github/labels.yml` 为准，本文不自造标签。

## 一、全景：一个任务的自动化生命周期

```
Issue 建好（模板 agent-task.md，带 agent:muse-2/3 + state:todo）
│ dispatch-check.yml：缺字段提醒、依赖未满足打 state:blocked
▼
Worker 认领：发认领评论 + 打 state:claimed
│ issue-to-pr.yml：自动建分支 + 脚手架提交 + 开草稿 PR，
│ Issue 下留自动化评论
▼
Worker 在分支上提交代码，草稿 PR 转正式
│ quality.yml：5 个 job 检查（compileall / ruff / 门户 / 文档守卫 / pytest）
│ 失败 → ci-failure-notify.yml：到 PR 与 Issue 通知 builder
▼
打 state:qa-ready → agent-orchestrator.yml：POST webhook 请 Grok 冒烟
│ Grok 通过 → 打 smoke-passed（+ state:qa-passed）
▼
auto-merge.yml：quality 全绿 + smoke-passed + 无 do-not-merge → 自动合并
│ 其中 update-branches 作业：main 前进时自动更新
│ 落后的 agent/* 分支（update-branch），更新后 CI 自动重跑再评估合并
▼
pr-merged-writeback.yml：给 Issue 打 state:merged、摘其余
state: 标签、发 PR_MERGED 评论、关闭 Issue
│
▼
Muse 1 按 dispatch-rules.md 第四节做最终汇总（人工/Agent 环节，不自动化）
```

## 二、各 workflow 的实现口径

| workflow | 触发 | 动作 | 关键安全阀 |
| --- | --- | --- | --- |
| `issue-to-pr.yml` | Issue 打 `state:claimed` 标签；或手动 dispatch（填 Issue 号） | 解析分支名 → 建分支（脚手架提交）→ 开草稿 PR（描述自动生成、含 `Closes #N`）→ Issue 留评论 | 只处理 open 且带 `agent:muse-2/3` 的 Issue；分支名正则校验；全程幂等（完成标记 + 存在性检查） |
| `auto-merge.yml` 的 `update-branches` 作业 | push 到 main；或手动 dispatch | 对落后的 `agent/*` 分支 PR 调 `PUT /pulls/{n}/update-branch` | 只动 `agent/*` 分支、非草稿、`mergeable_state == behind` 才更新；冲突只记录不处理（开发 Agent 自行 rebase） |
| `ci-failure-notify.yml` | `quality` 工作流跑完且结论 failure / timed_out | 按 head SHA 定位 open PR，评论通知 builder（失败 job、运行链接、SHA），关联 Issue 同步一份 | 同一 run 只通知一次（`ci-failure-notice` 标记去重）；只通知不改代码/标签 |
| `pr-merged-writeback.yml` | PR 关闭且 `merged == true`、base 为 main | 解析关联 Issue → 打 `state:merged`、摘其余 `state:` 标签 → 发 PR_MERGED 评论 → 关闭 Issue | 解析不到关联 Issue 就跳过；已回写过的重复触发跳过 |

既有 workflow 不在 TASK-07 改动范围：`quality.yml`（检查本体）、
`agent-orchestrator.yml`（Grok 冒烟触发）、`dispatch-check.yml`（分派检查）；
`auto-merge.yml` 只加了 `update-branches` 作业与 push / dispatch 触发，
原合并评估逻辑未动（push 触发时合并评估作业等更新作业跑完再执行）。

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
5. **CI 失败通知不是 QA 失败**：`state:qa-failed` 是 Grok 冒烟失败的标签，
CI 失败只发通知评论、不打任何标签（标签只用 labels.yml 已定义的，
不为 CI 失败新造标签）。

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

## 五、运维备忘

- 四个自动化 workflow 都只用 `GITHUB_TOKEN` + REST API（curl/jq），不引入
第三方 Action；权限按最小化声明在各文件头 `permissions:`。
- 手动补救入口：`issue-to-pr`、`auto-merge` 均支持 `workflow_dispatch`，
在 Actions 页选对应 workflow 填参数即可重跑（幂等，不会重复建）。
- 出问题先看对应 workflow 的运行日志：每个跳过分支都写了原因
（无标签 / 非 behind / 解析不到 Issue 等），按日志定位即可。
