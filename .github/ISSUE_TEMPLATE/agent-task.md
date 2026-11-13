---
name: Agent 任务
about: 跨 VM 多 Agent 协作的任务单（Muse 1 拆分、Muse 2/3 开发、Grok 测试）。所有字段必填，缺字段的 Issue 会被 dispatch-check 提醒补齐。
title: "[任务] "
labels: ["state:todo"]
assignees: []
---

<!-- Task ID 即本 Issue 编号。任务中心是 GitHub Issues，唯一真相源，见 docs/workspace/agent-protocol.md -->

## 任务说明

<!-- 一句话说清要做什么、为什么做。不写背景的任务不认领。 -->

## 负责人

<!-- 三选一，打上对应标签；同一任务同一时间只有一个负责人 -->
<!-- - [ ] agent:muse-1（总指挥，只负责协调类任务，不写业务代码） -->
<!-- - [ ] agent:muse-2（Coding Worker） -->
<!-- - [ ] agent:muse-3（Coding Worker） -->
<!-- - [ ] agent:grok（QA Worker，只负责测试，不写代码） -->

负责人：<!-- 如 agent:muse-2 -->

## 优先级

<!-- P0：阻塞主线 / P1：本轮要做 / P2：可延后 -->

优先级：<!-- 如 P1 -->

## 文件修改范围

<!-- 列出允许改的文件或目录。认领后只能改这个范围，超出范围先在评论里说明并等 Muse 1 确认。 -->
<!-- 重叠文件范围的任务由 Muse 1 串行化，见 docs/workspace/dispatch-rules.md -->
<!-- - apps/my_app/backend/services/xxx.py -->

## 依赖任务

<!-- 前置任务全部完成（state:merged）后才能认领。无依赖写“无”。 -->
<!-- depends on #N -->

依赖：<!-- 如 depends on #12，或“无” -->

## 验收标准

<!-- 可逐条打勾的验收项。验收不通过即返工，不由口头确认代替。 -->
<!-- - [ ] agent-done-check.mjs 通过（改动前后端加 --full） -->
<!-- - [ ] quality.yml 5 个 job 全绿 -->
<!-- - [ ] Grok D:\02 冒烟通过（smoke-passed 标签） -->

- [ ]
- [ ]

## 所属分支

<!-- 一任务一分支，命名 agent/<任务>，如 agent/task-12-data-access -->
<!-- 分支由认领的 Worker 创建，不在 Issue 里先建 -->

分支：`agent/<!-- 任务简称 -->`

## 当前状态

<!-- 状态只用标签表示，不要只写在正文里：state:todo → state:claimed → state:in-review → state:qa-ready → state:qa-passed / state:qa-failed → state:merged -->
<!-- 认领、进度、结果、失败一律以 Issue 评论承载，格式见 docs/workspace/agent-protocol.md -->

状态：<!-- 如 state:todo -->
