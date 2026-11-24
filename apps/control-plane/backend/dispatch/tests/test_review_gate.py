"""独立代码审核记录门禁（review_gate.py）的判定规则测试。"""

from __future__ import annotations

import pytest

from dispatch.review_gate import (
    format_review_marker,
    latest_review,
    parse_review_markers,
    review_gate_passes,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def _marker(pr: int = 123, sha: str = SHA_A, reviewer: str = "muse-1",
            verdict: str = "passed") -> str:
    return format_review_marker(pr, sha, reviewer, verdict)


def _body(marker: str, extra: str = "审核通过，逻辑无问题。") -> str:
    return f"{marker}\n\n## 独立代码审核：通过\n\n{extra}\n"


# ------------------------------------------------------------------ 标记格式
def test_format_marker_roundtrip() -> None:
    marker = _marker()
    assert marker == (
        f"<!-- review-result: pr-123 sha-{SHA_A} "
        "reviewer-muse-1 verdict=passed -->"
    )
    parsed = parse_review_markers([marker])
    assert parsed == [
        {"pr": 123, "sha": SHA_A, "reviewer": "muse-1", "verdict": "passed"}
    ]


def test_format_marker_rejects_bad_verdict_and_sha() -> None:
    with pytest.raises(ValueError):
        format_review_marker(123, SHA_A, "muse-1", "ok")
    with pytest.raises(ValueError):
        format_review_marker(123, "abc", "muse-1", "passed")
    with pytest.raises(ValueError):
        format_review_marker(123, SHA_A, "muse 1", "passed")


def test_malformed_marker_is_ignored_not_fatal() -> None:
    bodies = [
        "<!-- review-result: pr-123 sha-xyz verdict=passed -->",
        "<!-- review-result: pr-123 sha-" + SHA_A + " verdict=maybe -->",
        _body(_marker()),
    ]
    assert len(parse_review_markers(bodies)) == 1
    assert latest_review(bodies)["verdict"] == "passed"


# ------------------------------------------------------------------ 门禁判定
def test_passed_on_current_head_passes() -> None:
    passed, reason = review_gate_passes([_body(_marker())], 123, SHA_A)
    assert passed is True
    assert "muse-1" in reason


def test_no_marker_fails() -> None:
    passed, reason = review_gate_passes(["只有正文，没有标记"], 123, SHA_A)
    assert passed is False
    assert "未审" in reason


def test_passed_on_old_head_fails_after_new_commit() -> None:
    """新提交自动使旧审核失效（P2-1 核心规则）。"""
    passed, reason = review_gate_passes([_body(_marker(sha=SHA_A))], 123, SHA_B)
    assert passed is False
    assert "已失效" in reason


def test_later_changes_requested_overrides_earlier_passed() -> None:
    """审核者事后改判：最后一条标记说了算。"""
    bodies = [
        _body(_marker(sha=SHA_A, verdict="passed")),
        _body(_marker(sha=SHA_A, verdict="changes-requested"),
              "发现一处竞态，须改。"),
    ]
    passed, reason = review_gate_passes(bodies, 123, SHA_A)
    assert passed is False
    assert "changes-requested" in reason


def test_later_blocked_overrides_earlier_passed() -> None:
    bodies = [
        _body(_marker(sha=SHA_A, verdict="passed")),
        _body(_marker(sha=SHA_A, verdict="blocked"), "涉及生产库，须用户定。"),
    ]
    passed, _ = review_gate_passes(bodies, 123, SHA_A)
    assert passed is False


def test_repassed_after_fix_on_new_head_passes() -> None:
    """改完推新提交、重新审核通过：放行。"""
    bodies = [
        _body(_marker(sha=SHA_A, verdict="passed")),
        _body(_marker(sha=SHA_B, verdict="changes-requested"), "须改。"),
        _body(_marker(sha=SHA_C, verdict="passed"), "已改好，重新审过。"),
    ]
    passed, _ = review_gate_passes(bodies, 123, SHA_C)
    assert passed is True


def test_marker_for_another_pr_does_not_count() -> None:
    passed, reason = review_gate_passes([_body(_marker(pr=999))], 123, SHA_A)
    assert passed is False
    assert "PR #999" in reason


def test_reviewer_name_variants_parse() -> None:
    for reviewer in ("muse-1", "muse-4", "lead-agent", "muse_2"):
        marker = _marker(reviewer=reviewer)
        assert parse_review_markers([marker])[0]["reviewer"] == reviewer


# ------------------------------------------------------- 复审发现 1：verdict 边界
def test_verdict_with_suffix_is_not_parsed_as_passed() -> None:
    """`verdict=passedXYZ` / `passed.` / `passed-foo` 绝不能当成 passed 放行。

    verdict 后只允许空白、`--`（标记结尾）或串尾。
    """
    base = f"<!-- review-result: pr-123 sha-{SHA_A} reviewer-muse-1 verdict="
    for bad in ("passedXYZ -->", "passed. -->", "passed-foo -->",
                "passedx -->", "changes-requestedx -->"):
        assert parse_review_markers([base + bad]) == []
        passed, _ = review_gate_passes([base + bad], 123, SHA_A)
        assert passed is False
    # 合法收尾仍能解析：带空格的标记结尾、无空格的标记结尾
    for good in ("passed -->", "passed-->", "blocked -->"):
        assert len(parse_review_markers([base + good])) == 1


def test_format_marker_rejects_bad_pr_number() -> None:
    with pytest.raises(ValueError):
        format_review_marker(0, SHA_A, "muse-1", "passed")
    with pytest.raises(ValueError):
        format_review_marker(-3, SHA_A, "muse-1", "passed")


# ------------------------------------------- 复审发现 3/4：标记位置与同一评论多条
def test_marker_mid_body_is_accepted() -> None:
    """标记不必在首行（与 smoke-result 的“取最后一条”口径一致）。"""
    body = "先说两句人话。\n\n" + _marker() + "\n\n结论如上。"
    assert len(parse_review_markers([body])) == 1


def test_last_marker_in_same_comment_wins() -> None:
    """同一评论先贴 passed、后贴 blocked：blocked 说了算（安全方向）。"""
    body = (
        _marker(sha=SHA_A, verdict="passed")
        + "\n\n改判：\n\n"
        + _marker(sha=SHA_A, verdict="blocked")
    )
    markers = parse_review_markers([body])
    assert len(markers) == 1
    assert markers[0]["verdict"] == "blocked"
    passed, _ = review_gate_passes([body], 123, SHA_A)
    assert passed is False


# ------------------------------------------------------- 复审发现 6：分页拉取
def test_fetch_comment_bodies_paginates(monkeypatch) -> None:
    """两页评论（第 1 页满 100 条）都要拉到，顺序即时间顺序。"""
    import io
    import json as _json
    import urllib.parse
    import urllib.request as _request

    from dispatch import review_gate

    page1 = [{"body": f"第 1 页第 {i} 条"} for i in range(100)]
    page2 = [{"body": "第 2 页唯一一条"}]

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return _json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen_urls: list[str] = []

    def _fake_urlopen(request, timeout=30):
        # 必须按页码精确匹配：URL 里 `per_page=100` 本身就含有子串
        # `page=1`，用 `"page=1" in url` 判断会在每一页都返回第 1 页，
        # 循环永不终止（复审抓到的死循环 bug，三次 OOM 正是它造成的）。
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(request.full_url).query
        )
        seen_urls.append(request.full_url)
        if query.get("page") == ["1"]:
            return _FakeResponse(page1)
        return _FakeResponse(page2)

    monkeypatch.setattr(_request, "urlopen", _fake_urlopen)
    bodies = review_gate.fetch_comment_bodies("Jouwala/repo", 123, "tok")
    assert len(bodies) == 101
    assert bodies[0] == "第 1 页第 0 条"
    assert bodies[-1] == "第 2 页唯一一条"
    assert any("page=2" in url for url in seen_urls)


