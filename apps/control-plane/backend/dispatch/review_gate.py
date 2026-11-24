"""独立代码审核记录门禁（Phase 2 P2-1，Issue #131）。

协议见 `docs/workspace/review-record-protocol.md`。审核结论以 PR 评论里
的机器可读标记承载（惯例贴在评论首行），复用冒烟结果（smoke-result）
的 SHA 绑定模式：

    <!-- review-result: pr-<N> sha-<40 位 SHA> reviewer-<审核者> verdict=passed|changes-requested|blocked -->

判定规则（唯一真相源，本模块与 `auto-merge.yml` 的门禁共用）：
- 取 PR 评论里**最后一条** review-result 标记（按评论时间顺序；
  同一评论含多条标记时取最后一条）；
- 门禁通过当且仅当：标记的 pr 等于本 PR、verdict 是 passed、
  标记的 sha 等于当前 head SHA；
- 新提交（sha 变化）自动使旧审核失效；审核者事后改判（补一条
  changes-requested / blocked）同样使旧 passed 失效。
- **低风险免审（2026-11-24 流程提速批）**：改动小、未触及受保护路径、
  且无 `risk:high` 标签的 PR 可以免独立审核（仍需 CI + 冒烟双门禁）。
  判定与阈值见下方 `waiver_decision()`；CLI 加 `--strict` 可强制回到
  「一律要求审核记录」的旧口径。

本模块**零第三方依赖**（只用标准库），供 CI（auto-merge 工作流）与
单测共用；不要在这里引入 `dispatch.github_client` 等业务依赖——
门禁脚本必须在只有 `GH_TOKEN` 的 Actions 环境里直接跑。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

#: 审核记录标记。reviewer 只允许字母/数字/`_`/`-`/`.`（如 muse-1、muse-4）。
#: verdict 后须跟空白、`--`（标记结尾）或串尾——没有这个边界，
#: `verdict=passedXYZ` 也会被解析成 passed（放行方向，绝不允许）。
REVIEW_MARKER_RE = re.compile(
    r"review-result:\s*pr-(?P<pr>\d+)\s+"
    r"sha-(?P<sha>[0-9a-f]{40})\s+"
    r"reviewer-(?P<reviewer>[A-Za-z0-9_.\-]+)\s+"
    r"verdict=(?P<verdict>passed|changes-requested|blocked)(?=(?:\s|--|$))"
)

#: 合法 verdict 全集；只有 passed 能放行合并。
VERDICTS = ("passed", "changes-requested", "blocked")

# ---------------------------------------------------------------- 低风险免审
# 2026-11-24 流程提速批（用户定 B 档）：并非所有改动都值得一次跨会话的独立
# 审核。满足**全部**条件时免独立审核，但 CI 全绿 + smoke-passed 双门禁不动：
#   1. PR 没有 `risk:high` 标签（生产库、数仓、nginx、发布类任务必须审）；
#   2. 改动文件数 <= WAIVER_MAX_FILES，增删行合计 <= WAIVER_MAX_LINES；
#   3. 没有触及受保护路径（CI/门禁/部署脚本/迁移/鉴权/依赖清单等）。
# 三条都是客观、可机器复核的事实；**不认作者自打的“低风险”标签**，否则
# 门禁等于自己给自己放行。读不到 PR 元数据（API 失败 / 字段缺失）一律按
# 「必须审核」处理 —— fail-closed，免审只能由确凿证据触发。
WAIVER_MAX_FILES = 15
WAIVER_MAX_LINES = 400

#: 受保护目录前缀：这些路径下的改动永远要求独立审核。
PROTECTED_PREFIXES = (
    ".github/",
    ".githooks/",
    ".claude/",
    "ops/",
    "runtime/",
    "nginx-1.28.0/",
    "apps/control-plane/",
    "apps/agent-worker/",
    "apps/my_app/alembic/",
    "apps/my_app/monitor_fixes/",
)

#: 受保护的具体文件（相对仓库根，正斜杠）。
PROTECTED_PATHS = frozenset(
    {
        "AGENTS.md",
        "nginx.conf.template",
        "apps/my_app/backend/main.py",
        "apps/my_app/requirements.txt",
        "docs/workspace/agent-protocol.md",
        "docs/workspace/review-record-protocol.md",
        "docs/workspace/dispatch-rules.md",
    }
)

#: 文件名本身敏感（依赖清单 / 锁文件 / 环境变量），改了就要求审核。
PROTECTED_FILENAMES = frozenset(
    {
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "pyproject.toml",
    }
)

#: 路径里出现这些片段即视为鉴权/凭据相关，要求审核。
PROTECTED_PATH_FRAGMENTS = ("/auth", "auth_", "_auth", "credential", "secret")


def is_protected_path(path: str) -> bool:
    """判断单个改动路径是否属于受保护范围（免审白名单之外）。"""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if not p:
        return True
    if p in PROTECTED_PATHS:
        return True
    if p.startswith(PROTECTED_PREFIXES):
        return True
    name = p.rsplit("/", 1)[-1].lower()
    if name in PROTECTED_FILENAMES or name.startswith(".env"):
        return True
    low = p.lower()
    return any(fragment in low for fragment in PROTECTED_PATH_FRAGMENTS)


def waiver_decision(
    paths: list[str],
    additions: int,
    deletions: int,
    labels: list[str] | tuple[str, ...],
) -> tuple[bool, str]:
    """低风险免审判定，返回 (可免审, 原因)。

    **只返回事实**：调用方（CLI）拿到 True 才跳过审核门禁；
    False 时原因写进日志，便于解释“为什么这个 PR 还要审”。
    """
    label_set = {str(l).strip() for l in (labels or []) if str(l).strip()}
    if "risk:high" in label_set:
        return False, "PR 带 risk:high 标签，必须独立审核"
    if not paths:
        return False, "取不到改动文件列表，按必须审核处理"
    if len(paths) > WAIVER_MAX_FILES:
        return False, f"改动 {len(paths)} 个文件 > 上限 {WAIVER_MAX_FILES}"
    total = max(0, int(additions or 0)) + max(0, int(deletions or 0))
    if total > WAIVER_MAX_LINES:
        return False, f"改动 {total} 行 > 上限 {WAIVER_MAX_LINES}"
    for p in paths:
        if is_protected_path(p):
            return False, f"触及受保护路径 {p}"
    return True, (
        f"低风险（{len(paths)} 文件 / {total} 行；无 risk:high、"
        "无受保护路径）"
    )


def format_review_marker(
    pr_number: int, sha: str, reviewer: str, verdict: str
) -> str:
    """生成一条审核记录标记（贴评论首行用）。"""
    if not isinstance(pr_number, int) or pr_number < 1:
        raise ValueError(f"非法 PR 编号：{pr_number!r}")
    if verdict not in VERDICTS:
        raise ValueError(f"非法 verdict：{verdict!r}，只允许 {VERDICTS}")
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise ValueError("sha 必须是 40 位小写十六进制")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", reviewer or ""):
        raise ValueError(f"非法 reviewer：{reviewer!r}")
    return (
        f"<!-- review-result: pr-{pr_number} sha-{sha} "
        f"reviewer-{reviewer} verdict={verdict} -->"
    )


def parse_review_markers(bodies: list[str]) -> list[dict]:
    """按评论顺序抽取全部 review-result 标记（同一评论含多条时取最后一条）。

    每条返回 {"pr": int, "sha": str, "reviewer": str, "verdict": str}。
    格式不对的行直接跳过，不抛错——评论正文是人写的，门禁只认合规标记。
    取最后一条是安全方向：同一评论先贴 passed、后贴 blocked 时，
    blocked 说了算，不会把已改判的结论当成通过。
    """
    markers: list[dict] = []
    for body in bodies or []:
        matches = list(REVIEW_MARKER_RE.finditer(body or ""))
        if not matches:
            continue
        match = matches[-1]
        markers.append(
            {
                "pr": int(match.group("pr")),
                "sha": match.group("sha"),
                "reviewer": match.group("reviewer"),
                "verdict": match.group("verdict"),
            }
        )
    return markers


def latest_review(bodies: list[str]) -> dict | None:
    """最后一条审核记录；没有则返回 None。"""
    markers = parse_review_markers(bodies)
    return markers[-1] if markers else None


def review_gate_passes(
    bodies: list[str], pr_number: int, head_sha: str
) -> tuple[bool, str]:
    """判定审核门禁是否通过，返回 (通过与否, 原因说明）。

    只看最后一条标记：passed + pr 一致 + sha 等于当前 head 才放行。
    其余情况（无记录、改判、旧 head、pr 不符）一律不通过。
    """
    marker = latest_review(bodies)
    if marker is None:
        return False, "无审核记录标记（未审）"
    if marker["pr"] != pr_number:
        return False, f"最后一条审核记录属于 PR #{marker['pr']}，不是本 PR"
    if marker["verdict"] != "passed":
        return (
            False,
            f"最后一条审核结论是 {marker['verdict']}（审核者 "
            f"{marker['reviewer']}），未通过",
        )
    if marker["sha"] != head_sha:
        return (
            False,
            f"审核绑定的是旧 head {marker['sha'][:12]}，当前 head "
            f"{head_sha[:12]}，审核已失效",
        )
    return True, f"审核通过（{marker['reviewer']}，sha {head_sha[:12]}）"


def fetch_comment_bodies(repo: str, pr_number: int, token: str) -> list[str]:
    """读 PR 的全部 Issue 评论正文（分页拉满，顺序即时间顺序）。

    最多拉 100 页（10000 条评论）：服务端若永远返回满页就不再是
    “分页”，而是异常——此时抛错，门禁 fail-closed（main 捕获后
    exit 1），不让门禁脚本无限循环。
    """
    bodies: list[str] = []
    page = 1
    while True:
        if page > 100:
            raise RuntimeError(f"评论分页超过 100 页，疑似服务端异常")
        url = (
            f"https://api.github.com/repos/{repo}/issues/{pr_number}"
            f"/comments?per_page=100&page={page}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            comments = json.loads(response.read().decode("utf-8"))
        if not comments:
            break
        bodies.extend(comment.get("body") or "" for comment in comments)
        if len(comments) < 100:
            break
        page += 1
    return bodies


def fetch_pr_risk(repo: str, pr_number: int, token: str) -> dict | None:
    """读 PR 的风险元数据：改动文件、增删行、标签。

    返回 `{"paths": [...], "additions": int, "deletions": int, "labels": [...]}`；
    任何一步失败返回 None —— 调用方按「必须审核」处理（fail-closed），
    绝不把读不到证据当成低风险。
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    def _get(url: str):
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        pr = _get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}")
        files = _get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
            "/files?per_page=100"
        )
    except Exception:  # noqa: BLE001 - 读不到就不免审
        return None
    if not isinstance(pr, dict) or not isinstance(files, list):
        return None
    paths = [str(item.get("filename") or "") for item in files if isinstance(item, dict)]
    paths = [p for p in paths if p]
    labels = [
        str(item.get("name") or "")
        for item in (pr.get("labels") or [])
        if isinstance(item, dict)
    ]
    try:
        additions = int(pr.get("additions") or 0)
        deletions = int(pr.get("deletions") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "paths": paths,
        "additions": additions,
        "deletions": deletions,
        "labels": labels,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI（供 auto-merge 工作流调用）：通过返回 0，不通过返回 1。

    输出约定（auto-merge 解析）：放行时额外打印一行
    `REVIEW_TIER=waived sha=<40位> files=<n> lines=<n>` 或
    `REVIEW_TIER=reviewed sha=<40位>`，供工作流落审计评论用。
    """
    parser = argparse.ArgumentParser(description="审核记录门禁判定")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--pr", required=True, type=int, help="PR 编号")
    parser.add_argument("--head-sha", required=True, help="当前 head SHA")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="关闭低风险免审（一律要求审核记录，用于人工复核/测试）",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("缺少 GH_TOKEN / GITHUB_TOKEN，无法读评论", flush=True)
        return 1

    if not args.strict:
        risk = fetch_pr_risk(args.repo, args.pr, token)
        if risk is None:
            print("取不到 PR 元数据（文件/增删/标签），按必须审核处理", flush=True)
        else:
            waived, why = waiver_decision(
                risk["paths"], risk["additions"], risk["deletions"], risk["labels"]
            )
            if waived:
                total = risk["additions"] + risk["deletions"]
                print(f"审核门禁：通过——{why}，免独立审核", flush=True)
                print(
                    f"REVIEW_TIER=waived sha={args.head_sha} "
                    f"files={len(risk['paths'])} lines={total}",
                    flush=True,
                )
                return 0
            print(f"低风险免审不适用：{why}", flush=True)

    try:
        bodies = fetch_comment_bodies(args.repo, args.pr, token)
    except Exception as exc:  # noqa: BLE001 - 门禁读不到证据时宁可不放行
        print(f"读 PR 评论失败：{exc}，门禁不通过", flush=True)
        return 1
    passed, reason = review_gate_passes(bodies, args.pr, args.head_sha)
    print(f"审核门禁：{'通过' if passed else '不通过'}——{reason}", flush=True)
    if passed:
        print(f"REVIEW_TIER=reviewed sha={args.head_sha}", flush=True)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
