"""GitHub 原生权威层契约（ADR-7，冻结层）。

本文件是 ref-CAS 调度实现的**唯一接口真相源**：ref 命名、解析、存活判定、评论标记、
`RefStore` 协议与后端选择常量。实现（`github_refs.py` / `ref_lease.py` / `ref_service.py` /
`ref_scope.py` / `factory.py`）与测试只依赖这里声明的名字，不互相 import 私有细节。

设计依据：`docs/workspace/multi-worker-github-native-authority.md`（ADR-7）。
核心不变量：

- HWM（high-water mark）ref 每任务只增不减：`refs/heads/agent-hwm/issue-<N>/<token:08d>`；
  创建 HWM = **服务端原子 CAS**，是认领/续租的线性化点；
- 租约 ref 携带 epoch_ms，存活只看 `now - epoch_ms < ttl`，不读评论；
- 任何状态写都必须 `max_token(hwm) == my_token` 且自己的 lease ref 仍在，否则 `StaleFencing`；
- 旧 token 的 HWM ref 永久保留，保证 token 单调，不因回收被重用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, Sequence

from .api import (  # 错误类型直接复用 api 契约
    DEFAULT_LEASE_TTL,
    LeaseHeld,
    StaleFencing,
)

# ---------------------------------------------------------------------------
# 后端选择
# ---------------------------------------------------------------------------

#: 环境变量：调度器权威后端。生产多 VM 用 github；单机/dev 可用 sqlite。
ENV_BACKEND = "AGENT_SCHEDULER_BACKEND"
BACKEND_GITHUB = "github"
BACKEND_SQLITE = "sqlite"
DEFAULT_BACKEND = BACKEND_GITHUB

#: 认领/续租遇到 ref 冲突时的最大重试次数（并发失败者重读后重试）。
DEFAULT_CLAIM_RETRIES = 3

#: ref 权威层的默认租约时长（续租会推进 token 并刷新 epoch）。
DEFAULT_REF_LEASE_TTL = DEFAULT_LEASE_TTL

# ---------------------------------------------------------------------------
# Ref 命名
# ---------------------------------------------------------------------------

HWM_ROOT = "refs/heads/agent-hwm"
LEASE_ROOT = "refs/heads/agent-lease"
SCOPE_ROOT = "refs/heads/agent-scope"
DISPATCH_ROOT = "refs/heads/agent-dispatch"
REAP_ROOT = "refs/heads/agent-reap"

#: Reaper 回收权 ref：先 CAS 认领（同一过期租约只允许一个 Reaper 处理）。
_REAP_RE = re.compile(
    r"^refs/heads/agent-reap/issue-(?P<issue>\d+)/(?P<token>\d{8})/"
    r"(?P<reaper>[a-z0-9][a-z0-9-]{0,40})$"
)


def reap_ref(issue_number: int, token: int, reaper_instance: str) -> str:
    """回收权 ref：issuetoken 唯一，并发 Reaper 只有一个能创建成功。"""
    reaper = str(reaper_instance).lower()
    if not _AGENT_RE.match(reaper):
        raise ValueError(f"reaper_instance 不能安全放进 ref 名：{reaper_instance!r}")
    return f"{REAP_ROOT}/issue-{int(issue_number)}/{fmt_token(token)}/{reaper}"


def reap_prefix(issue_number: Optional[int] = None, token: Optional[int] = None) -> str:
    if issue_number is None:
        return f"{REAP_ROOT}/"
    prefix = f"{REAP_ROOT}/issue-{int(issue_number)}/"
    return prefix if token is None else f"{prefix}{fmt_token(token)}/"


def parse_reap(ref: str) -> Optional[dict]:
    match = _REAP_RE.match(ref or "")
    if not match:
        return None
    return {
        "issue_number": int(match.group("issue")),
        "token": int(match.group("token")),
        "reaper_instance": match.group("reaper"),
        "ref": ref,
    }


_TOKEN_WIDTH = 8
_AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_HEX_RE = re.compile(r"^[a-f0-9]{1,40}$")
_ISSUE_PREFIX_RE = re.compile(r"^issue-(?P<issue>\d+)$")
_HWM_RE = re.compile(r"^refs/heads/agent-hwm/issue-(?P<issue>\d+)/(?P<token>\d{8})$")
_LEASE_RE = re.compile(
    r"^refs/heads/agent-lease/issue-(?P<issue>\d+)/(?P<token>\d{8})/"
    r"(?P<epoch>\d{13})/(?P<agent>[a-z0-9][a-z0-9-]{0,40})/"
    r"(?P<instance>[a-f0-9]{1,40})/(?P<attempt>[a-f0-9]{1,40})$"
)


def fmt_token(token: int) -> str:
    if not isinstance(token, int) or token < 1:
        raise ValueError(f"token 必须是正整数：{token!r}")
    return f"{token:0{_TOKEN_WIDTH}d}"


def hwm_ref(issue_number: int, token: int) -> str:
    """HWM ref：每任务单调代次（只增不减，永不删除）。"""
    return f"{HWM_ROOT}/issue-{int(issue_number)}/{fmt_token(token)}"


def hwm_prefix(issue_number: int) -> str:
    """list refs 用的前缀（matching-refs 接受不带 refs/heads 也可，由实现决定）。"""
    return f"{HWM_ROOT}/issue-{int(issue_number)}/"


def lease_ref(
    issue_number: int,
    token: int,
    epoch_ms: int,
    agent_id: str,
    instance_id: str,
    attempt_id: str,
) -> str:
    """租约 ref：token + epoch_ms + 身份；存活判定只看 epoch_ms。"""
    agent = str(agent_id).lower()
    if not _AGENT_RE.match(agent):
        raise ValueError(f"agent_id 不能安全放进 ref 名：{agent_id!r}")
    for label, value in (("instance_id", instance_id), ("attempt_id", attempt_id)):
        text = str(value).lower()
        if not _HEX_RE.match(text):
            raise ValueError(f"{label} 必须是十六进制串才能放进 ref：{value!r}")
    if int(epoch_ms) <= 0:
        raise ValueError("epoch_ms 必须为正")
    return (
        f"{LEASE_ROOT}/issue-{int(issue_number)}/{fmt_token(token)}/"
        f"{int(epoch_ms)}/{agent}/{str(instance_id).lower()}/{str(attempt_id).lower()}"
    )


def lease_prefix(issue_number: int, token: Optional[int] = None) -> str:
    base = f"{LEASE_ROOT}/issue-{int(issue_number)}/"
    return base if token is None else f"{base}{fmt_token(token)}/"


@dataclass(frozen=True)
class LeaseRef:
    """从 ref 名解析出的一条租约（不包含 GitHub 返回的 sha）。"""

    issue_number: int
    token: int
    epoch_ms: int
    agent_id: str
    instance_id: str
    attempt_id: str
    ref: str = ""

    def is_live(self, now_ms: int, ttl_ms: int) -> bool:
        return now_ms - self.epoch_ms < ttl_ms


def parse_hwm_token(ref: str) -> Optional[int]:
    match = _HWM_RE.match(ref or "")
    return int(match.group("token")) if match else None


def parse_lease(ref: str) -> Optional[LeaseRef]:
    match = _LEASE_RE.match(ref or "")
    if not match:
        return None
    return LeaseRef(
        issue_number=int(match.group("issue")),
        token=int(match.group("token")),
        epoch_ms=int(match.group("epoch")),
        agent_id=match.group("agent"),
        instance_id=match.group("instance"),
        attempt_id=match.group("attempt"),
        ref=ref,
    )


def max_hwm_token(refs: Sequence[object]) -> int:
    """从 RefStore.list 的结果里取最大 token；没有返回 0。"""
    best = 0
    for item in refs:
        token = parse_hwm_token(getattr(item, "ref", "") or "")
        if token is not None and token > best:
            best = token
    return best


def live_lease(refs: Sequence[object], now_ms: int, ttl_ms: int) -> Optional[LeaseRef]:
    """在一组 lease ref 里找「token 最大且未过期」的那条。"""
    parsed = [lease for lease in (parse_lease(getattr(i, "ref", "") or "") for i in refs) if lease]
    if not parsed:
        return None
    newest = max(parsed, key=lambda lease: (lease.token, lease.epoch_ms))
    return newest if newest.is_live(now_ms, ttl_ms) else None


def ttl_ms(ttl: timedelta) -> int:
    return int(ttl.total_seconds() * 1000)


def require_current_token(
    task_id: str,
    presented_attempt: str,
    presented_token: int,
    max_token: int,
    lease_present: bool,
    reason: str = "",
) -> None:
    """代次校验的唯一实现：所有写操作前调用，不符抛 StaleFencing。"""
    if not lease_present or presented_token != max_token:
        raise StaleFencing(
            task_id,
            presented_attempt,
            int(presented_token),
            "",
            int(max_token),
            reason or "代次不匹配（旧执行者或租约已被推进）",
        )


def require_live_claim(
    task_id: str, lease: Optional[LeaseRef], holder_instance: str, now_ms: int, ttl: timedelta
) -> None:
    """认领冲突判定：有存活租约且不是本实例时抛 LeaseHeld。"""
    if lease is None or not lease.is_live(now_ms, ttl_ms(ttl)):
        return
    if lease.instance_id != str(holder_instance).lower():
        expires = datetime.fromtimestamp(lease.epoch_ms / 1000, timezone.utc) + ttl
        raise LeaseHeld(task_id, lease.agent_id, lease.instance_id, expires)


# ---------------------------------------------------------------------------
# 范围锁 ref 命名
# ---------------------------------------------------------------------------

_UNSAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]")


def encode_scope_path(normalized_path: str) -> str:
    """把规范化路径按段编码成 ref 安全形式（保留 `/` 层级，不安全字符 → `_<hex2>`）。"""
    if not normalized_path or normalized_path.startswith("/") or ".." in normalized_path.split("/"):
        raise ValueError(f"非法 scope 路径：{normalized_path!r}")
    encoded_segments = [
        _UNSAFE_SEGMENT_RE.sub(lambda m: "_%02x" % ord(m.group(0)), segment)
        for segment in normalized_path.split("/")
        if segment
    ]
    if not encoded_segments:
        raise ValueError(f"scope 路径规范化后为空：{normalized_path!r}")
    return "/".join(encoded_segments)


def scope_ref(encoded_path: str, task_id: str, token: int) -> str:
    return f"{SCOPE_ROOT}/{encoded_path}/{task_id}-{fmt_token(token)}"


def scope_overlap_prefixes(encoded_path: str) -> tuple[str, ...]:
    """获取锁时要 list 的前缀：自己路径 + 所有祖先（捕获目录/文件前缀重叠）。"""
    segments = encoded_path.split("/")
    prefixes = [f"{SCOPE_ROOT}/{'/'.join(segments[: i + 1])}/" for i in range(len(segments))]
    return tuple(prefixes)


def parse_scope_ref(ref: str) -> Optional[dict]:
    """解析 `agent-scope/<path>/<task>-<token>`；path 可能多段。"""
    prefix = SCOPE_ROOT + "/"
    if not (ref or "").startswith(prefix):
        return None
    rest = ref[len(prefix):]
    if "/" in rest:
        path, leaf = rest.rsplit("/", 1)
    else:
        return None
    match = re.match(r"^(?P<task>issue-\d+)-(?P<token>\d{8})$", leaf)
    if not match:
        return None
    return {
        "encoded_path": path,
        "task_id": match.group("task"),
        "token": int(match.group("token")),
        "ref": ref,
    }


# ---------------------------------------------------------------------------
# 评论标记（审计与人类可读；权威在 refs）
# ---------------------------------------------------------------------------

COMPLETION_MARKER_TEMPLATE = "<!-- ref-completion: {payload} -->"
LEASE_MARKER_TEMPLATE = "<!-- ref-lease: {payload} -->"


def completion_marker(idempotency_key: str) -> str:
    return f"<!-- ref-completion-id: {idempotency_key} -->"


def completion_comment_body(
    *,
    issue_number: int,
    token: int,
    attempt_id: str,
    terminal_status: str,
    idempotency_key: str,
    summary: str,
    timestamp: str,
    agent_id: str = "",
    msg_type: str = "TASK_COMPLETED",
) -> str:
    header = (
        f"<!-- agent-msg: {msg_type}, task: #{issue_number}, "
        f"from: {agent_id or 'scheduler'}, at: {timestamp} -->"
    )
    payload = (
        f"issue={issue_number}, token={token:08d}, attempt={attempt_id}, "
        f"status={terminal_status}, idempotency={idempotency_key}"
    )
    return "\n".join(
        part
        for part in (
            header,
            LEASE_MARKER_TEMPLATE.format(payload=payload),
            completion_marker(idempotency_key),
            summary or "",
            f"attempt: {attempt_id}",
        )
        if part
    )


def lease_comment_body(
    *,
    issue_number: int,
    token: int,
    agent_id: str,
    instance_id: str,
    attempt_id: str,
    timestamp: str,
) -> str:
    payload = (
        f"issue={issue_number}, token={token:08d}, agent={agent_id}, "
        f"instance={instance_id}, attempt={attempt_id}"
    )
    return (
        f"<!-- agent-msg: TASK_ACCEPTED, task: #{issue_number}, from: {agent_id}, "
        f"at: {timestamp} -->\n"
        f"{LEASE_MARKER_TEMPLATE.format(payload=payload)}\n"
        f"{agent_id}/{instance_id} 认领（token={token:08d}），权威状态见 refs。"
    )


def renew_comment_body(
    *, issue_number: int, token: int, attempt_id: str, timestamp: str, agent_id: str
) -> str:
    payload = f"issue={issue_number}, token={token:08d}, attempt={attempt_id}"
    return (
        f"<!-- agent-msg: TASK_PROGRESS, task: #{issue_number}, from: {agent_id}, "
        f"at: {timestamp} -->\n"
        f"{LEASE_MARKER_TEMPLATE.format(payload=payload)}\n"
        f"续租（token={token:08d}）。attempt: {attempt_id}"
    )


def find_completion_marker(comment_bodies: Sequence[str]) -> Optional[str]:
    """从评论正文里找 completion 幂等标记，返回 idempotency key；没有返回 None。"""
    for body in comment_bodies:
        for line in (body or "").splitlines():
            marker = line.strip()
            if marker.startswith("<!-- ref-completion-id: ") and marker.endswith(" -->"):
                return marker[len("<!-- ref-completion-id: ") : -len(" -->")]
    return None


# ---------------------------------------------------------------------------
# RefStore 协议（实现：github_refs.RestRefStore；测试用 fake）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefInfo:
    ref: str
    sha: str = ""
    object_type: str = "commit"


class RefStore(Protocol):
    """GitHub refs 的最小协议。所有方法失败都抛 GitHubRefError（实现定义）。"""

    def create(self, ref: str, sha: str) -> bool:
        """创建 ref；返回 True=本次创建成功，False=已存在（HTTP 422）。"""
        ...

    def get(self, ref: str) -> Optional[RefInfo]:
        ...

    def list(self, prefix: str) -> list[RefInfo]:
        """列出前缀下所有 ref（服务端分页由实现内部处理）。"""
        ...

    def delete(self, ref: str) -> bool:
        ...

    def branch_sha(self, name: str = "main") -> str:
        """取分支头 SHA（创建 ref 必须指向一个已存在的对象）。"""
        ...
