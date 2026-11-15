# 任务 #70：Muse 3 Worker 配置更新：GitHub 原生权威层（PAT 需 contents:write + 拉最新 main）

- Issue：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/70
- 分支：`agent/task-70-muse-3-worker-github-pat-contents-write`
- 状态：已认领（state:claimed），脚手架由 issue-to-pr 工作流自动生成

## 目标

方案已改：权威层从 SQLite 中央调度器（PR #52）改为 GitHub 原生 ref-CAS（PR #60~#65），SQLite 已删除（PR-6）；多 VM 部署门槛已解除（owner 2026-11-15 批准，PR #68）。Worker 需做以下配置更改：
1. GitHub PAT：当前 fine-grained PAT 只有 Issues 读写，不够。ref-CAS 建/删 refs 需要 Contents 读写。换一个新 PAT（或扩权限）：Contents: Read and write + Issues: Read and write，范围仅本仓库。
2. 环境变量：GITHUB_TOKEN=新 PAT；GITHUB_OWNER=Jouwala；GITHUB_REPO=noc-soc-intelligent-platform。
3. 代码：拉最新 main（Worker 已切 GitHubRefScheduler，无 SQLite 回退）；删除 AGENT_SCHEDULER_DB 环境变量（如有）。
4. instance_id：每台 VM 的 Worker 用不同 instance_id（默认随机 hex，可固定）。
完成后在 Issue 回复确认，Muse 1 复核。

## 已完成

- [x] 认领任务、建分支与草稿 PR（自动化）

## 未完成

- [ ] 按 Issue 验收标准实现
- [ ] agent-done-check 自检并把命令与结果贴到 Issue 评论

## 下一步

在本分支提交代码，草稿 PR 转正式后按 agent-protocol 发 TASK_COMPLETED / PR_CREATED 评论。
