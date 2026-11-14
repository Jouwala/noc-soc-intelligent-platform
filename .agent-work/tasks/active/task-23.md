# 任务 #23：TASK-08：统一 Agent 管理控制台

- Issue：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/23
- 分支：`agent/task-23-task-08-agent`
- 状态：已认领（state:claimed），脚手架由 issue-to-pr 工作流自动生成

## 目标

## 目标
统一 Agent 管理控制台：节点、任务、执行进度、异常、审批一处可见。
## 阶段
第五阶段（外壳组件可先行）。
## 负责人
Muse 2。
## 依赖
TASK-02 查询/SSE 接口（GitHub 原生形态下为 Issue/PR 状态聚合）。
## 改动文件
- 新建 `apps/portal/src/components/agent/`：`AgentNodesView.tsx`、`AgentTasksView.tsx`、`ApprovalsView.tsx`，`hooks/useAgentEvents.ts`（SSE 推送，复用 `sseStream.ts` 解析器）
## 验收标准
- 节点（在线/失联）、任务（状态/进度/认领关系）、执行日志流、异常与审批队列可见。
- 状态实时推送，不用 60s 轮询；审批可操作并记事件。

## 已完成

- [x] 认领任务、建分支与草稿 PR（自动化）

## 未完成

- [ ] 按 Issue 验收标准实现
- [ ] agent-done-check 自检并把命令与结果贴到 Issue 评论

## 下一步

在本分支提交代码，草稿 PR 转正式后按 agent-protocol 发 TASK_COMPLETED / PR_CREATED 评论。
