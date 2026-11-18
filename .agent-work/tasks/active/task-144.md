# 任务 #144：[修复] #121 QA 失败（第 1 轮）：【P0】修 3 处生产定时任务导入错误 + paramiko 补进 lock（依赖审计）

- Issue：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/144
- 分支：`agent/task-144-121-qa-1-p0-3-paramiko-lock`
- 状态：已认领（state:claimed），脚手架由 issue-to-pr 工作流自动生成

## 目标



## 任务说明

修复 #121 的 QA 失败项（第 1 轮自动修复，由 auto-fix 工作流按 TASK-06-B 创建）。

- 原 Issue：#121（【P0】修 3 处生产定时任务导入错误 + paramiko 补进 lock（依赖审计））
- 失败 PR：#139
- HEAD SHA：`7aedd5b2f590521d11ded5fcc6cba33014c7c8c7`
- 失败报告：https://github.com/Jouwala/noc-soc-intelligent-platform/issues/121#issuecomment-6973359262

## 负责人

负责人：agent:muse-2（回派原开发 Agent）

## 优先级

优先级：P1

## 文件修改范围

与原 Issue #121 相同（见原 Issue 的文件修改范围）；超出范围先在评论里说明并等 Muse 1 确认。

## 依赖任务

依赖：depends on #121

## 验收标准

- [ ] 修复：## D:\02 冒烟结果：失败
- [ ] 修复：失败。1. /api/health：db_mode='pg' body={"status":"ok","db_mode":"pg","database":"postgresql","db_exists":true}；2. 只读业务接口返回非空：len=0 body=；3. 门户 9277：http_code=200；4. nginx 8080/8443：http_code=200 / http_code=200；5. index.html sha256 一致；index.html sha256 `46CBB5BC0D60BEBD432660324B722A86657B47EB112890B73FE41E31A99C304C`；6. nginx -t：exit=0 out=nginx: the configuration file D:\02_olbr_portal\nginx-1.28.0\conf/nginx.conf syntax is ok / nginx: configuration file D:\02_olbr_portal\nginx-1.28.0\conf/nginx.conf test is successful
- [ ] 修复：`exit=1 summary='2 failed, 967 passed, 7 skipped, 25 warnings in 101.70s (0:01:41)' out=D:\02_olbr_portal\TMP\smoke_pytest_139_7aedd5b.out ignored: tests/test_nur_exclude_semantics.py::test_Y_subtracts_the_list, tests/test_nur_exclude_semantics.py::test_Y_is_not_the_list_itself`
- [ ] 修复后重新走评审与冒烟，取得 state:qa-passed
- [ ] 在原 Issue #121 发 FIX_COMPLETED 评论（新提交、回归自检结果）

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
