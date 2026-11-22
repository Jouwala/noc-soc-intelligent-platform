# agent-nudge —— 让多 Muse 体系主动找人

## 为什么有这个目录

2026-11-20 诊断：本仓库的多 Muse 体系是 **pull 模型**——每个 Muse 会话必须自己
定时去 GitHub 拉任务（`docs/workspace/agent-polling-setup.md` 的「每 30 分钟定时检查」）。
但那只是写在文档里的人肉约定，仓库里**没有任何机器机制**保证定时任务真的在跑。
状态视图 #156 显示「在线 0 人」，任务无人认领最久 5 天，告警收件箱 #148 积压 11 条
无人读——整个体系退化成「用户问一句、Muse 动一下」。

GitHub Actions 侧（auto-dispatch 每小时、lease-reaper 每 5 分钟）其实是正常的，
断的是「最后一公里：把 GitHub 上的任务/告警主动送到人（或 Muse）眼前」。

本目录提供这一层的工具。

## 工具

### `status_probe.py` —— 状态巡检 + 主动提醒（止血）

只读汇总 GitHub 上的真实状态，判断是否需要提醒人， anomalies 主动推微信。

```powershell
# 只读巡检，打印摘要（默认 dry-run，不推、不改 GitHub）
python ops\agent-nudge\status_probe.py

# 结构化输出（供 automation 的 agent 消费）
python ops\agent-nudge\status_probe.py --json

# 巡检 + 有异常则推微信（幂等：状态签名不变不重复推）
python ops\agent-nudge\status_probe.py --push

# 忽略幂等强制推一条（验证通道用）
python ops\agent-nudge\status_probe.py --push --force
```

约束：
- 默认 dry-run；`--push` 才发消息；**对 GitHub 只读**，不改状态、不发催办评论。
- 幂等：签名存 `TMP\_agent_nudge_last_push.json`，状态没变化不重复推，避免刷屏。
- 凭据从 `credentials` 表运行时读取，不落盘、不打印；推送失败只记日志。
- 只用标准库 + `gh` CLI，系统 Python 即可跑（不依赖 sqlalchemy/后端 venv）。
- 推送后端：`NOCSOC_PUSH_MODE=serverchan|pushplus|auto`（默认 auto）。

触发提醒的条件（可在脚本顶部调阈值）：在线 0 人 / 无人认领 ≥1 / 当前 QA 失败 >0 /
告警收件箱有积压评论 / 找不到状态视图或告警收件箱 Issue。

### 治本 automation（WorkBuddy 定时任务）

见 WorkBuddy 自动化任务「noc-soc Muse 1 主动巡检」。它定时驱动一个 agent 以
Muse 1 职责主动读 GitHub（告警收件箱 + 状态视图 + 无人认领任务），发现异常就
在本机会话里处理/催办，补上「Muse 自己不会定时醒」的缺口。脚本与 automation
分工：脚本负责确定性检测+推微信，agent 负责需要判断的派发/催办。

## 与现有 workflow 的关系（不重复）

| 层 | 谁 | 做什么 | 触发 |
| --- | --- | --- | --- |
| GitHub 自动化 | Actions | 分派、租约回收、状态视图写回、告警写收件箱 | 事件 + 定时（正常） |
| 检测+提醒（本目录） | status_probe.py | 读状态、判断异常、推微信 | WorkBuddy 定时任务 |
| 派发/催办（治本） | WorkBuddy agent | 读告警/无人认领，做需要判断的处理 | WorkBuddy 定时任务 |

本目录不替代 Actions，只在「GitHub 已经把问题记下来但没人看」时把问题送到人眼前。
