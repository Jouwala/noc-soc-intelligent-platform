# 任务 #179：release-build win-x64 回归测试失败：zip-dir 符号链接/可执行位在 Windows 上丢失（v0.0.1 无 ZIP）

- Issue：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/179
- 分支：`agent/task-179-release-build-win-symlink`
- 状态：已认领（state:claimed），脚手架由 issue-to-pr 工作流自动生成

## 目标

## 任务说明
release-build（tag v0.0.1，commit a9d666b）Run 44561485336 在 win-x64 失败：步骤 6「构建脚本回归测试」exit 1（只跑 24 秒），linux-x64 被 fail-fast 取消，质量门 success。v0.0.1 Release 已于 16:02 发布但附件为空，无 ZIP，P1-8 无法开始。

失败用例（均为 ops/deploy/lib/zip-dir.test.mjs，Windows runner 特有；同一提交在本机 Linux 上 13/13 全过）：
1. :51「符号链接按链接条目入包，可执行位保留」— AssertionError: 420 !== 493（期望 0o755，得 0o644，可执行位丢失），zip-dir.test.mjs:61。
2. :67「unzip 解包还原符号链接，链接可执行」— AssertionError: false !== true，zip-dir.test.mjs:77。
其余 11/13 通过。

日志：win-x64 job 135695119538（https://github.com/Jouwala/noc-soc-intelligent-platform/actions/runs/44561485336/job/135695119538）；回归测试命令：node --test ops/deploy/lib/zip-package.test.mjs ops/deploy/lib/zip-dir.test.mjs（release-build.yml:108）。

注意：release-build 的 workflow_dispatch 只构建 dev-<短 SHA>、不发布 Release；重发布 v0.0.1 需另想触发方式（如把 tag v0.0.1 重推到修复提交），修之前先确认方案。

## 负责人
agent:muse-4

## 优先级
P0

## 文件修改范围
- ops/deploy/lib/zip-dir.*（符号链接与可执行位在 Windows 上的打包/解包处理）
- ops/deploy/lib/zip-dir.test.mjs（仅当测试本身需按平台调整时）
- .github/workflows/release-build.yml（仅当步骤需改时）

## 依赖任务
无

## 验收标准
- Windows runner 上 node --test ops/deploy/lib/zip-package.test.mjs ops/deploy/lib/zip-dir.test.mjs 13/13 通过
- release-build 重跑后双平台成功，v0.0.1 Release 附件齐全（win-x64 与 linux-x64 的 .zip + .sha256）
- Linux 上既有行为不降低（13/13 仍过，符号链接/可执行位往返仍成立）

## 所属分支
agent/task-179-release-build-win-symlink

## 已完成

- [x] 认领任务、建分支与草稿 PR（自动化）

## 未完成

- [ ] 按 Issue 验收标准实现
- [ ] agent-done-check 自检并把命令与结果贴到 Issue 评论

## 下一步

在本分支提交代码，草稿 PR 转正式后按 agent-protocol 发 TASK_COMPLETED / PR_CREATED 评论。
