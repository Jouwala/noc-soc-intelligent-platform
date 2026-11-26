"""CLI 集成：`--claim-probe` 单行 JSON / `--check` / `verify_concurrent_claim.py`。"""

from __future__ import annotations

import json
import subprocess
import sys

from _linux_fixtures import fake_github  # noqa: F401 - pytest 夹具
from _linux_testkit import PACKAGE_DIR, REPO_ROOT, config_mapping, make_body, write_config

WORKER_CLI = PACKAGE_DIR / "worker_linux.py"
VERIFY_CLI = PACKAGE_DIR / "verify_concurrent_claim.py"
REQUIRED_FIELDS = (
    "ok",
    "result",
    "worker_id",
    "instance",
    "attempt",
    "token",
    "hwm_refs",
    "lease_refs",
    "refs_error",
)


def _run(args, timeout=180):
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(PACKAGE_DIR),
    )


def _write_config(tmp_path, server, worker_id="muse-1-linux", instance=None):
    raw = config_mapping(server, worker_id, instance, tmp_path)
    return write_config(tmp_path / f"config-{worker_id}.json", raw)


def _add_issue(server, number=1, *, agent="muse-1-linux"):
    server.add_issue(
        number,
        labels=(f"agent:{agent}", "state:todo"),
        title=f"任务 {number}",
        body=make_body(command=["true"]),
    )


def _refs(server, prefix):
    return [ref for ref in server.refs_snapshot() if ref.startswith(prefix)]


def test_claim_probe_cli_outputs_single_line_json_with_refs(fake_github, tmp_path):
    _add_issue(fake_github)
    config_path = _write_config(tmp_path, fake_github)
    result_file = tmp_path / "probe.json"

    proc = _run(
        [
            str(WORKER_CLI),
            "--config",
            str(config_path),
            "--claim-probe",
            "--issue",
            "1",
            "--result-file",
            str(result_file),
        ]
    )
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"stdout 必须是单行 JSON：{proc.stdout!r}"
    payload = json.loads(lines[0])
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    assert not missing, missing
    assert payload["result"] == "claimed"
    assert payload["token"] == 1
    assert payload["refs_error"] == ""
    assert payload["hwm_refs"] and payload["lease_refs"]
    assert payload["config_path"] == str(config_path)
    assert result_file.read_text(encoding="utf-8").strip() == lines[0]

    assert len(_refs(fake_github, "refs/heads/agent-hwm/issue-1/")) == 1
    assert len(_refs(fake_github, "refs/heads/agent-lease/issue-1/")) == 1
    # 配置里的 token 值不得出现在任何输出里
    assert "test-token" not in proc.stdout + proc.stderr


def test_claim_probe_second_process_reports_lease_held(fake_github, tmp_path):
    _add_issue(fake_github)
    config_a = _write_config(tmp_path, fake_github, "muse-1-linux")
    config_b = _write_config(tmp_path, fake_github, "muse-2-linux")

    first = _run([str(WORKER_CLI), "--config", str(config_a), "--claim-probe", "--issue", "1"])
    second = _run([str(WORKER_CLI), "--config", str(config_b), "--claim-probe", "--issue", "1"])
    assert first.returncode == 0 and second.returncode == 0
    first_payload = json.loads([line for line in first.stdout.splitlines() if line.strip()][-1])
    second_payload = json.loads([line for line in second.stdout.splitlines() if line.strip()][-1])
    assert first_payload["result"] == "claimed"
    assert second_payload["result"] == "LeaseHeld"
    assert _refs(fake_github, "refs/heads/agent-lease/issue-1/")
    assert len(_refs(fake_github, "refs/heads/agent-hwm/issue-1/")) == 1


def test_check_cli_reports_identity_and_refs(fake_github, tmp_path):
    config_path = _write_config(tmp_path, fake_github)
    proc = _run([str(WORKER_CLI), "--config", str(config_path), "--check"])
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stdout
    payload = json.loads(lines[0])
    assert payload["ok"] is True and payload["refs_ok"] is True
    assert payload["main_sha"] == fake_github.anchor_sha
    assert payload["identity"]["worker_id"] == "muse-1-linux"
    assert payload["identity"]["instance"] == "muse-1-linux"
    assert payload["auth_mode"] == "config"
    assert payload["hostname"]
    assert "test-token" not in proc.stdout + proc.stderr


def test_check_cli_rejects_invalid_config_with_exit_2(fake_github, tmp_path):
    raw = config_mapping(fake_github, "muse-1-linux", None, tmp_path)
    raw["repo_root"] = str(tmp_path / "not-a-repo")
    config_path = write_config(tmp_path / "bad.json", raw)
    proc = _run([str(WORKER_CLI), "--config", str(config_path), "--check"])
    assert proc.returncode == 2
    assert "config-error" in proc.stderr


def test_verify_concurrent_claim_tool_and_cleanup(fake_github, tmp_path):
    _add_issue(fake_github)
    config_a = _write_config(tmp_path, fake_github, "muse-1-linux")
    config_b = _write_config(tmp_path, fake_github, "muse-2-linux")
    winner_file = tmp_path / "winner.json"

    proc = _run(
        [
            str(VERIFY_CLI),
            "--issue",
            "1",
            "--config-a",
            str(config_a),
            "--config-b",
            str(config_b),
            "--winner-file",
            str(winner_file),
        ]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout
    assert "agent-hwm/issue-1" in proc.stdout
    assert "LeaseHeld" in proc.stdout
    assert winner_file.is_file()
    winner = json.loads(winner_file.read_text(encoding="utf-8"))
    assert winner["result"] == "claimed"
    assert _refs(fake_github, "refs/heads/agent-lease/issue-1/")

    cleanup = _run([str(VERIFY_CLI), "--cleanup-winner", str(winner_file)])
    assert cleanup.returncode == 0, cleanup.stdout + cleanup.stderr
    assert _refs(fake_github, "refs/heads/agent-lease/issue-1/") == []
    # HWM 是只增不减的代次序列，这里不断言「恰好 1 条」：并发交错下，失败者可能
    # 在赢家「HWM 已建、lease 未建」的窗口里读到代次并推进到 token 2（ADR-7 §3.3
    # 窗口；control-plane 的 test_ref_concurrency 有确定性复现用例），那条 ref
    # 合法存在、cleanup 也只删 lease 不删 HWM。原断言 ==1 只在失败者恰好先读到
    # 旧代次时成立，是 CI 偶发 assert 2 == 1 的根因。协议真不变量是：token 恰为
    # 1..N 连续序列（每次铸造都是当前 max+1），且赢家的 token 在其中。
    hwm_refs = _refs(fake_github, "refs/heads/agent-hwm/issue-1/")
    tokens = sorted(int(ref.rsplit("/", 1)[1]) for ref in hwm_refs)
    assert tokens == list(range(1, len(tokens) + 1)), hwm_refs
    assert int(winner["token"]) in tokens


def test_worker_module_imports_without_repo_side_effects():
    """避免测试误把仓库根目录当状态目录：包内模块导入后不应写文件。"""
    proc = _run(["-c", "import worker_linux, linux_worker_core, linux_config; print('ok')"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
    assert not (REPO_ROOT / ".heartbeat-muse-1-linux.json").exists()
