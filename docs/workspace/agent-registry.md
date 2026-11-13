# Agent 注册表（第一阶段，2026-11-13）

> Worker 注册、心跳与健康检查的登记册。Muse 1 维护本表，身份变化先改这里再改代码。
> 心跳定义：节点的最近 GitHub 活动时间（push / 评论 / PR 更新），Muse 1 定时查 API 判定，90 分钟无活动判失联。
> 身份独立化（V6，用户 2026-11-13 决定）：Muse 1/2/3 尽可能用独立 GitHub 身份，Grok 如支持也用独立机器人身份；暂时做不到时先用机器身份标记过渡（见下表“过渡方案”列）。

| Agent | 角色 | 运行环境 | GitHub 身份 | 状态 | 过渡方案 |
| --- | --- | --- | --- | --- | --- |
| Muse 1 | 总指挥 / Orchestrator：拆解、分配、协调、审核、决策 | 独立 VM | 用户 Jouwala 经 Muse 连接器（GitHub 账号 Jouwala） | 已注册 | — |
| Muse 2 | Coding Worker：负责分配给它的开发任务 | 独立 VM | 现与 Muse 3 共用 jouwalamiaoyin@gmail.com 身份 | 独立身份过渡中 | 机器身份标记：认领评论头写 `agent-claim: muse-2`，提交信息写 `muse-2:` 前缀，PR 描述注明负责人 |
| Muse 3 | Coding Worker：负责分配给它的开发任务 | 独立 VM | 现与 Muse 2 共用 jouwalamiaoyin@gmail.com 身份；计划独立身份 miaoyinwajila@gmail.com，待确认 | 独立身份过渡中 | 机器身份标记：认领评论头写 `agent-claim: muse-3`，提交信息写 `muse-3:` 前缀，PR 描述注明负责人 |
| Grok Bot | QA Worker：冒烟测试、功能验证、测试报告、失败反馈；不写代码（第一阶段） | Routine webhook 触发，D:\02 执行 | 机器人身份（待确认是否支持独立身份） | 已注册（webhook 链路已验证） | 如不支持独立身份，测试报告以 Grok Routine 名义发，评论头写 `from: grok` 区分 |

## 能力标签

| Agent | 能力 |
| --- | --- |
| Muse 1 | 需求分析、任务拆分、文件范围查重、进度协调、结果整合、冲突处理、QA 结果裁决（通过 / 返工 / 升级） |
| Muse 2 | Python 后端、FastAPI、SQLite/PG、任务系统、数据访问层 |
| Muse 3 | 前端（React / 门户）、services 重组、文档 |
| Grok Bot | D:\02 冒烟测试（§8.2–8.4）、离线 pytest、结构化测试报告 |

> 能力标签是认领匹配的依据，不是硬限制：任务需要的能力超出负责人已有能力时，由 Muse 1 改派，不硬上。

## 最近活动（Muse 1 每次检查后更新）

| Agent | 最近 GitHub 活动 | 状态 |
| --- | --- | --- |
| Muse 1 | 2026-11-13：PR #15 合并、dispatch #1–#6、第一阶段分支 | 在线 |
| Muse 2 | 2026-11-13：PR #13（`refactor/round-2-data-access`，头 98ff35b） | 在线 |
| Muse 3 | 2026-11-13：PR #14（`refactor/round-2-services-regroup`，头 104e30d） | 在线 |
| Grok Bot | 2026-11-13：PR #13/#14 冒烟重跑（dispatch #5/#6） | 在线 |

## 注册 / 注销规则

- 新 Worker 加入：先在本表登记（角色、VM、GitHub 身份、能力），再由 Muse 1 分配试验 Issue，试验通过后正式接任务。
- Worker 退出：Muse 1 在本表标记注销，其未完成任务按租约过期重派（认领后 4 小时无进展即过期）。
- 身份变更：GitHub 身份独立化完成后，更新本表对应行，并删除过渡方案列的标记要求。
