"""双门禁接线（#132）的静态契约测试：auto-merge 调审核门禁、PR 开启通知。

只读 workflow 文本做断言（不用 PyYAML，避免 CI 测试环境多一个依赖）。
守护的是 #127 / #136 两次竞态的教训：审核门禁必须真实接进合并判定，
而不是只写在文档里。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
AUTO_MERGE = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"
NOTIFY = REPO_ROOT / ".github" / "workflows" / "pr-opened-notify.yml"
LABELS = REPO_ROOT / ".github" / "labels.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------- auto-merge 双门禁
def test_auto_merge_calls_review_gate_after_smoke_binding() -> None:
    """smoke SHA 绑定通过后，必须调 review_gate.py 判定才继续。"""
    text = _read(AUTO_MERGE)
    assert "review_gate.py" in text
    assert "--repo" in text and "--pr" in text and "--head-sha" in text
    smoke_pos = text.index("smoke-passed 已绑定当前 head")
    # 以实际调用行为准（不是头注释 / checkout 步骤里提到的文件名）。
    # 2026-11-24 起调用结果存进 GATE_OUT，供低风险免审分支落审计评论。
    gate_pos = text.index(
        'GATE_OUT="$(python3 apps/control-plane/backend/dispatch/review_gate.py'
    )
    merge_pos = text.index("条件满足，合并 PR")
    assert smoke_pos < gate_pos < merge_pos


def test_auto_merge_review_gate_fails_closed() -> None:
    """门禁脚本返回非零就跳过（不合入），不是只记日志继续走。"""
    text = _read(AUTO_MERGE)
    assert (
        'if ! GATE_OUT="$(python3 apps/control-plane/backend/dispatch/review_gate.py'
        in text
    )
    assert "独立审核门禁未通过，跳过" in text


def test_auto_merge_waiver_path_is_wired_and_audited() -> None:
    """低风险免审必须接线：读到 REVIEW_TIER=waived 才写审计评论，不是静默放行。"""
    text = _read(AUTO_MERGE)
    assert "REVIEW_TIER=waived" in text
    assert "review-waived: pr-" in text
    # 免审评论同样绑定 head SHA，事后可复核是哪个 head 免的审
    assert "sha-$HEAD_SHA" in text


def test_auto_merge_checks_out_main_for_gate_script() -> None:
    """门禁脚本取 main 检出的可信版本，不取 PR 分支的——PR 作者不得自带门禁脚本。"""
    text = _read(AUTO_MERGE)
    assert "actions/checkout@v4" in text
    checkout_pos = text.index("actions/checkout@v4")
    assert "ref: main" in text[checkout_pos:checkout_pos + 400]
    assert "review_gate.py" in text[checkout_pos:checkout_pos + 400]


def test_auto_merge_keeps_smoke_gate_and_do_not_merge_veto() -> None:
    """加双门禁不得把原有门禁改没：smoke 绑定与 do-not-merge 一票否决都在。"""
    text = _read(AUTO_MERGE)
    assert "smoke-result: pr-" in text
    assert "do-not-merge" in text
    assert "smoke-passed" in text


# ------------------------------------------------------- PR 开启通知
def test_pr_opened_notify_triggers_on_opened_and_labels_needs_review() -> None:
    text = _read(NOTIFY)
    assert "pull_request" in text
    assert "opened" in text
    assert "needs-review" in text
    assert "/labels" in text


def test_pr_opened_notify_fails_loudly_if_label_missing() -> None:
    """标签缺失时作业要变红（--fail），不是静默跳过——没标签就等于没通知。

    断言锚在 curl 调用行：文件头注释里也出现 --fail，笼统断言会空过。
    """
    text = _read(NOTIFY)
    assert "curl -fsS" in text


def test_needs_review_label_defined_in_labels_yml() -> None:
    """标签先在 labels.yml 定义，再由工作流使用（仓库约定：不自造新标签）。"""
    text = _read(LABELS)
    assert "needs-review" in text