def test_fetch_comment_bodies_stops_after_100_pages(monkeypatch) -> None:
    """服务端永远返回满页时抛错（fail-closed），不无限循环。"""
    import json as _json
    import urllib.request as _request

    from dispatch import review_gate

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return _json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    full_page = [{"body": "永远满页"} for _ in range(100)]
    calls = {"n": 0}

    def _always_full(request, timeout=30):
        calls["n"] += 1
        return _FakeResponse(full_page)

    monkeypatch.setattr(_request, "urlopen", _always_full)
    with pytest.raises(RuntimeError, match="超过 100 页"):
        review_gate.fetch_comment_bodies("Jouwala/repo", 123, "tok")
    assert calls["n"] == 100


# ------------------------------------------- 低风险免审（2026-11-24 流程提速批）
from dispatch.review_gate import (  # noqa: E402 - 本节新增，放在文件尾部便于阅读
    WAIVER_MAX_FILES,
    WAIVER_MAX_LINES,
    is_protected_path,
    waiver_decision,
)


def test_waiver_allows_small_business_change() -> None:
    ok, why = waiver_decision(
        ["apps/my_app/backend/services/prtg/mapping.py", "apps/portal/src/pages/Location.tsx"],
        additions=40,
        deletions=8,
        labels=["agent:muse-2"],
    )
    assert ok is True
    assert "低风险" in why


