# 任务 #171：[修复] #146 QA 失败（第 2 轮）：【ZIP交付 Phase 1】Muse 4：双平台 ZIP 构建、打包与 Release 自动化

- Issue：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/171
- 分支：`agent/task-171-146-qa-2-zip-phase-1-muse-4-zip-release`
- 状态：已认领（state:claimed），脚手架由 issue-to-pr 工作流自动生成

## 目标



## 任务说明

修复 #146 的 QA 失败项（第 2 轮自动修复，由 auto-fix 工作流按 TASK-06-B 创建）。

- 原 Issue：#146（【ZIP交付 Phase 1】Muse 4：双平台 ZIP 构建、打包与 Release 自动化）
- 失败 PR：#151
- HEAD SHA：`b1e6a617f84c861bff1667f2c4952614e117f369`
- 失败报告：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/146#issuecomment-7007139106

## 负责人

负责人：agent:muse-4（回派原开发 Agent）

## 优先级

优先级：P1

## 文件修改范围

与原 Issue #146 相同（见原 Issue 的文件修改范围）；超出范围先在评论里说明并等 Muse 1 确认。

## 依赖任务

依赖：depends on #146

## 验收标准

- [ ] 修复：## D:\02 冒烟结果：失败
- [ ] 修复：失败。1. /api/health：见报告；2. 只读业务接口：见报告；3. 门户 9277：未找到检查行；4. nginx 8080/8443：未找到检查行 / 未找到检查行；5. index.html sha256 未找到检查行；6. nginx -t：未找到检查行
- [ ] 修复：`exit=1 summary='2 failed, 965 passed, 9 skipped, 24 warnings in 17.69s' out=D:\02_olbr_portal\.smoke\TMP\smoke_pytest_151_b1e6a61.out ignored: tests/test_nur_exclude_semantics.py::test_Y_subtracts_the_list, tests/test_nur_exclude_semantics.py::test_Y_is_not_the_list_itself`
- [ ] 修复：### 失败项
- [ ] 修复：- §8.3 检查：start_all 失败，跳过 §8.3 检查（见 start_all 日志）
- [ ] 修复后重新走评审与冒烟，取得 state:qa-passed
- [ ] 在原 Issue #146 发 FIX_COMPLETED 评论（新提交、回归自检结果）

## 所属分支

分支：待认领后由 issue-to-pr 按本 Issue 编号自动创建

## 当前状态

状态：state:todo


## 已完成

- [x] 认领任务、建分支与草稿 PR（自动化）

## 未完成

- [ ] 按 Issue 验收标准实现
- [ ] agent-done-check 自检并把命令与结果贴到 Issue 评论

## 下一步

在本分支提交代码，草稿 PR 转正式后按 agent-protocol 发 TASK_COMPLETED / PR_CREATED 评论。
