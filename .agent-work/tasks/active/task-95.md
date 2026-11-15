# 任务 #95：[修复] #75 QA 失败（第 1 轮）：生产前置：启用 Reaper cron + 失败告警

- Issue：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/95
- 分支：`agent/task-49-fix-75`
- 状态：已认领（state:claimed），脚手架由 issue-to-pr 工作流自动生成

## 目标



## 任务说明

修复 #75 的 QA 失败项（第 1 轮自动修复，由 auto-fix 工作流按 TASK-06-B 创建）。

- 原 Issue：#75（生产前置：启用 Reaper cron + 失败告警）
- 失败 PR：#92
- HEAD SHA：`d7ad29aef0627639f1e73e79d7ad85d1165feecf`
- 失败报告：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/75#issuecomment-6898921635

## 负责人

负责人：agent:muse-3（回派原开发 Agent）

## 优先级

优先级：P1

## 文件修改范围

与原 Issue #75 相同（见原 Issue 的文件修改范围）；超出范围先在评论里说明并等 Muse 1 确认。

## 依赖任务

依赖：depends on #75

## 验收标准

- [ ] 修复：## D:\02 冒烟结果：失败
- [ ] 修复：失败。1. /api/health：db_mode='pg' body={"status":"ok","db_mode":"pg","database":"postgresql","db_exists":true}；2. 只读业务接口返回非空：len=0 body=；3. 门户 9277：http_code=200；4. nginx 8080/8443：http_code=200 / http_code=200；5. index.html sha256 一致；index.html sha256 `7B8E951864C820F13B538316CF82DB0B9FE2B5D47E0D3062ABF03096FDA2D090`；6. nginx -t：exit=0 out=nginx: the configuration file D:\02_olbr_portal\nginx-1.28.0\conf/nginx.conf syntax is ok / nginx: configuration file D:\02_olbr_portal\nginx-1.28.0\conf/nginx.conf test is successful
- [ ] 修复：`exit=1 summary='2 failed, 938 passed, 10 skipped, 24 warnings in 116.42s (0:01:56)' out=D:\02_olbr_portal\TMP\smoke_pytest_92_d7ad29a.out ignored: tests/test_nur_exclude_semantics.py::test_Y_subtracts_the_list, tests/test_nur_exclude_semantics.py::test_Y_is_not_the_list_itself`
- [ ] 修复后重新走评审与冒烟，取得 state:qa-passed
- [ ] 在原 Issue #75 发 FIX_COMPLETED 评论（新提交、回归自检结果）

## 所属分支

分支：`agent/task-49-fix-75`

## 当前状态

状态：state:todo


## 已完成

- [x] 认领任务、建分支与草稿 PR（自动化）

## 未完成

- [ ] 按 Issue 验收标准实现
- [ ] agent-done-check 自检并把命令与结果贴到 Issue 评论

## 下一步

在本分支提交代码，草稿 PR 转正式后按 agent-protocol 发 TASK_COMPLETED / PR_CREATED 评论。