def test_waiver_blocks_risk_high() -> None:
    ok, why = waiver_decision(["apps/portal/src/pages/Location.tsx"], 5, 1, ["risk:high"])
    assert ok is False
    assert "risk:high" in why


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/quality.yml",
        ".githooks/pre-commit",
        "ops/deploy/smoke-watcher.ps1",
        "apps/control-plane/backend/dispatch/review_gate.py",
        "apps/agent-worker/worker.py",
        "apps/my_app/alembic/versions/0009_x.py",
        "apps/my_app/backend/main.py",
        "apps/my_app/backend/services/auth_service.py",
        "apps/portal/package-lock.json",
        "apps/my_app/requirements.txt",
        "apps/my_app/frontend/.env.local",
        "AGENTS.md",
        "docs/workspace/agent-protocol.md",
    ],
)
def test_waiver_blocks_protected_paths(path: str) -> None:
    ok, why = waiver_decision([path], 1, 0, [])
    assert ok is False
    assert "受保护路径" in why


def test_waiver_blocks_too_many_files() -> None:
    paths = [f"apps/my_app/frontend/src/pages/Page{i}.tsx" for i in range(WAIVER_MAX_FILES + 1)]
    ok, why = waiver_decision(paths, 10, 10, [])
    assert ok is False
    assert "文件" in why


def test_waiver_blocks_too_many_lines() -> None:
    ok, why = waiver_decision(
        ["apps/my_app/backend/services/prtg/mapping.py"],
        additions=WAIVER_MAX_LINES,
        deletions=1,
        labels=[],
    )
    assert ok is False
    assert "行" in why


def test_waiver_requires_paths() -> None:
    ok, why = waiver_decision([], 0, 0, [])
    assert ok is False
    assert "取不到" in why


def test_is_protected_path_normalizes_separators() -> None:
    assert is_protected_path(".github\\workflows\\quality.yml") is True
    assert is_protected_path("./apps/my_app/backend/services/prtg/mapping.py") is False
    assert is_protected_path("") is True


def test_fetch_pr_risk_returns_none_on_error(monkeypatch) -> None:
    import urllib.request as _request

    from dispatch import review_gate

    def _boom(request, timeout=30):
        raise OSError("network down")

    monkeypatch.setattr(_request, "urlopen", _boom)
    assert review_gate.fetch_pr_risk("Jouwala/repo", 1, "tok") is None


def test_cli_waives_low_risk_without_review_marker(monkeypatch, capsys) -> None:
    """低风险 PR：不读审核评论也放行，并打印 REVIEW_TIER=waived。"""
    from dispatch import review_gate

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(
        review_gate,
        "fetch_pr_risk",
        lambda repo, pr, token: {
            "paths": ["apps/portal/src/pages/Location.tsx"],
            "additions": 3,
            "deletions": 1,
            "labels": [],
        },
    )

    def _must_not_fetch(repo, pr, token):
        raise AssertionError("低风险免审不应读审核评论")

    monkeypatch.setattr(review_gate, "fetch_comment_bodies", _must_not_fetch)
    code = review_gate.main(["--repo", "Jouwala/repo", "--pr", "1", "--head-sha", SHA_A])
    out = capsys.readouterr().out
    assert code == 0
    assert f"REVIEW_TIER=waived sha={SHA_A}" in out


def test_cli_strict_still_requires_review(monkeypatch, capsys) -> None:
    """--strict 回到旧口径：低风险也必须有一致 head 的审核记录。"""
    from dispatch import review_gate

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(
        review_gate,
        "fetch_pr_risk",
        lambda repo, pr, token: {
            "paths": ["apps/portal/src/pages/Location.tsx"],
            "additions": 3,
            "deletions": 1,
            "labels": [],
        },
    )
    monkeypatch.setattr(review_gate, "fetch_comment_bodies", lambda repo, pr, token: [])
    code = review_gate.main(
        ["--repo", "Jouwala/repo", "--pr", "1", "--head-sha", SHA_A, "--strict"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "不通过" in out
