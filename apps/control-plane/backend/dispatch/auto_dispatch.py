"""PR 合入后 / 每小时定时巡检自动分派下一个就绪任务（原 `auto-dispatch.yml` 内嵌脚本）。

行为与安全阀沿用原工作流（见 `docs/workspace/github-automation.md` 第二节）：

- 合入路径：只处理 merged == true 且 base 为 main 的 PR（workflow_dispatch 路径也再校验一次）；
- 定时路径：每小时 :17 巡检一次，不看 PR，直接按 main HEAD 分派——兜住
  「任务早就绪、但没有新 PR 合入」的空窗（`event=schedule`）；
- Issue 事件路径：新 Issue 创建（opened）或依赖 Issue 关闭（closed）时
  即时分派，不等巡检（`event=issues`，来源 `issue-event`）；PR 事件由
  job 层 `!github.event.issue.pull_request` 排除，不与 pull_request 重复。
- 就绪 = `state:todo` + 无其他 state 标签 + 命中**注册表白名单**的 agent 标签
  （来源 `docs/workspace/agent-registry.md`，见 `builder_registry.py`）+
  `depends on #N` 依赖全关闭 + 无未过期的认领 / 分派记录；
- auto-fix 修复 Issue 对原 Issue 的 `depends on` 是自依赖（原 Issue 要等本修复
  合入才关闭），分派时忽略，避免互相等待（2026-11-16 #108 死锁）；
- 优先派给本次合并空闲出来的 builder，其次编号最小的就绪任务；每次只派一个；
- 没有就绪任务只记日志，不报错。

修复的缺陷：

1. **全量分页**：候选 Issue（`state:todo`）与每个 Issue 的评论都走
   `client.paginate`，不再只读第一页。评论 >100 条时第 2 页的认领 / 分派
   记录能读到；Issue >100 条时第 2 页之后的任务能发现。
2. **并发幂等（TASK-07）**：任务选定后先 `POST /git/refs` 登记
   `dispatch/<source>/issue-<N>-<sha>-<digest>`（原子 CAS，见
   `dispatch_registry.py`），登记成功才发 TASK_ASSIGNED 评论；评论头带
   `dispatch_id`。并发运行只有一个赢家；重复投递 / workflow 重试命中已存在
   的 ref，只做对账，不再分派第二个任务。
3. **失败显式**：查询 / 分页失败不再 `except` 成“没记录”，直接抛错让
   workflow 变红，避免用不完整数据做分派决定。
4. **分派超时**：TASK_ASSIGNED 发出 24 小时仍无人认领（协议规定）时不再永久
   卡住该任务，允许重新分派；认领后的 4 小时租约过期由 `claim_expiry.py`
   处理（lease-reaper 每 5 分钟巡检）。

调用方式（workflow 里就是这一行）：`python apps/control-plane/backend/dispatch/auto_dispatch.py`
环境变量：`REPO` / `GH_TOKEN` / `EVENT` / `PR_NUMBER_EVENT` / `PR_NUMBER_INPUT`
/ `MERGE_SHA_EVENT`。`EVENT=schedule` 走定时巡检路径。
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 直接以文件方式运行时（workflow 的调用方式）把 backend 目录放进 sys.path，
# 这样 `dispatch` 包可导入；作为包被测试导入时这段是空操作。
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from dispatch.builder_registry import load_builders  # noqa: E402
from dispatch.comment_ledger import ensure_comment_once  # noqa: E402
from dispatch.dispatch_registry import (  # noqa: E402
    SOURCE_AUTO_DISPATCH,
    SOURCE_CRON_SWEEP,
    SOURCE_ISSUE_EVENT,
    DispatchRegistry,
)
from dispatch.github_client import GitHubAPIError, GitHubClient, client_from_env  # noqa: E402
from dispatch.protocol import (  # noqa: E402
    ASSIGNED_MARKER,
    CLAIM_MARKERS,
    agent_of,
    build_agent_header,
    comment_has_dispatch_id,
    effective_dependencies,
    parse_dependencies,
    parse_marker_time,
    resolve_issue_number,
    utc_now_iso,
)

# 对账等待：ref 已登记但评论暂时读不到时，先给“登记的赢家”一点时间把评论
# 写完；超时仍未出现才补发（覆盖“登记后进程崩溃 / 评论超时”）。
DEFAULT_RECONCILE_TIMEOUT = 2.0
POLL_INTERVAL = 0.05

#: 分派 24 小时无人认领即视为超时（docs/workspace/agent-protocol.md 第三节）。
DEFAULT_ASSIGN_TTL = timedelta(hours=24)


def _labels_of(issue: dict[str, Any]) -> list[str]:
    return [lb.get("name", "") for lb in issue.get("labels", []) or []]


def _comment_bodies(client: GitHubClient, number: int) -> list[str]:
    """全量评论正文（分页跟随 Link）。失败直接抛出，不静默当成“没有”。"""
    return [c.get("body") or "" for c in client.list_issue_comments(number)]


def find_dispatch_comment(
    client: GitHubClient, number: int, dispatch_id: str
) -> bool:
    """分页扫描该 Issue 全部评论，判断某个 dispatch_id 是否已发过分派评论。"""
    return any(
        comment_has_dispatch_id(body, dispatch_id)
        for body in _comment_bodies(client, number)
    )


def ensure_dispatch_comment(
    client: GitHubClient,
    number: int,
    dispatch_id: str,
    body: str,
    *,
    wait_timeout: float = 0.0,
    poll_interval: float = POLL_INTERVAL,
    sleep=time.sleep,
) -> str:
    """保证该 dispatch 恰好有一条 TASK_ASSIGNED 评论。

    返回值：`exists` / `posted` / `reconciled`（超时后对账发现已生效）/
    `posted-after-timeout`（超时后确认没生效，补发一次）。

    - `wait_timeout > 0`：留给“另一个并发运行正在发评论”的观察窗口，窗口
      内出现评论就不再发（避免并发重复）；
    - 发送抛传输层超时：请求可能已经生效，先重扫评论对账，找不到才补发
      一次 —— 这是“API 成功但客户端超时不得产生第二条有效分派”的落点。
    """
    return ensure_comment_once(
        client,
        number,
        lambda comment_body: comment_has_dispatch_id(comment_body, dispatch_id),
        body,
        wait_timeout=wait_timeout,
        poll_interval=poll_interval,
        sleep=sleep,
    )


def ensure_todo_state(client: GitHubClient, number: int) -> None:
    """幂等回写标签：补 state:todo、摘残留 state:blocked。

    两个操作本身幂等（加标签去重、摘不存在的标签把 404 当成功），所以不先
    查标签再写 —— 没有 check-then-act 的竞态窗口。
    """
    client.add_issue_labels(number, ["state:todo"])
    client.remove_issue_label(number, "state:blocked")


def is_ready(
    client: GitHubClient,
    issue: dict[str, Any],
    *,
    builders: tuple[str, ...] | None = None,
    now: datetime | None = None,
    assign_ttl: timedelta = DEFAULT_ASSIGN_TTL,
) -> tuple[bool, str]:
    """四条就绪条件（见模块注释），返回 (是否就绪, 不就绪原因)。"""
    number = int(issue["number"])
    labels = _labels_of(issue)
    if "state:todo" not in labels:
        return False, "无 state:todo 标签"
    other_states = [
        lb for lb in labels
        if lb.startswith("state:") and lb not in ("state:todo", "state:blocked")
    ]
    if other_states:
        return False, f"已有其他状态标签 {other_states}"
    allowed = tuple(builders) if builders is not None else load_builders()
    if not agent_of(labels, allowed):
        return False, f"无白名单内的 agent 标签（白名单：{', '.join(allowed)}）"

    body = issue.get("body") or ""
    raw_deps = parse_dependencies(body)
    deps = effective_dependencies(body)
    dropped = sorted(set(raw_deps) - set(deps))
    if dropped:
        print(
            f"[dispatch] #{number} 忽略自依赖 {dropped}：auto-fix 修复任务对其原 Issue "
            "的 depends on 会互相等待（死锁），按无依赖处理",
            flush=True,
        )
    for dep in deps:
        dep_issue = client.get_issue(dep)  # 查询失败直接抛错，不当成“未完成”或“已完成”
        if dep_issue.get("state") != "closed":
            return False, f"依赖 #{dep} 未完成"

    bodies = _comment_bodies(client, number)  # 全量分页
    if any(marker in body for body in bodies for marker in CLAIM_MARKERS):
        return False, "已有认领评论"
    assigned_times = [
        parse_marker_time(body) for body in bodies if ASSIGNED_MARKER in body
    ]
    if assigned_times:
        latest = max((moment for moment in assigned_times if moment), default=None)
        current = now or datetime.now(timezone.utc)
        # 没有时间戳的老评论按“刚分派”保守处理，不为省一次查询放过重复分派。
        if latest is None or current - latest < assign_ttl:
            return False, "已有 TASK_ASSIGNED 分派评论"
        print(
            f"[dispatch] #{number} 上次分派于 {latest:%Y-%m-%dT%H:%M:%SZ}，"
            f"已超过 {int(assign_ttl.total_seconds() // 3600)}h 无人认领，允许重新分派",
            flush=True,
        )
    return True, ""


def freed_builder(
    client: GitHubClient, pr: dict[str, Any], builders: tuple[str, ...] | None = None
) -> str:
    """判定本次合并空闲出来的 builder：PR 标签 → 关联 Issue 标签。

    这个结果只影响“优先派给谁”的排序，查询失败只记警告、不阻断分派。
    """
    allowed = tuple(builders) if builders is not None else load_builders()
    agent = agent_of(_labels_of(pr), allowed)
    if agent:
        return agent
    issue_no = resolve_issue_number(
        pr.get("body") or "", (pr.get("head") or {}).get("ref") or ""
    )
    if not issue_no:
        return ""
    try:
        issue = client.get_issue(issue_no)
    except GitHubAPIError as exc:
        print(f"[dispatch] 关联 Issue #{issue_no} 查询失败（仅影响优先级排序）：{exc}", flush=True)
        return ""
    return agent_of(_labels_of(issue), allowed)


def collect_ready(
    client: GitHubClient,
    *,
    builders: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """分页读取全部 state:todo Issue，筛出就绪任务。"""
    allowed = tuple(builders) if builders is not None else load_builders()
    ready: list[tuple[dict[str, Any], str]] = []
    for issue in client.list_open_issues("state:todo"):
        number = issue.get("number")
        if number is None or "pull_request" in issue:
            continue
        ok, reason = is_ready(client, issue, builders=allowed, now=now)
        if ok:
            agent = agent_of(_labels_of(issue), allowed)
            ready.append((issue, agent))
            print(f"[dispatch] #{number} 就绪（{agent}）", flush=True)
        else:
            print(f"[dispatch] #{number} 不就绪：{reason}", flush=True)
    return ready


def build_assignment_comment(
    number: int,
    agent_name: str,
    pr_number: int,
    dispatch_id: str,
    *,
    at: str | None = None,
    reason: str = "pr-merged",
) -> str:
    """TASK_ASSIGNED 评论：协议机器可读头（带 dispatch_id）+ 人可读正文。"""
    header = build_agent_header(
        "TASK_ASSIGNED",
        task=number,
        sender="github-actions",
        to=agent_name,
        at=at or utc_now_iso(),
        dispatch_id=dispatch_id,
    )
    if reason == "schedule":
        intro = (
            f"任务已自动分派：定时巡检（每小时 :17）发现就绪任务，"
            f"{agent_name} 当前空闲，本任务自动派给 {agent_name}。\n\n"
        )
    elif reason == "issues":
        intro = (
            f"任务已自动分派：新 Issue 创建 / 依赖 Issue 关闭，触发即时分派，"
            f"{agent_name} 当前空闲，本任务自动派给 {agent_name}。\n\n"
        )
    else:
        intro = (
            f"任务已自动分派：PR #{pr_number} 合并后，{agent_name} 空闲，"
            f"本任务自动派给 {agent_name}。\n\n"
        )
    return (
        f"{header}\n"
        f"{intro}"
        f"请按协议认领：发认领评论并打 `state:claimed` 标签。"
        f"任务说明与验收标准见本 Issue 正文；依赖已全部满足，可以认领。\n"
    )


def reconcile_task(
    client: GitHubClient,
    registry: DispatchRegistry,
    number: int,
    sha: str,
    pr_number: int,
    *,
    reconcile_timeout: float = DEFAULT_RECONCILE_TIMEOUT,
) -> bool:
    """对账：登记过但状态可能没回写完 → 只补评论 / 标签，绝不重复分派。

    返回 True 表示该任务此前确实已登记（本次只对齐了状态）。补发评论时
    负责人从 Issue 标签重新解析，PR 号用本次触发 PR，保证补发内容与正常
    分派一致。
    """
    dispatch_id = registry.make_id("issue", number, sha, SOURCE_AUTO_DISPATCH)
    if not registry.exists("issue", number, sha, SOURCE_AUTO_DISPATCH):
        print(f"[dispatch] #{number} 的登记 ref 不存在，本次不做对账", flush=True)
        return False
    print(f"[dispatch] #{number} 已登记（dispatch_id={dispatch_id}），只做对账", flush=True)
    agent = agent_of(_labels_of(client.get_issue(number)), load_builders())
    agent_name = agent.split(":", 1)[1] if agent else "未知"
    ensure_dispatch_comment(
        client,
        number,
        dispatch_id,
        build_assignment_comment(number, agent_name, pr_number, dispatch_id),
        wait_timeout=reconcile_timeout,
    )
    ensure_todo_state(client, number)
    return True


def reconcile_prior_dispatches(
    client: GitHubClient,
    registry: DispatchRegistry,
    sha: str,
    pr_number: int,
    *,
    reconcile_timeout: float,
) -> list[int]:
    """按触发（来源 + merge sha）找出已登记的分派并逐个对账。

    返回对账成功的目标 Issue 编号；空列表表示该触发还没有任何分派。
    """
    reconciled: list[int] = []
    for item in registry.find_for_trigger(sha, SOURCE_AUTO_DISPATCH):
        if item.get("kind") != "issue":
            continue
        number = int(item["number"])
        if reconcile_task(
            client, registry, number, sha, pr_number, reconcile_timeout=reconcile_timeout
        ):
            reconciled.append(number)
    return reconciled


def dispatch_next(
    client: GitHubClient,
    repo: str,
    pr_number: int,
    *,
    merge_sha: str | None = None,
    reconcile_timeout: float = DEFAULT_RECONCILE_TIMEOUT,
    at: str | None = None,
) -> dict[str, Any]:
    """主流程：校验 PR → 对账 → 选就绪任务 → CAS 登记 → 发分派评论。

    返回结构化结果（便于单测与日志）：
    `{"status": "skip"|"no-ready"|"dispatched"|"reconciled", ...}`。
    任何 GitHub 查询失败都向上抛，由 workflow 显式失败。
    """
    if pr_number <= 0:
        return {"status": "skip", "reason": "未取到 PR 编号"}
    pr = client.get_pull(pr_number)
    if pr.get("merged") is not True or (pr.get("base") or {}).get("ref") != "main":
        return {
            "status": "skip",
            "reason": (
                f"PR #{pr_number} 未合入 main（merged={pr.get('merged')}，"
                f"base={(pr.get('base') or {}).get('ref')}）"
            ),
        }
    sha = str(merge_sha or pr.get("merge_commit_sha") or "")
    if not sha:
        raise GitHubAPIError(
            f"PR #{pr_number} 已合入但拿不到 merge_commit_sha，无法生成稳定分派键"
        )

    registry = DispatchRegistry(client, repo)
    # ① 触发级对账：同一个合并（逻辑来源 + merge sha）此前已登记过分派？
    #    找到说明这是重复投递 / workflow 重试，只对齐状态，不再选新任务。
    reconciled = reconcile_prior_dispatches(
        client, registry, sha, pr_number, reconcile_timeout=reconcile_timeout
    )
    if reconciled:
        return {
            "status": "reconciled",
            "merge_sha": sha,
            "issues": reconciled,
            "reason": "该合并此前已分派过，本次只对账状态（幂等）",
        }

    # ② 正常路径：选一个就绪任务。
    builders = load_builders()
    print(f"[dispatch] 分派白名单：{', '.join(builders)}", flush=True)
    freed = freed_builder(client, pr, builders)
    print(f"[dispatch] PR #{pr_number} 已合入（{sha[:12]}），空闲 builder：{freed or '未知'}", flush=True)
    ready = collect_ready(client, builders=builders)
    if not ready:
        # 竞态兜底：本次扫描期间可能有并发运行刚好完成分派——它发完评论后
        # 任务就不再“就绪”，直接报 no-ready 会掩盖“已经派过一次”。再查一次
        # 触发登记，有就按对账返回，保证重试语义诚实（不派第二个任务）。
        raced = reconcile_prior_dispatches(
            client, registry, sha, pr_number, reconcile_timeout=reconcile_timeout
        )
        if raced:
            return {
                "status": "reconciled",
                "merge_sha": sha,
                "issues": raced,
                "reason": "扫描期间并发运行已完成分派，本次只对账状态（幂等）",
            }
        return {"status": "no-ready", "merge_sha": sha, "reason": "没有可分派的就绪任务"}

    def sort_key(item: tuple[dict[str, Any], str]) -> tuple[int, int]:
        issue, agent = item
        return (0 if agent == freed else 1, int(issue["number"]))

    ready.sort(key=sort_key)
    issue, agent = ready[0]
    number = int(issue["number"])
    agent_name = agent.split(":", 1)[1]

    # ③ 原子登记（CAS）。created=False 表示并发输家 / 已登记过：只对账。
    #    ref 锚到 merge commit（触发提交，一定存在于仓库）——名字里的 sha 才是
    #    语义来源，锚点只是 git ref 的合法性要求。
    registration = registry.register(
        "issue", number, sha, SOURCE_AUTO_DISPATCH, anchor_sha=sha
    )
    body = build_assignment_comment(number, agent_name, pr_number, registration.dispatch_id, at=at)
    outcome = ensure_dispatch_comment(
        client,
        number,
        registration.dispatch_id,
        body,
        wait_timeout=0.0 if registration.created else reconcile_timeout,
    )
    ensure_todo_state(client, number)
    return {
        "status": "dispatched" if registration.created else "reconciled",
        "merge_sha": sha,
        "issue": number,
        "agent": agent,
        "dispatch_id": registration.dispatch_id,
        "comment": outcome,
    }


def dispatch_scheduled(
    client: GitHubClient,
    repo: str,
    *,
    at: str | None = None,
    builders: tuple[str, ...] | None = None,
    reconcile_timeout: float = DEFAULT_RECONCILE_TIMEOUT,
    source: str = SOURCE_CRON_SWEEP,
    event_name: str = "schedule",
) -> dict[str, Any]:
    """无特定 PR 的分派路径（定时巡检 / Issue 事件）：不看 PR，按 main HEAD 分派一个就绪任务。

    与合入路径共用就绪判定与 CAS 登记，区别只在“触发键”：来源记
    `cron-sweep`（定时）或 `issue-event`（新 Issue 创建 / 依赖关闭）、
    sha 取 main HEAD，ref 落到 `dispatch/<来源>/...`。
    这样即使一整天没有 PR 合入，就绪任务也不会一直干等；同日重复运行命中
    已登记的 ref，只对账不再发第二条评论。
    """
    sha = client.get_branch_sha("main")
    event_label = {"schedule": "定时巡检", "issues": "Issue 事件"}.get(
        event_name, event_name
    )
    print(f"[dispatch] {event_label}：main HEAD {sha[:12]}", flush=True)
    allowed = tuple(builders) if builders is not None else load_builders()
    print(f"[dispatch] 分派白名单：{', '.join(allowed)}", flush=True)
    ready = collect_ready(client, builders=allowed)
    if not ready:
        return {
            "status": "no-ready",
            "event": event_name,
            "merge_sha": sha,
            "reason": "没有可分派的就绪任务",
        }
    ready.sort(key=lambda item: int(item[0]["number"]))
    issue, agent = ready[0]
    number = int(issue["number"])
    agent_name = agent.split(":", 1)[1]
    registry = DispatchRegistry(client, repo)
    registration = registry.register(
        "issue", number, sha, source, anchor_sha=sha
    )
    body = build_assignment_comment(
        number, agent_name, 0, registration.dispatch_id, at=at, reason=event_name
    )
    outcome = ensure_dispatch_comment(
        client,
        number,
        registration.dispatch_id,
        body,
        wait_timeout=0.0 if registration.created else reconcile_timeout,
    )
    ensure_todo_state(client, number)
    return {
        "status": "dispatched" if registration.created else "reconciled",
        "event": event_name,
        "merge_sha": sha,
        "issue": number,
        "agent": agent,
        "dispatch_id": registration.dispatch_id,
        "comment": outcome,
    }


def run_from_env(env: dict[str, str], client: GitHubClient | None = None) -> dict[str, Any]:
    """workflow 入口的纯逻辑部分（可注入客户端单测）。"""
    repo = env.get("REPO") or env.get("GITHUB_REPOSITORY") or ""
    if not repo:
        raise GitHubAPIError("缺少 REPO 环境变量")
    event = env.get("EVENT") or ""
    pr_number = int(env.get("PR_NUMBER_EVENT") or env.get("PR_NUMBER_INPUT") or "0")
    merge_sha = env.get("MERGE_SHA_EVENT") or ""
    print(f"[dispatch] event={event or '未知'} pr={pr_number}", flush=True)
    client = client or client_from_env(env)
    if event == "schedule":
        return dispatch_scheduled(client, repo)
    if event == "issues":
        # 新 Issue 创建 / 依赖 Issue 关闭：即时分派，不等每小时巡检。
        # 来源记 issue-event（与 cron-sweep 区分，幂等键独立）。
        return dispatch_scheduled(
            client, repo,
            source=SOURCE_ISSUE_EVENT,
            event_name="issues",
        )
    return dispatch_next(client, repo, pr_number, merge_sha=merge_sha)


def main() -> int:
    result = run_from_env(dict(os.environ))
    print(f"[dispatch] 结果：{result}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
